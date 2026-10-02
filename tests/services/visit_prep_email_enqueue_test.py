"""Atomic real outbox commands with explicitly invented consent qualification."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

import app.services.visit_prep_email_enqueue as enqueue
from app.configuration.visit_prep import VisitPrepSettings
from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_prep_email_enqueue import (
    enqueue_email_invitation,
    enqueue_locked_email_invitation,
)
from app.services.visit_prep_email_payload import (
    bind_stored_invitation,
    decrypt_invitation_payload,
)
from app.services.visit_preps import revoke_visit_prep, rotate_visit_prep_credential
from app.utils.encryption import EncryptionError, decrypt_value

from activemodel.session_manager import get_session
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep_delivery import VisitPrepEmailDelivery
from sqlalchemy import text
from sqlmodel import select

from tests.services.visit_prep_delivery_model_test import _update
from tests.services.visit_prep_email_evidence_test import (
    _evidence,
    _store_approval,
    _store_consent,
)
from tests.services.visit_preps_test import _create, _lineage, _scope


def _ready(label="email-enqueue", *, expiry=None):
    now = datetime.now(UTC)
    scope = _scope(label, now=now)
    created = _create(scope, now=now, scheduled_at=now + timedelta(days=14))
    root, _, _ = _lineage(created.visit_prep.id)
    _, approval, consent = _evidence(root, expires=expiry)
    _store_approval(approval)
    _store_consent(consent)

    def invented_source(_session, evidence, *, checked_at):
        # TEST ONLY: compare complete invented intent, not a consent label.
        return evidence == consent and checked_at >= consent.effective_at

    arguments = {
        "owner_practice_member_id": scope.owner.id,
        "owner_doctor_id": scope.doctor.id,
        "practice_id": scope.practice.id,
        "expected_clerk_subject": scope.doctor.clerk_id,
        "visit_prep_id": root.id,
        "expected_credential_generation": 1,
        "access_token": created.access_token,
        "operation_id": str(uuid4()),
        "patient_origin": "https://intake.example.invalid",
        "source_verifier": invented_source,
        "settings": VisitPrepSettings(
            enabled=True,
            public_api_enabled=True,
            email_send_enabled=True,
            enabled_doctor_ids=frozenset({str(scope.doctor.id)}),
        ),
    }
    return scope, root, consent, arguments


def _rows(fixture):
    with get_session() as session:
        return list(
            session.exec(
                select(VisitPrepEmailDelivery).where(
                    VisitPrepEmailDelivery.visit_prep_id == fixture[1].id
                )
            ).all()
        )


def _audits(fixture):
    with get_session() as session:
        return list(
            session.exec(
                select(AuthorityAuditEvent).where(
                    AuthorityAuditEvent.event_type
                    == "visit_prep.email_invitation_queued",
                    AuthorityAuditEvent.client_event_id == fixture[3]["operation_id"],
                )
            ).all()
        )


def _deny(fixture, **changes):
    with pytest.raises(VisitPrepEmailInvalid) as caught:
        enqueue_email_invitation(**(fixture[3] | changes))
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert str(caught.value) == "Visit Prep email is invalid"
    return caught.value


def test_atomic_private_handoff_without_a_chart_and_exact_replay():
    fixture = _ready()
    receipt = enqueue_email_invitation(**fixture[3])
    replay = enqueue_email_invitation(**fixture[3])
    (row,) = _rows(fixture)
    (audit,) = _audits(fixture)
    assert receipt.disposition == "queued" and replay.disposition == "replayed"
    assert receipt.delivery_id == replay.delivery_id == str(row.id)
    assert receipt.state == row.state == "queued"
    assert receipt.requested_at == row.created_at == row.requested_at
    assert receipt.expires_at <= row.requested_at + timedelta(hours=24)
    assert fixture[2].expires_at is not None
    assert receipt.expires_at <= fixture[2].expires_at
    assert fixture[1].patient_id is None and fixture[1].appointment_id is None
    assert repr(receipt) == "EmailEnqueueReceipt(<redacted>)"
    assert row.payload_ciphertext is not None
    prep, _, credentials = _lineage(fixture[1].id)
    invitation = decrypt_invitation_payload(
        row.payload_ciphertext,
        bind_stored_invitation(
            row, prep, credentials[-1], checked_at=datetime.now(UTC)
        ),
        patient_origin=fixture[3]["patient_origin"],
    )
    assert invitation.access_token == fixture[3]["access_token"]
    assert invitation.recipient == "patient@example.com"
    evidence = json.loads(decrypt_value(row.authorization_ciphertext))
    assert set(evidence) == {
        "context",
        "request_digest",
        "subject",
        "approval",
        "consent",
    }
    assert evidence["approval"]["event_kind"] == "approved"
    assert evidence["consent"] == fixture[2].manifest()
    assert evidence["context"]["delivery_id"] == str(row.id)
    assert audit.request_digest == evidence["request_digest"]
    for private in (
        fixture[3]["access_token"],
        "patient@example.com",
        fixture[3]["expected_clerk_subject"],
    ):
        assert private not in repr(receipt)
        assert private not in row.authorization_ciphertext
        assert private not in row.payload_ciphertext
        assert private not in audit.model_dump_json()


@pytest.mark.parametrize(
    "changes",
    [
        {"source_verifier": None},
        {"source_verifier": lambda *_a, **_k: False},
        {"source_verifier": lambda *_a, **_k: 1},
        {"view_as_active": True},
        {"view_as_active": None},
        {"expected_clerk_subject": "other-subject"},
        {"expected_credential_generation": True},
        {"expected_credential_generation": 2},
        {"expected_credential_generation": 0},
        {"operation_id": str(uuid4()).upper()},
        {"operation_id": "00000000-0000-0000-0000-000000000000"},
        {"patient_origin": "https://intake.example.invalid/path"},
        {"patient_origin": "http://intake.example.invalid"},
        {"access_token": "vp1_" + "B" * 43},
        {"access_token": "malformed"},
    ],
)
def test_unqualified_or_divergent_new_intent_never_leaves_an_outbox_or_audit(changes):
    fixture = _ready()
    _deny(fixture, **changes)
    assert _rows(fixture) == [] and _audits(fixture) == []


@pytest.mark.parametrize(
    "change",
    [
        "enabled",
        "public_api_enabled",
        "email_send_enabled",
        "configuration_valid",
        "cohort",
    ],
)
def test_new_handoff_requires_all_current_channel_and_release_controls(change):
    fixture = _ready()
    settings = fixture[3]["settings"]
    changed = replace(
        settings,
        **(
            {"enabled_doctor_ids": frozenset()}
            if change == "cohort"
            else {change: False}
        ),
    )
    _deny(fixture, settings=changed)
    assert _rows(fixture) == []


@pytest.mark.parametrize(
    "field",
    ["owner_practice_member_id", "owner_doctor_id", "practice_id", "visit_prep_id"],
)
def test_other_real_owner_cannot_substitute_scope(field):
    fixture, other = _ready(), _ready("other-enqueue")
    _deny(fixture, **{field: other[3][field]})
    assert _rows(fixture) == [] and _rows(other) == []


@pytest.mark.parametrize("field", ["operation_id", "access_token", "patient_origin"])
def test_existing_credential_never_gets_a_second_invitation_or_divergent_replay(field):
    fixture = _ready()
    original = enqueue_email_invitation(**fixture[3])
    changed = {
        "operation_id": str(uuid4()),
        "access_token": "vp1_" + "C" * 43,
        "patient_origin": "https://other.example.invalid",
    }
    _deny(fixture, **{field: changed[field]})
    assert len(_rows(fixture)) == len(_audits(fixture)) == 1
    assert str(_rows(fixture)[0].id) == original.delivery_id


@pytest.mark.parametrize(
    "change", ["consent", "approval", "revocation", "rotation", "channel"]
)
def test_exact_historical_replay_is_not_current_sending_permission(change):
    fixture = _ready()
    scope, root, _, arguments = fixture
    original = enqueue_email_invitation(**arguments)
    if change in {"consent", "approval"}:
        _, approval, consent = _evidence(root, sequence=2, kind="withdrawn")
        _store_consent(consent) if change == "consent" else _store_approval(approval)
    elif change == "revocation":
        revoke_visit_prep(
            owner_practice_member_id=scope.owner.id,
            owner_doctor_id=scope.doctor.id,
            practice_id=scope.practice.id,
            visit_prep_id=root.id,
            expected_credential_generation=1,
            reason="physician_revoked",
        )
    elif change == "rotation":
        rotate_visit_prep_credential(
            owner_practice_member_id=scope.owner.id,
            owner_doctor_id=scope.doctor.id,
            practice_id=scope.practice.id,
            visit_prep_id=root.id,
            expected_credential_generation=1,
        )
    else:
        arguments = arguments | {
            "settings": replace(arguments["settings"], email_send_enabled=False)
        }
    replay = enqueue_email_invitation(**(arguments | {"source_verifier": None}))
    assert (
        replay.disposition == "replayed" and replay.delivery_id == original.delivery_id
    )
    assert len(_rows(fixture)) == len(_audits(fixture)) == 1


def test_owner_authority_is_still_required_for_historical_receipts():
    fixture = _ready()
    enqueue_email_invitation(**fixture[3])
    fixture[0].practice.negotiated_baa_on_file = False
    fixture[0].practice.save()
    _deny(fixture)
    assert len(_rows(fixture)) == 1


def test_global_operation_key_collision_rolls_back_new_root_work():
    fixture, other = _ready(), _ready("key-collision")
    enqueue_email_invitation(**fixture[3])
    _deny(other, operation_id=fixture[3]["operation_id"])
    assert len(_rows(fixture)) == 1 and _rows(other) == [] and _audits(other) == []


def test_caller_can_roll_back_outbox_and_audit_together_without_helper_commit():
    fixture = _ready()
    with get_session() as session:
        session.execute(text("SELECT 1"))
        transaction = session.get_transaction()
        assert transaction is not None
        receipt = enqueue_locked_email_invitation(session, **fixture[3])
        assert session.get_transaction() is transaction and transaction.is_active
        assert session.get(VisitPrepEmailDelivery, receipt.delivery_id) is not None
        session.rollback()
    assert _rows(fixture) == [] and _audits(fixture) == []


@pytest.mark.parametrize("failure", ["encrypt", "post_insert_check", "source"])
def test_crypto_source_and_post_insert_failure_roll_back_private_state(
    monkeypatch, failure
):
    fixture = _ready()
    if failure == "encrypt":

        def fail(*_a, **_k):
            raise EncryptionError()

        monkeypatch.setattr(enqueue, "encrypt_invitation_payload", fail)
    elif failure == "post_insert_check":

        def fail(*_a, **_k):
            raise VisitPrepEmailInvalid()

        monkeypatch.setattr(enqueue, "decrypt_invitation_payload", fail)
    else:

        def fail(*_a, **_k):
            raise ValueError("synthetic private consent source")

        fixture[3]["source_verifier"] = fail
    _deny(fixture)
    assert _rows(fixture) == [] and _audits(fixture) == []


def test_source_expiring_during_second_check_prevents_any_new_intent():
    fixture = _ready(expiry=datetime.now(UTC) + timedelta(seconds=1))
    calls = 0

    def slow(session, evidence, *, checked_at):
        nonlocal calls
        calls += 1
        if calls == 2:
            session.execute(text("SELECT pg_sleep(1.1)"))
        return evidence == fixture[2]

    _deny(fixture, source_verifier=slow)
    assert calls == 2 and _rows(fixture) == [] and _audits(fixture) == []


def test_final_authority_failure_rolls_back_both_outbox_and_audit(monkeypatch):
    fixture = _ready()
    original = enqueue._authorize_owner
    calls = 0

    def fail_after_audit(session, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            assert session.exec(
                select(VisitPrepEmailDelivery).where(
                    VisitPrepEmailDelivery.visit_prep_id == fixture[1].id
                )
            ).one()
            assert session.exec(
                select(AuthorityAuditEvent).where(
                    AuthorityAuditEvent.client_event_id == fixture[3]["operation_id"]
                )
            ).one()
            raise VisitPrepEmailInvalid()
        return original(session, **kwargs)

    monkeypatch.setattr(enqueue, "_authorize_owner", fail_after_audit)
    _deny(fixture)
    assert calls == 2 and _rows(fixture) == [] and _audits(fixture) == []


@pytest.mark.parametrize("when", [1, 2])
def test_source_cannot_commit_away_locks_and_leave_an_intent(when):
    fixture = _ready()
    calls = 0

    def committing(session, evidence, *, checked_at):
        nonlocal calls
        calls += 1
        if calls == when:
            session.commit()
        return evidence == fixture[2]

    _deny(fixture, source_verifier=committing)
    assert calls == when and _rows(fixture) == [] and _audits(fixture) == []


@pytest.mark.parametrize(
    "state,code",
    [
        ("accepted", None),
        ("failed", "transport_unavailable"),
        ("suppressed", "consent_withdrawn"),
        ("uncertain", "submission_uncertain"),
    ],
)
def test_terminal_replay_never_restores_a_secret_or_reopens_submission(state, code):
    fixture = _ready()
    first = enqueue_email_invitation(**fixture[3])
    (row,) = _rows(fixture)
    if state in {"accepted", "uncertain"}:
        row = _update(
            row,
            state="submitting",
            submitted_at=datetime.now(UTC),
            lease_until=datetime.now(UTC) + timedelta(seconds=90),
        )
    _update(
        row,
        state=state,
        outcome_code=code,
        payload_ciphertext=None,
        lease_until=None,
        finalized_at=datetime.now(UTC),
    )
    replay = enqueue_email_invitation(**(fixture[3] | {"source_verifier": None}))
    (stored,) = _rows(fixture)
    assert replay.state == state and replay.disposition == "replayed"
    assert replay.delivery_id == first.delivery_id and stored.payload_ciphertext is None
    assert len(_audits(fixture)) == 1


def test_invitation_secret_deadline_is_capped_at_twenty_four_hours():
    fixture = _ready(expiry=datetime.now(UTC) + timedelta(days=3))
    first = enqueue_email_invitation(**fixture[3])
    assert (
        timedelta(hours=23, minutes=59)
        < first.expires_at - first.requested_at
        <= timedelta(hours=24)
    )
