"""Real internal SMS status API with committed independent-connection fixtures."""

from dataclasses import replace
from typing import cast

import pytest
from fastapi import FastAPI, Request

from app.routes.dependencies.view_as import apply_view_as
from app.services import visit_prep_access, visit_prep_sms_outbox

from tests.integration.visit_prep_sms_outbox_test import (
    _finish_queue_only_fixtures,  # noqa: F401 -- owned fixture cleanup only
    _ready,
)
from tests.integration.visit_prep_sms_status_test import _audits
from tests.routes.visit_prep_question_sets_test import _provider_client


def test_closed_sms_status_read_with_sends_disabled(monkeypatch):
    fixture, options, _ = _ready(monkeypatch)
    visit_prep_sms_outbox.enqueue_sms(**options)
    monkeypatch.setattr(
        visit_prep_access,
        "VISIT_PREP_SETTINGS",
        replace(options["settings"], public_api_enabled=False, sms_send_enabled=False),
    )
    with _provider_client(fixture.doctor, monkeypatch) as client:
        response = client.get(
            f"/internal/v1/visit-prep/preps/{fixture.prep_id}/sms-status"
        )
    assert response.status_code == 200
    value = response.json()
    assert set(value) == {
        "contract",
        "preparation_id",
        "current_credential_generation",
        "messages",
        "checked_at",
        "dispatch_integration",
    }
    assert (
        value["contract"] == "visit_prep.sms_status.v1"
        and value["dispatch_integration"] == "not_connected"
    )
    assert [m["status"] for m in value["messages"]] == [
        "queued",
        "not_requested",
        "not_requested",
    ]
    assert set(value["messages"][0]) == {
        "step",
        "status",
        "outcome_code",
        "credential_generation",
        "context_current",
        "invitation_expired",
        "submission_window_elapsed",
        "requested_at",
        "expires_at",
        "submitted_at",
        "finalized_at",
    }
    assert all(
        s not in response.text
        for s in (
            fixture.access_token,
            "+12025550123",
            "José",
            "ciphertext",
            "operation_id",
            "consent",
            "sender_digest",
        )
    )
    assert (
        response.headers["cache-control"] == "no-store"
        and response.headers["referrer-policy"] == "no-referrer"
    )
    assert _audits(fixture) == 1


@pytest.mark.parametrize(
    "change",
    ["support", "baa", "other_owner", "path", "unknown_query", "duplicate_query"],
)
def test_denied_read_never_echoes_private_input(monkeypatch, change):
    fixture, options, _ = _ready(monkeypatch)
    identifier, query = str(fixture.prep_id), ""
    if change == "path":
        identifier = "private-invalid-id"
    elif change == "other_owner":
        identifier = str(_ready(monkeypatch)[0].prep_id)
    elif change == "baa":
        fixture.practice.negotiated_baa_on_file = False
        fixture.practice.save()
    elif change in {"unknown_query", "duplicate_query"}:
        query = (
            "?private=value"
            if change == "unknown_query"
            else "?private=value&private=value"
        )
    with _provider_client(fixture.doctor, monkeypatch) as client:
        if change == "support":

            def support(request: Request):
                request.state.view_as = object()

            cast(FastAPI, client.app).dependency_overrides[apply_view_as] = support
        response = client.get(
            f"/internal/v1/visit-prep/preps/{identifier}/sms-status{query}"
        )
    assert response.status_code in {404, 422}
    assert response.json()["detail"] in {"Not found", "Visit Prep request is invalid."}
    assert (
        "private" not in response.text
        and response.headers["cache-control"] == "no-store"
    )
    assert _audits(fixture) == 0


def test_sms_status_is_get_only_with_stable_internal_operation():
    from app.routes.internal import internal_api_app
    from app.utils.openapi import generate_openapi_schema

    operation = generate_openapi_schema(internal_api_app)["paths"][
        "/internal/v1/visit-prep/preps/{prep_id}/sms-status"
    ]
    assert (
        set(operation) == {"get"}
        and operation["get"]["operationId"] == "visit_prep_sms_status"
    )
