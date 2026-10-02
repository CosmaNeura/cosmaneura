"""Real authenticated status API is closed, audited and read-only."""

from dataclasses import replace
from typing import cast

import pytest
from fastapi import FastAPI, Request

from app.routes.dependencies.view_as import apply_view_as
from app.services import visit_prep_access

from tests.routes.visit_prep_question_sets_test import _provider_client
from tests.services.visit_prep_email_status_test import _audits
from tests.services.visit_prep_email_worker_test import _queued


def test_status_http_has_closed_contract_no_private_content_and_no_channel_gate(
    monkeypatch,
):
    fixture = _queued()
    monkeypatch.setattr(
        visit_prep_access,
        "VISIT_PREP_SETTINGS",
        replace(
            fixture[3]["settings"], public_api_enabled=False, email_send_enabled=False
        ),
    )
    with _provider_client(fixture[0].doctor, monkeypatch) as client:
        response = client.get(
            f"/internal/v1/visit-prep/preps/{fixture[1].id}/email-status"
        )
    assert response.status_code == 200
    value = response.json()
    assert (
        value["contract"] == "visit_prep.email_status.v1"
        and value["status"] == "queued"
    )
    assert value["dispatch_integration"] == "default_off_worker"
    assert set(value) == {
        "contract",
        "preparation_id",
        "status",
        "outcome_code",
        "current_credential_generation",
        "credential_generation",
        "context_current",
        "invitation_expired",
        "submission_window_elapsed",
        "requested_at",
        "expires_at",
        "submitted_at",
        "finalized_at",
        "checked_at",
        "dispatch_integration",
    }
    for private in (
        fixture[3]["access_token"],
        "patient@example.com",
        "ciphertext",
        "approval",
        "consent",
        "delivery_id",
        "operation_id",
    ):
        assert private not in response.text
    assert (
        response.headers["cache-control"] == "no-store"
        and response.headers["referrer-policy"] == "no-referrer"
    )
    assert len(_audits(fixture)) == 1


@pytest.mark.parametrize(
    "change",
    ["support", "baa", "other_owner", "path", "unknown_query", "duplicate_query"],
)
def test_denied_status_never_echoes_private_input(monkeypatch, change):
    fixture = _queued()
    monkeypatch.setattr(
        visit_prep_access, "VISIT_PREP_SETTINGS", fixture[3]["settings"]
    )
    identifier = str(fixture[1].id)
    query = ""
    if change == "path":
        identifier = "private-invalid-id"
    elif change == "other_owner":
        identifier = str(_queued("other-status-http")[1].id)
    elif change == "baa":
        fixture[0].practice.negotiated_baa_on_file = False
        fixture[0].practice.save()
    elif change in {"unknown_query", "duplicate_query"}:
        query = (
            "?private=value"
            if change == "unknown_query"
            else "?private=value&private=value"
        )
    with _provider_client(fixture[0].doctor, monkeypatch) as client:
        if change == "support":

            def support(request: Request):
                request.state.view_as = object()

            cast(FastAPI, client.app).dependency_overrides[apply_view_as] = support
        response = client.get(
            f"/internal/v1/visit-prep/preps/{identifier}/email-status{query}"
        )
    assert response.status_code in {404, 422}
    assert response.json()["detail"] in {"Not found", "Visit Prep request is invalid."}
    assert (
        "private" not in response.text
        and fixture[3]["access_token"] not in response.text
    )
    assert response.headers["cache-control"] == "no-store"
    assert _audits(fixture) == []


def test_status_operation_is_internal_and_has_no_mutation_methods():
    from app.routes.internal import internal_api_app
    from app.utils.openapi import generate_openapi_schema

    paths = generate_openapi_schema(internal_api_app)["paths"]
    operation = paths["/internal/v1/visit-prep/preps/{prep_id}/email-status"]
    assert (
        set(operation) == {"get"}
        and operation["get"]["operationId"] == "visit_prep_email_status"
    )
