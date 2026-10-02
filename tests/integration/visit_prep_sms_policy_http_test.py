"""Actual owner-only SMS policy API; no patient records or live messaging."""

from dataclasses import replace
from typing import cast
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request

from app.configuration import visit_prep_sms
from app.configuration.visit_prep_sms import VisitPrepSMSRouting
from app.routes.dependencies.view_as import apply_view_as
from app.routes.internal import internal_api_app
from app.services import visit_prep_access
from app.services import visit_prep_sms_policy_view as view
from app.utils.openapi import generate_openapi_schema

from activemodel.session_manager import get_engine
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from tests.integration.visit_prep_sms_policy_test import (
    _audits,
    _intent,
    _policy_ready,
    _rows,
)
from tests.routes.visit_prep_question_sets_test import _provider_client
from tests.services.visit_preps_test import _withdraw_seat

PATH = "/internal/v1/visit-prep/sms-policy"


def _ready(monkeypatch):
    fixture, settings, routing = _policy_ready(monkeypatch)
    monkeypatch.setattr(visit_prep_access, "VISIT_PREP_SETTINGS", settings)
    return fixture, settings, routing


def _payload(fixture, settings, routing, **changes):
    intent = _intent(fixture, settings, routing)
    return {
        "expected_event_sequence": 0,
        "event_kind": "approved",
        "policy_fingerprint": intent["policy_fingerprint"],
        "expected_sender_digest": intent["sender_digest"],
        "expires_at": intent["expires_at"].isoformat(),
    } | changes


def _post(client, payload, key=None):
    return client.post(
        PATH, json=payload, headers={"Idempotency-Key": key or str(uuid4())}
    )


def _private(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert all(
        value not in response.text
        for value in (
            "ciphertext",
            "vp1_",
            "example.invalid",
            "+12025550100",
            "clerk_id",
            "private_caller",
        )
    )


def test_actual_policy_review_approval_withdrawal_and_old_retry_never_send(monkeypatch):
    fixture, settings, routing = _ready(monkeypatch)
    payload, key = _payload(fixture, settings, routing), str(uuid4())
    with _provider_client(fixture.doctor, monkeypatch) as client:
        initial = client.get(PATH)
        assert initial.status_code == 200
        body = initial.json()
        assert body["contract"] == "visit_prep.sms_policy.v1"
        assert body["delivery_status"] == "not_connected"
        assert body["status"] == "not_approved" and body["event_sequence"] == 0
        assert (
            body["decision_sender_digest"]
            is body["decision_policy_fingerprint"]
            is body["recorded_at"]
            is body["expires_at"]
            is None
        )
        assert set(body["templates"]) == {
            "booking_sms",
            "unopened_48h_sms",
            "unfinished_12h_sms",
        }
        assert all(
            "{private_preparation_link}" in value and "Reply STOP" in value
            for value in body["templates"].values()
        )
        assert body["standard_cadence"]["provider_editable"] is False
        assert body["standard_cadence"]["quiet_start_hour"] == 21
        assert body["standard_cadence"]["quiet_end_hour"] == 8
        assert body["standard_cadence"]["minimum_channel_spacing_hours"] == 4
        approved = _post(client, payload, key)
        assert (
            approved.status_code == 200 and approved.json()["disposition"] == "recorded"
        )
        current = client.get(PATH)
        assert current.json()["status"] == "approved"
        withdrawal = _post(
            client,
            payload
            | {
                "expected_event_sequence": 1,
                "event_kind": "withdrawn",
                "expires_at": None,
            },
        )
        assert withdrawal.status_code == 200
        retry = _post(client, payload, key)
        assert (
            retry.json()["disposition"] == "replayed"
            and retry.json()["current_event_sequence"] == 2
        )
        final = client.get(PATH)
        assert final.json()["status"] == "withdrawn"
    for response in (initial, approved, current, withdrawal, retry, final):
        _private(response)
    assert len(_rows(fixture)) == len(_audits(fixture)) == 2
    with Session(get_engine()) as session:
        for table in ("visit_prep_sms_consent_event", "visit_prep_sms_delivery"):
            assert (
                session.execute(
                    text(
                        f"SELECT count(*) FROM {table} WHERE owner_practice_member_id=:owner"
                    ),
                    {"owner": fixture.owner.id.uuid},
                ).scalar_one()
                == 0
            )
        assert (
            session.execute(
                text(
                    "SELECT count(*) FROM authority_audit_event WHERE resource_ref=:owner AND event_type='visit_prep.sms_policy_read'"
                ),
                {"owner": str(fixture.owner.id)},
            ).scalar_one()
            == 3
        )
    assert settings.sms_send_enabled is False and settings.public_api_enabled is False


def test_changed_sender_is_not_approved_by_stale_tab_but_old_decision_can_be_withdrawn(
    monkeypatch,
):
    fixture, settings, routing = _ready(monkeypatch)
    payload, key = _payload(fixture, settings, routing), str(uuid4())
    with _provider_client(fixture.doctor, monkeypatch) as client:
        assert _post(client, payload, key).status_code == 200
        monkeypatch.setattr(
            visit_prep_sms, "ROUTING", replace(routing, sender_number="+12025550101")
        )
        changed = client.get(PATH)
        assert changed.json()["status"] == "sender_changed"
        assert (
            _post(client, payload | {"expected_event_sequence": 1}).status_code == 409
        )
        assert _post(client, payload, key).json()["disposition"] == "replayed"
        monkeypatch.setattr(visit_prep_sms, "ROUTING", VisitPrepSMSRouting())
        assert client.get(PATH).json()["sender_digest"] is None
        withdrawn = _post(
            client,
            payload
            | {
                "expected_event_sequence": 1,
                "event_kind": "withdrawn",
                "expires_at": None,
            },
        )
        assert (
            withdrawn.status_code == 200
            and client.get(PATH).json()["status"] == "withdrawn"
        )


def test_unset_sender_and_timing_are_honest_without_recording_or_granting(monkeypatch):
    import app.configuration.visit_prep_cadence as cadence

    fixture, settings, routing = _ready(monkeypatch)
    monkeypatch.setattr(visit_prep_sms, "ROUTING", VisitPrepSMSRouting())
    monkeypatch.setattr(cadence, "VISIT_PREP_CADENCE_SETTINGS", None)
    with _provider_client(fixture.doctor, monkeypatch) as client:
        response = client.get(PATH)
        assert response.status_code == 200
        assert (
            response.json()["sender_digest"]
            is response.json()["standard_cadence"]
            is None
        )
        assert _post(client, _payload(fixture, settings, routing)).status_code == 409
    _private(response)
    assert _rows(fixture) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"owner_doctor_id": "private_caller"},
        {"sender_number": "+12025550100"},
        {"standard_cadence": {"quiet_start_hour": 0}},
        {"expected_event_sequence": True},
        {"expected_event_sequence": -1},
        {"expected_sender_digest": "A" * 64},
        {"expires_at": "2099-01-01T12:00:00"},
        {"expires_at": 1900000000},
    ],
)
def test_invalid_payload_is_closed_and_redacted(monkeypatch, changes):
    fixture, settings, routing = _ready(monkeypatch)
    with _provider_client(fixture.doctor, monkeypatch) as client:
        response = _post(client, _payload(fixture, settings, routing) | changes)
    assert response.status_code == 422
    _private(response)
    assert _rows(fixture) == []


@pytest.mark.parametrize("change", ["disabled", "cohort", "baa", "seat", "support"])
def test_current_authority_denies_read_and_decision(monkeypatch, change):
    fixture, settings, routing = _ready(monkeypatch)
    if change in {"disabled", "cohort"}:
        monkeypatch.setattr(
            visit_prep_access,
            "VISIT_PREP_SETTINGS",
            replace(
                settings,
                **(
                    {"enabled": False}
                    if change == "disabled"
                    else {"enabled_doctor_ids": frozenset()}
                ),
            ),
        )
    elif change == "baa":
        with Session(get_engine()) as session:
            session.execute(
                text("UPDATE practice SET negotiated_baa_on_file=false WHERE id=:id"),
                {"id": fixture.practice.id.uuid},
            )
            session.commit()
    elif change == "seat":
        _withdraw_seat(fixture.owner)
    with _provider_client(fixture.doctor, monkeypatch) as client:
        if change == "support":

            def support(request: Request):
                request.state.view_as = object()

            cast(FastAPI, client.app).dependency_overrides[apply_view_as] = support
        responses = [
            client.get(PATH),
            _post(client, _payload(fixture, settings, routing)),
        ]
    for response in responses:
        assert response.status_code in {404, 409}
        _private(response)
    assert _rows(fixture) == []


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [("Idempotency-Key", "private_caller")],
        [("Idempotency-Key", str(uuid4())), ("Idempotency-Key", str(uuid4()))],
    ],
)
def test_requires_one_valid_operation_header(monkeypatch, headers):
    fixture, settings, routing = _ready(monkeypatch)
    with _provider_client(fixture.doctor, monkeypatch) as client:
        response = client.post(
            PATH, json=_payload(fixture, settings, routing), headers=headers
        )
    assert response.status_code == 422
    _private(response)
    assert _rows(fixture) == []


def test_unknown_query_and_streamed_oversize_are_redacted(monkeypatch):
    fixture, settings, routing = _ready(monkeypatch)
    with _provider_client(fixture.doctor, monkeypatch) as client:
        query = client.get(PATH + "?owner=private_caller")
        oversized = client.post(
            PATH,
            content=iter([b" " * 700, b" " * 700]),
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": str(uuid4()),
            },
        )
    assert query.status_code == 422 and oversized.status_code == 413
    _private(query)
    _private(oversized)
    assert _rows(fixture) == []


def test_committed_but_lost_ack_retries_original_operation_once(monkeypatch):
    fixture, settings, routing = _ready(monkeypatch)
    payload, key, original = (
        _payload(fixture, settings, routing),
        str(uuid4()),
        Session.commit,
    )
    with _provider_client(fixture.doctor, monkeypatch) as client:
        with monkeypatch.context() as patch:

            def lose_ack(session):
                original(session)
                raise SQLAlchemyError("Synthetic private lost acknowledgment")

            patch.setattr(Session, "commit", lose_ack)
            response = _post(client, payload, key)
            assert response.status_code == 409
        retry = _post(client, payload, key)
    assert retry.status_code == 200 and retry.json()["disposition"] == "replayed"
    assert len(_rows(fixture)) == len(_audits(fixture)) == 1


def test_read_audit_failure_does_not_disclose_a_policy_snapshot(monkeypatch):
    fixture, _, _ = _ready(monkeypatch)
    monkeypatch.setattr(
        view,
        "_templates",
        lambda: (_ for _ in ()).throw(ValueError("Synthetic private failure")),
    )
    with _provider_client(fixture.doctor, monkeypatch) as client:
        response = client.get(PATH)
    assert response.status_code == 409
    _private(response)
    with Session(get_engine()) as session:
        assert (
            session.execute(
                text(
                    "SELECT count(*) FROM authority_audit_event WHERE resource_ref=:owner AND event_type='visit_prep.sms_policy_read'"
                ),
                {"owner": str(fixture.owner.id)},
            ).scalar_one()
            == 0
        )


def test_actual_internal_openapi_keeps_the_owner_contract_closed_and_named():
    spec = generate_openapi_schema(internal_api_app)
    path = spec["paths"][PATH]
    assert path["get"]["operationId"] == "visit_prep_sms_policy_read"
    assert path["post"]["operationId"] == "visit_prep_sms_policy_decide"
    schemas = spec["components"]["schemas"]
    request = schemas["VisitPrepSMSPolicyDecisionRequest"]
    assert request["additionalProperties"] is False
    assert set(request["required"]) == {
        "event_kind",
        "expected_event_sequence",
        "expected_sender_digest",
        "policy_fingerprint",
        "expires_at",
    }
    assert schemas["VisitPrepSMSPolicyResponse"]["additionalProperties"] is False
