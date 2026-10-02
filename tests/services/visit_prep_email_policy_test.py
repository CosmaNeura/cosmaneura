"""Physician decisions on owned PostgreSQL, without recipient consent or sends."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest

import app.services.visit_prep_email_policy as policy
from app.configuration.visit_prep import VisitPrepSettings
from app.services.visit_prep_email_authority import resolve_locked_email_authority
from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_prep_email_evidence import (
    email_policy_fingerprint,
    read_email_approval,
)
from app.services.visit_prep_email_policy import record_email_policy_decision

from activemodel.session_manager import get_session
from app.models.authority import AuthorityAuditEvent
from app.models.practice import lock_practice_seats
from app.models.visit_prep_email_authority import (
    VisitPrepEmailApprovalEvent,
    VisitPrepEmailConsentEvent,
)
from sqlalchemy import func, text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from tests.services.visit_prep_email_evidence_test import _evidence, _store_consent
from tests.services.visit_preps_test import _create, _lineage, _scope


def _ready(label="email-policy"):
    scope = _scope(label, now=datetime.now(UTC))
    settings = VisitPrepSettings(
        enabled=True,
        enabled_doctor_ids=frozenset({str(scope.doctor.id)}),
    )
    return scope, settings


def _inputs(fixture, **changes):
    scope, settings = fixture
    return {
        "owner_practice_member_id": scope.owner.id,
        "owner_doctor_id": scope.doctor.id,
        "practice_id": scope.practice.id,
        "expected_clerk_subject": scope.doctor.clerk_id,
        "operation_id": str(uuid4()),
        "expected_event_sequence": 0,
        "event_kind": "approved",
        "policy_fingerprint": email_policy_fingerprint(),
        "expires_at": datetime.now(UTC) + timedelta(days=30),
        "settings": settings,
    } | changes


def _rows(scope):
    with get_session() as session:
        return session.exec(
            select(VisitPrepEmailApprovalEvent)
            .where(
                VisitPrepEmailApprovalEvent.owner_practice_member_id == scope.owner.id
            )
            .order_by(VisitPrepEmailApprovalEvent.event_sequence)
        ).all()


def _audits(scope):
    refs = [str(row.id) for row in _rows(scope)]
    with get_session() as session:
        return session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.resource_type == "visit_prep_email_approval",
                AuthorityAuditEvent.resource_ref.in_(refs),
            )
        ).all()


def _deny(inputs):
    with pytest.raises(VisitPrepEmailInvalid) as caught:
        record_email_policy_decision(**inputs)
    assert str(caught.value) == "Visit Prep email is invalid"
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_explicit_policy_decision_has_locked_actor_and_atomic_content_free_audit():
    fixture = _ready()
    inputs = _inputs(fixture)
    receipt = record_email_policy_decision(**inputs)
    assert receipt.disposition == "recorded"
    assert receipt.event_kind == "approved"
    assert receipt.event_sequence == receipt.current_event_sequence == 1
    assert receipt.expires_at == inputs["expires_at"]
    assert repr(receipt) == "EmailPolicyDecisionReceipt(<redacted>)"
    row = _rows(fixture[0])[0]
    evidence = read_email_approval(row)
    assert evidence.actor_doctor_id == str(fixture[0].doctor.id)
    assert evidence.operation_id == inputs["operation_id"]
    assert row.created_at == row.recorded_at == receipt.recorded_at
    audit = _audits(fixture[0])[0]
    assert audit.event_source == "platform_authoritative"
    assert audit.event_type == "visit_prep.email_policy_approved"
    assert str(audit.client_event_id) == inputs["operation_id"]
    assert audit.request_digest == row.payload_hmac_sha256
    assert audit.occurred_at == row.recorded_at
    assert audit.resource_ref == receipt.event_id
    assert audit.actor_id is None and audit.person_id is None
    assert audit.tenant_id is None and audit.interaction_id is None
    assert audit.platform_refs == [
        {"kind": "doctor", "ref": str(fixture[0].doctor.id)},
        {"kind": "practice", "ref": str(fixture[0].practice.id)},
        {"kind": "practice_member", "ref": str(fixture[0].owner.id)},
    ]
    with get_session() as session:
        assert (
            session.exec(
                select(func.count())
                .select_from(VisitPrepEmailConsentEvent)
                .where(
                    VisitPrepEmailConsentEvent.owner_practice_member_id
                    == fixture[0].owner.id
                )
            ).one()
            == 0
        )
    assert fixture[1].email_send_enabled is False
    assert fixture[1].public_api_enabled is False


def test_exact_replay_preserves_evidence_and_does_not_duplicate_audit():
    fixture = _ready()
    inputs = _inputs(fixture)
    recorded = record_email_policy_decision(**inputs)
    original_cipher = _rows(fixture[0])[0].evidence_ciphertext
    replayed = record_email_policy_decision(**inputs)
    assert replayed == replace(recorded, disposition="replayed")
    assert _rows(fixture[0])[0].evidence_ciphertext == original_cipher
    assert len(_rows(fixture[0])) == len(_audits(fixture[0])) == 1


def test_captured_policy_is_consumable_but_never_substitutes_for_recipient_consent():
    fixture = _ready()
    scope = fixture[0]
    record_email_policy_decision(**_inputs(fixture))
    now = datetime.now(UTC)
    created = _create(scope, now=now, scheduled_at=now + timedelta(days=14))
    root, _, _ = _lineage(created.visit_prep.id)
    _, _, consent = _evidence(root)

    def invented_source(_session, evidence, *, checked_at):
        # Still a synthetic source: this test qualifies physician approval
        # consumption, not the missing real patient-consent capture adapter.
        return evidence == consent and checked_at >= consent.effective_at

    resolve_inputs = {
        "owner_practice_member_id": scope.owner.id,
        "owner_doctor_id": scope.doctor.id,
        "practice_id": scope.practice.id,
        "expected_clerk_subject": scope.doctor.clerk_id,
        "visit_prep_id": root.id,
        "source_verifier": invented_source,
        "settings": replace(
            fixture[1], public_api_enabled=True, email_send_enabled=True
        ),
    }
    with get_session() as session, pytest.raises(VisitPrepEmailInvalid):
        resolve_locked_email_authority(session, **resolve_inputs)
    _store_consent(consent)
    with get_session() as session:
        resolved = resolve_locked_email_authority(session, **resolve_inputs)
        assert resolved.approval == read_email_approval(_rows(scope)[0])
    record_email_policy_decision(
        **_inputs(
            fixture, expected_event_sequence=1, event_kind="withdrawn", expires_at=None
        )
    )
    with get_session() as session, pytest.raises(VisitPrepEmailInvalid):
        resolve_locked_email_authority(session, **resolve_inputs)


@pytest.mark.parametrize(
    "changes",
    [
        {"expires_at": datetime.now(UTC) + timedelta(days=31)},
        {"event_kind": "withdrawn", "expires_at": None},
        {"policy_fingerprint": "a" * 64},
        {"expected_event_sequence": 1},
    ],
)
def test_operation_reuse_with_divergent_intent_is_denied(changes):
    fixture = _ready()
    inputs = _inputs(fixture)
    record_email_policy_decision(**inputs)
    _deny(inputs | changes)
    assert len(_rows(fixture[0])) == len(_audits(fixture[0])) == 1


def test_old_approval_replay_does_not_restore_a_withdrawn_stream():
    fixture = _ready()
    inputs = _inputs(fixture)
    first = record_email_policy_decision(**inputs)
    withdrawal_inputs = _inputs(
        fixture, expected_event_sequence=1, event_kind="withdrawn", expires_at=None
    )
    withdrawn = record_email_policy_decision(**withdrawal_inputs)
    assert withdrawn.event_kind == "withdrawn"
    assert withdrawn.event_sequence == 2 and withdrawn.expires_at is None
    assert record_email_policy_decision(**withdrawal_inputs).disposition == "replayed"
    replayed = record_email_policy_decision(**inputs)
    assert replayed.event_id == first.event_id
    assert replayed.event_sequence == 1 and replayed.current_event_sequence == 2
    assert read_email_approval(_rows(fixture[0])[-1]).event_kind == "withdrawn"
    _deny(inputs | {"operation_id": str(uuid4())})
    assert len(_rows(fixture[0])) == len(_audits(fixture[0])) == 2
    renewed = record_email_policy_decision(
        **_inputs(fixture, expected_event_sequence=2)
    )
    assert renewed.event_sequence == renewed.current_event_sequence == 3


def test_policy_update_requires_new_confirmation_but_does_not_block_withdrawal(
    monkeypatch,
):
    fixture = _ready()
    inputs = _inputs(fixture)
    record_email_policy_decision(**inputs)
    monkeypatch.setattr(policy, "email_policy_fingerprint", lambda: "b" * 64)
    _deny(
        _inputs(
            fixture,
            expected_event_sequence=1,
            policy_fingerprint=inputs["policy_fingerprint"],
        )
    )
    withdrawn = record_email_policy_decision(
        **_inputs(
            fixture,
            expected_event_sequence=1,
            event_kind="withdrawn",
            expires_at=None,
            policy_fingerprint=inputs["policy_fingerprint"],
        )
    )
    assert withdrawn.event_kind == "withdrawn"
    assert record_email_policy_decision(**inputs).current_event_sequence == 2
    renewed = record_email_policy_decision(
        **_inputs(fixture, expected_event_sequence=2, policy_fingerprint="b" * 64)
    )
    assert renewed.policy_fingerprint == "b" * 64


@pytest.mark.parametrize(
    "changes",
    [
        {"operation_id": "00000000-0000-0000-0000-000000000000"},
        {"operation_id": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"},
        {"operation_id": uuid4()},
        {"expected_event_sequence": True},
        {"expected_event_sequence": -1},
        {"expected_event_sequence": 2_147_483_647},
        {"policy_fingerprint": "A" * 64},
        {"policy_fingerprint": "a" * 64},
        {"event_kind": "granted"},
        {"expires_at": None},
        {"expires_at": datetime(2026, 10, 1)},
        {"expires_at": datetime.now(UTC) - timedelta(seconds=1)},
        {"event_kind": "withdrawn"},
        {"expected_clerk_subject": "another-subject"},
        {"expected_clerk_subject": ""},
        {"view_as_active": True},
        {"view_as_active": None},
        {"settings": VisitPrepSettings()},
        {"settings": VisitPrepSettings(configuration_valid=False)},
    ],
)
def test_invalid_input_and_disabled_defaults_never_capture_approval(changes):
    fixture = _ready()
    _deny(_inputs(fixture, **changes))
    assert not _rows(fixture[0])


@pytest.mark.parametrize(
    "changes", [{"enabled": 1}, {"enabled_doctor_ids": frozenset()}]
)
def test_strict_feature_and_current_cohort_are_required(changes):
    fixture = _ready()
    _deny(_inputs(fixture, settings=replace(fixture[1], **changes)))


@pytest.mark.parametrize(
    "field", ["owner_practice_member_id", "owner_doctor_id", "practice_id"]
)
def test_other_real_owner_cannot_substitute_scope(field):
    fixture, other = _ready(), _ready("other-email-policy")
    values = {
        "owner_practice_member_id": other[0].owner.id,
        "owner_doctor_id": other[0].doctor.id,
        "practice_id": other[0].practice.id,
    }
    _deny(_inputs(fixture, **{field: values[field]}))


@pytest.mark.parametrize("change", ["baa", "entitlement", "membership", "doctor"])
def test_replay_still_requires_current_core_authority(change):
    fixture = _ready()
    inputs = _inputs(fixture)
    record_email_policy_decision(**inputs)
    scope = fixture[0]
    if change in {"baa", "entitlement"}:
        scope.practice.negotiated_baa_on_file = change != "baa"
        scope.practice.billing_exempt = change != "entitlement"
        scope.practice.save()
    elif change == "membership":
        with get_session() as session:
            lock_practice_seats(session, scope.practice.id)
            scope.owner.seat_state = "inactive"
            session.add(scope.owner)
            session.commit()
    else:
        scope.doctor.deleted_at = datetime.now(UTC)
        scope.doctor.save()
    _deny(inputs)


def test_equivalent_aware_deadline_replays_and_expired_receipt_is_not_renewed():
    fixture = _ready()
    inputs = _inputs(fixture, expires_at=datetime.now(UTC) + timedelta(seconds=1))
    recorded = record_email_policy_decision(**inputs)
    with get_session() as session:
        session.execute(text("SELECT pg_sleep(1.1)"))
    replayed = record_email_policy_decision(
        **(
            inputs
            | {
                "expires_at": inputs["expires_at"].astimezone(
                    timezone(timedelta(hours=2))
                )
            }
        )
    )
    assert replayed.expires_at == recorded.expires_at
    assert replayed.disposition == "replayed"
    assert len(_rows(fixture[0])) == 1


def test_failed_audit_rolls_back_both_decision_and_audit_without_private_chain(
    monkeypatch,
):
    fixture = _ready()
    original = Session.flush

    def fail_audit(session, *args, **kwargs):
        if any(isinstance(row, AuthorityAuditEvent) for row in session.new):
            raise SQLAlchemyError("synthetic private evidence must not escape")
        return original(session, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Session, "flush", fail_audit)
        _deny(_inputs(fixture))
    assert not _rows(fixture[0]) and not _audits(fixture[0])


def test_lost_commit_acknowledgment_recovers_by_exact_replay_without_second_decision(
    monkeypatch,
):
    fixture = _ready()
    inputs = _inputs(fixture)
    original = Session.commit

    def lost_ack(session):
        original(session)
        raise SQLAlchemyError("synthetic private commit diagnostic")

    with monkeypatch.context() as patch:
        patch.setattr(Session, "commit", lost_ack)
        _deny(inputs)
    assert len(_rows(fixture[0])) == len(_audits(fixture[0])) == 1
    receipt = record_email_policy_decision(**inputs)
    assert receipt.disposition == "replayed"
    assert receipt.event_sequence == receipt.current_event_sequence == 1
    assert len(_rows(fixture[0])) == len(_audits(fixture[0])) == 1


def test_deadline_elapsed_before_commit_rolls_back_decision_and_audit(monkeypatch):
    fixture = _ready()
    inputs = _inputs(fixture, expires_at=datetime.now(UTC) + timedelta(seconds=1))
    original = policy._authorize_owner
    calls = 0

    def delayed(session, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            session.execute(text("SELECT pg_sleep(1.1)"))
        return original(session, **kwargs)

    monkeypatch.setattr(policy, "_authorize_owner", delayed)
    _deny(inputs)
    assert calls == 3
    assert not _rows(fixture[0]) and not _audits(fixture[0])
