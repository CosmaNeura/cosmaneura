"""Real authenticated policy API and stored choices; synthetic owners only."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import Request

from app.routes.dependencies.view_as import apply_view_as
from app.services import visit_prep_access
from app.services.visit_prep_email_contract import (
    VisitPrepEmailInvalid,
    VisitPrepInvitationEmail,
)
from app.services.visit_prep_email_evidence import email_policy_fingerprint
from app.services.visit_prep_email_policy import record_email_policy_decision
from app.services.visit_prep_email_policy_view import get_email_policy_view

from activemodel.session_manager import get_session
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep_delivery import VisitPrepEmailDelivery
from app.models.visit_prep_email_authority import VisitPrepEmailConsentEvent
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from tests.routes.visit_prep_question_sets_test import _provider_client
from tests.services.visit_prep_email_policy_test import _audits, _inputs, _ready, _rows
from tests.services.visit_preps_test import _withdraw_seat

PATH = "/internal/v1/visit-prep/email-policy"


def _fixture(monkeypatch):
    fixture = _ready("policy-http")
    monkeypatch.setattr(visit_prep_access, "VISIT_PREP_SETTINGS", fixture[1])
    return fixture


def _payload(fixture, **changes):
    intent = _inputs(fixture)
    return {
        "expected_event_sequence": 0,
        "event_kind": "approved",
        "policy_fingerprint": intent["policy_fingerprint"],
        "expires_at": intent["expires_at"].isoformat(),
    } | changes


def _post(client, payload, key=None, **kwargs):
    return client.post(
        PATH, json=payload, headers={"Idempotency-Key": key or str(uuid4())}, **kwargs
    )


def _private(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    for private in (
        "ciphertext",
        "actor_binding",
        "vp1_",
        "example.invalid",
        "clerk",
        "patient@example",
    ):
        assert private not in response.text


def test_actual_policy_review_approval_withdrawal_and_old_replay_never_send(
    monkeypatch,
):
    fixture = _fixture(monkeypatch)
    scope = fixture[0]
    payload, key = _payload(fixture), str(uuid4())
    with _provider_client(scope.doctor, monkeypatch) as client:
        initial = client.get(PATH)
        body = initial.json()
        assert initial.status_code == 200
        assert body["contract"] == "visit_prep.email_policy.v1"
        assert body["status"] == "not_approved" and body["event_sequence"] == 0
        assert body["delivery_status"] == "not_connected"
        assert (
            body["decision_policy_fingerprint"]
            is body["recorded_at"]
            is body["expires_at"]
            is None
        )
        assert body["policy_fingerprint"] == email_policy_fingerprint()
        assert "{private_preparation_link}" in body["template_text"]
        assert "story in your own words" in body["template_text"]
        approved = _post(client, payload, key)
        assert approved.status_code == 200
        assert approved.json()["disposition"] == "recorded"
        assert set(approved.json()) == {
            "event_sequence",
            "current_event_sequence",
            "event_kind",
            "policy_fingerprint",
            "expires_at",
            "recorded_at",
            "disposition",
        }
        current = client.get(PATH)
        assert current.json()["status"] == "approved"
        withdrawn = _post(
            client,
            _payload(
                fixture,
                expected_event_sequence=1,
                event_kind="withdrawn",
                expires_at=None,
            ),
        )
        assert withdrawn.status_code == 200
        replay = _post(client, payload, key)
        assert replay.json()["event_sequence"] == 1
        assert replay.json()["current_event_sequence"] == 2
        assert replay.json()["disposition"] == "replayed"
        stale = _post(client, payload)
        assert stale.status_code == 409
        final = client.get(PATH)
        assert (
            final.json()["status"] == "withdrawn"
            and final.json()["event_sequence"] == 2
        )
    assert len(_rows(scope)) == len(_audits(scope)) == 2
    with get_session() as session:
        assert not session.exec(
            select(VisitPrepEmailConsentEvent).where(
                VisitPrepEmailConsentEvent.owner_practice_member_id == scope.owner.id
            )
        ).all()
        assert not session.exec(
            select(VisitPrepEmailDelivery).where(
                VisitPrepEmailDelivery.owner_practice_member_id == scope.owner.id
            )
        ).all()
        reads = session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.resource_ref == str(scope.owner.id),
                AuthorityAuditEvent.event_type == "visit_prep.email_policy_read",
            )
        ).all()
    assert len(reads) == 3
    assert all(row.content_sha256 == body["policy_fingerprint"] for row in reads)
    for response in (initial, approved, current, withdrawn, replay, stale, final):
        _private(response)
    assert fixture[1].email_send_enabled is fixture[1].public_api_enabled is False


def test_expired_policy_and_changed_actual_template_are_not_current_approval(
    monkeypatch,
):
    fixture = _fixture(monkeypatch)
    record_email_policy_decision(
        **_inputs(fixture, expires_at=datetime.now(UTC) + timedelta(seconds=1))
    )
    with get_session() as session:
        session.execute(text("SELECT pg_sleep(1.1)"))
    with _provider_client(fixture[0].doctor, monkeypatch) as client:
        expired = client.get(PATH)
        assert expired.json()["status"] == "expired"
        original = VisitPrepInvitationEmail.body.fget
        monkeypatch.setattr(
            VisitPrepInvitationEmail,
            "body",
            property(lambda self: original(self) + "\nUpdated fixed guidance."),
        )
        changed = client.get(PATH)
        assert changed.json()["status"] == "policy_changed"
        assert (
            changed.json()["policy_fingerprint"]
            != changed.json()["decision_policy_fingerprint"]
        )
        withdrawal = _post(
            client,
            _payload(
                fixture,
                expected_event_sequence=1,
                event_kind="withdrawn",
                policy_fingerprint=changed.json()["decision_policy_fingerprint"],
                expires_at=None,
            ),
        )
        assert withdrawal.status_code == 200
        assert client.get(PATH).json()["status"] == "withdrawn"
        assert "Updated fixed guidance." in changed.json()["template_text"]


@pytest.mark.parametrize(
    "changes",
    [
        {"owner_doctor_id": "doctor_caller"},
        {"actor_id": "actor_caller"},
        {"expected_event_sequence": True},
        {"expected_event_sequence": -1},
        {"expected_event_sequence": 2_147_483_647},
        {"policy_fingerprint": "A" * 64},
        {"event_kind": "consented"},
        {"expires_at": "2026-10-20T12:00:00"},
        {"expires_at": 1_900_000_000},
        {"expires_at": "1900000000"},
    ],
)
def test_invalid_payload_is_redacted_and_does_not_record_choice(monkeypatch, changes):
    fixture = _fixture(monkeypatch)
    with _provider_client(fixture[0].doctor, monkeypatch) as client:
        response = _post(client, _payload(fixture, **changes))
    assert response.status_code == 422 and response.json() == {
        "detail": "Visit Prep request is invalid."
    }
    _private(response)
    assert not _rows(fixture[0])


@pytest.mark.parametrize(
    "query", ["?actor=private_caller", "?event_sequence=0", "?limit=1&limit=2"]
)
def test_unknown_and_duplicate_query_fields_are_rejected_without_echo(
    monkeypatch, query
):
    fixture = _fixture(monkeypatch)
    with _provider_client(fixture[0].doctor, monkeypatch) as client:
        response = client.get(PATH + query)
    assert response.status_code == 422
    assert "private_caller" not in response.text
    _private(response)


@pytest.mark.parametrize("header", ["absent", "duplicate", "wrong", "noncanonical"])
def test_operation_header_requires_one_canonical_uuid(monkeypatch, header):
    fixture = _fixture(monkeypatch)
    headers = {
        "absent": [],
        "duplicate": [
            ("Idempotency-Key", str(uuid4())),
            ("Idempotency-Key", str(uuid4())),
        ],
        "wrong": [("Idempotency-Key", "private_header")],
        "noncanonical": [("Idempotency-Key", "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA")],
    }[header]
    with _provider_client(fixture[0].doctor, monkeypatch) as client:
        response = client.post(PATH, json=_payload(fixture), headers=headers)
    assert response.status_code in {409, 422}
    assert "private_header" not in response.text
    assert not _rows(fixture[0])
    _private(response)


@pytest.mark.parametrize(
    "change", ["disabled", "cohort", "baa", "seat", "doctor", "support"]
)
def test_current_authority_denies_both_read_and_decision(monkeypatch, change):
    fixture = _fixture(monkeypatch)
    scope, settings = fixture
    if change == "disabled":
        monkeypatch.setattr(
            visit_prep_access, "VISIT_PREP_SETTINGS", replace(settings, enabled=False)
        )
    elif change == "cohort":
        monkeypatch.setattr(
            visit_prep_access,
            "VISIT_PREP_SETTINGS",
            replace(settings, enabled_doctor_ids=frozenset()),
        )
    elif change == "baa":
        scope.practice.negotiated_baa_on_file = False
        scope.practice.save()
    elif change == "seat":
        _withdraw_seat(scope.owner)
    elif change == "doctor":
        scope.doctor.deleted_at = datetime.now(UTC)
        scope.doctor.save()
    with _provider_client(scope.doctor, monkeypatch) as client:
        if change == "support":

            def support(request: Request):
                request.state.view_as = object()

            client.app.dependency_overrides[apply_view_as] = support
        read, decision = client.get(PATH), _post(client, _payload(fixture))
    assert read.status_code in {404, 409} and decision.status_code in {404, 409}
    assert not _rows(scope)
    _private(read)
    _private(decision)


def test_streamed_oversized_body_is_denied_before_dispatch(monkeypatch):
    fixture = _fixture(monkeypatch)
    with _provider_client(fixture[0].doctor, monkeypatch) as client:
        response = client.post(
            PATH,
            content=iter([b" " * 700, b" " * 700]),
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": str(uuid4()),
            },
        )
    assert response.status_code == 413 and not _rows(fixture[0])
    _private(response)


def test_actual_lost_commit_ack_recovers_one_choice_through_same_http_operation(
    monkeypatch,
):
    fixture = _fixture(monkeypatch)
    original = Session.commit
    payload, key = _payload(fixture), str(uuid4())
    with _provider_client(fixture[0].doctor, monkeypatch) as client:
        with monkeypatch.context() as patch:

            def lost_ack(session):
                original(session)
                raise SQLAlchemyError("synthetic_private_commit_error")

            patch.setattr(Session, "commit", lost_ack)
            failed = _post(client, payload, key)
        assert failed.status_code == 409
        replay = _post(client, payload, key)
        assert replay.status_code == 200 and replay.json()["disposition"] == "replayed"
    assert len(_rows(fixture[0])) == len(_audits(fixture[0])) == 1
    assert "synthetic_private" not in failed.text


def test_policy_read_audit_failure_never_returns_a_snapshot(monkeypatch):
    fixture = _fixture(monkeypatch)
    original = Session.flush

    def fail_audit(session, *args, **kwargs):
        if any(isinstance(row, AuthorityAuditEvent) for row in session.new):
            raise SQLAlchemyError("synthetic_private_read_error")
        return original(session, *args, **kwargs)

    with (
        _provider_client(fixture[0].doctor, monkeypatch) as client,
        monkeypatch.context() as patch,
    ):
        patch.setattr(Session, "flush", fail_audit)
        response = client.get(PATH)
    assert response.status_code == 409
    assert (
        "template_text" not in response.text
        and "synthetic_private" not in response.text
    )


def test_domain_read_rejects_wrong_authenticated_subject_without_private_chain(
    monkeypatch,
):
    fixture = _fixture(monkeypatch)
    scope, settings = fixture
    with pytest.raises(VisitPrepEmailInvalid) as caught:
        get_email_policy_view(
            owner_practice_member_id=scope.owner.id,
            owner_doctor_id=scope.doctor.id,
            practice_id=scope.practice.id,
            expected_clerk_subject="user_wrong",
            settings=settings,
        )
    assert caught.value.__cause__ is caught.value.__context__ is None


def test_policy_openapi_has_only_current_owner_operations(monkeypatch):
    fixture = _fixture(monkeypatch)
    with _provider_client(fixture[0].doctor, monkeypatch) as client:
        schema = client.app.openapi()
    operations = schema["paths"][PATH]
    assert set(operations) == {"get", "post"}
    assert operations["get"]["operationId"] == "visit_prep_email_policy_read"
    assert operations["post"]["operationId"] == "visit_prep_email_policy_decide"
    assert "requestBody" not in operations["get"]
    parameters = operations["post"]["parameters"]
    assert [
        (value["name"], value["in"], value["required"]) for value in parameters
    ] == [("Idempotency-Key", "header", True)]
    body = schema["components"]["schemas"]["VisitPrepEmailPolicyDecisionRequest"]
    assert set(body["properties"]) == {
        "expected_event_sequence",
        "event_kind",
        "policy_fingerprint",
        "expires_at",
    }
    assert body["additionalProperties"] is False
