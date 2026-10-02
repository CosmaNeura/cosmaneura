"""Real current-scope resolution with an explicit invented source verifier."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

import app.services.visit_prep_email_authority as authority
from app.configuration.visit_prep import VisitPrepSettings
from app.services.visit_prep_email_authority import resolve_locked_email_authority
from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_prep_email_payload import invitation_recipient_digest
from app.services.visit_preps import reschedule_visit_prep, revoke_visit_prep

from activemodel.session_manager import get_session
from app.models.practice import lock_practice_seats
from sqlalchemy import text

from tests.services.visit_prep_email_evidence_test import (
    _evidence,
    _store_approval,
    _store_consent,
)
from tests.services.visit_preps_test import _create, _lineage, _scope


def _ready(label="email-authority", *, expiry=None):
    now = datetime.now(UTC)
    scope = _scope(label, now=now)
    created = _create(scope, now=now, scheduled_at=now + timedelta(days=14))
    root, _, _ = _lineage(created.visit_prep.id)
    _, approval, consent = _evidence(root, expires=expiry)
    _store_approval(approval)
    _store_consent(consent)
    settings = VisitPrepSettings(
        enabled=True,
        public_api_enabled=True,
        email_send_enabled=True,
        enabled_doctor_ids=frozenset({str(scope.doctor.id)}),
    )
    return scope, root, consent, settings


def _resolve(session, fixture, **changes):
    scope, root, consent, settings = fixture

    def invented_source(_session, evidence, *, checked_at):
        # Test-only authoritative source: no real capture adapter is installed.
        # Compare the complete expected intent, not a source label or row flag.
        return evidence == consent and checked_at >= consent.effective_at

    return resolve_locked_email_authority(
        session,
        **(
            {
                "owner_practice_member_id": scope.owner.id,
                "owner_doctor_id": scope.doctor.id,
                "practice_id": scope.practice.id,
                "expected_clerk_subject": scope.doctor.clerk_id,
                "visit_prep_id": root.id,
                "source_verifier": invented_source,
                "settings": settings,
            }
            | changes
        ),
    )


def _deny(fixture, **changes):
    with get_session() as session, pytest.raises(VisitPrepEmailInvalid) as caught:
        with session.begin_nested():
            _resolve(session, fixture, **changes)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_exact_current_streams_resolve_without_chart_or_mutation():
    fixture = _ready()
    with get_session() as session:
        session.execute(text("SELECT 1"))
        transaction = session.get_transaction()
        resolved = _resolve(session, fixture)
        assert session.get_transaction() is transaction
        assert resolved.preparation.patient_id is None
        assert resolved.preparation.appointment_id is None
        assert resolved.approval.event_kind == "approved"
        assert resolved.consent == fixture[2]
        assert resolved.credential.generation == 1
        assert not session.new and not session.dirty and not session.deleted
        assert repr(resolved) == "LockedEmailAuthority(<redacted>)"


@pytest.mark.parametrize(
    "changes",
    [
        {"source_verifier": None},
        {"source_verifier": lambda *_a, **_k: False},
        {"source_verifier": lambda *_a, **_k: 1},
        {"view_as_active": True},
        {"view_as_active": None},
        {"expected_clerk_subject": "other-subject"},
    ],
)
def test_missing_source_verification_impersonation_or_support_view_is_denied(changes):
    _deny(_ready(), **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"enabled": False},
        {"public_api_enabled": False},
        {"email_send_enabled": False},
        {"configuration_valid": False},
        {"enabled_doctor_ids": frozenset()},
        {"email_send_enabled": 1},
    ],
)
def test_current_release_and_channel_gates_are_independent(changes):
    fixture = _ready()
    _deny(fixture, settings=replace(fixture[3], **changes))


@pytest.mark.parametrize(
    "field",
    ["owner_practice_member_id", "owner_doctor_id", "practice_id", "visit_prep_id"],
)
def test_other_real_owner_or_preparation_cannot_substitute_scope(field):
    fixture, other = _ready(), _ready("other-email-authority")
    values = {
        "owner_practice_member_id": other[0].owner.id,
        "owner_doctor_id": other[0].doctor.id,
        "practice_id": other[0].practice.id,
        "visit_prep_id": other[1].id,
    }
    _deny(fixture, **{field: values[field]})


@pytest.mark.parametrize("stream", ["approval", "consent"])
def test_latest_withdrawal_blocks_an_older_unexpired_approved_stream(stream):
    fixture = _ready()
    _, approval, consent = _evidence(fixture[1], sequence=2, kind="withdrawn")
    if stream == "approval":
        _store_approval(approval)
    else:
        _store_consent(consent)
    _deny(fixture)


@pytest.mark.parametrize("change", ["policy", "recipient", "role", "source"])
def test_latest_authentic_but_inapplicable_evidence_is_not_permission(change):
    fixture = _ready()
    _, approval, consent = _evidence(fixture[1], sequence=2)
    if change == "policy":
        _store_approval(replace(approval, policy_fingerprint="a" * 64))
    else:
        changes = {
            "recipient": {
                "recipient_digest": invitation_recipient_digest("other@example.com")
            },
            "role": {
                "actor_role": "caregiver",
                "source_kind": "caregiver_confirmation",
            },
            "source": {"source_payload_digest": "b" * 64},
        }
        changed = replace(consent, **changes[change])
        _store_consent(changed)
        # Even an adapter approving this exact different recipient cannot grant
        # permission to the mailbox actually stored on the preparation.
        if change == "recipient":
            fixture = fixture[:2] + (changed, fixture[3])
    _deny(fixture)


@pytest.mark.parametrize(
    "change", ["baa", "entitlement", "seat", "doctor", "revocation"]
)
def test_current_authority_and_lifecycle_are_rechecked(change):
    fixture = _ready()
    scope, root, _, _ = fixture
    if change in {"baa", "entitlement"}:
        scope.practice.negotiated_baa_on_file = change != "baa"
        scope.practice.billing_exempt = change != "entitlement"
        scope.practice.save()
    elif change == "seat":
        with get_session() as session:
            lock_practice_seats(session, scope.practice.id)
            scope.owner.seat_state = "inactive"
            session.add(scope.owner)
            session.commit()
            session.refresh(scope.owner)
    elif change == "doctor":
        scope.doctor.deleted_at = datetime.now(UTC)
        scope.doctor.save()
    elif change == "revocation":
        revoke_visit_prep(
            owner_practice_member_id=scope.owner.id,
            owner_doctor_id=scope.doctor.id,
            practice_id=scope.practice.id,
            visit_prep_id=root.id,
            expected_credential_generation=1,
            reason="physician_revoked",
        )
    _deny(fixture)


def test_reschedule_resolves_only_the_new_credential_not_a_stale_generation():
    fixture = _ready()
    scope, root, _, _ = fixture
    reschedule_visit_prep(
        owner_practice_member_id=scope.owner.id,
        owner_doctor_id=scope.doctor.id,
        practice_id=scope.practice.id,
        visit_prep_id=root.id,
        scheduled_at=datetime.now(UTC) + timedelta(days=20),
        timezone_name="UTC",
        expected_schedule_generation=1,
        expected_credential_generation=1,
    )
    with get_session() as session:
        resolved = _resolve(session, fixture)
        assert resolved.credential.generation == 2
        assert resolved.credential.revoked_at is None


def test_runtime_policy_change_invalidates_previous_standing_approval(monkeypatch):
    fixture = _ready()
    monkeypatch.setattr(authority, "email_policy_fingerprint", lambda: "c" * 64)
    _deny(fixture)


def test_source_verification_cannot_commit_away_the_current_authority_locks():
    fixture = _ready()

    def committing(session, *_args, **_kwargs):
        session.commit()
        return True

    _deny(fixture, source_verifier=committing)


def test_unavailable_source_does_not_expose_its_private_exception_chain():
    fixture = _ready()

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("invented private source") from ValueError("private actor")

    _deny(fixture, source_verifier=unavailable)


def test_source_verification_cannot_release_a_caller_savepoint():
    fixture = _ready()

    def releasing(session, *_args, **_kwargs):
        session.get_nested_transaction().rollback()
        return True

    _deny(fixture, source_verifier=releasing)


@pytest.mark.parametrize("stream", ["approval", "consent"])
def test_source_check_elapsed_time_is_not_a_frozen_authority_grant(stream):
    fixture = _ready()
    _, approval, consent = _evidence(
        fixture[1], sequence=2, expires=datetime.now(UTC) + timedelta(seconds=1)
    )
    if stream == "approval":
        _store_approval(approval)
    else:
        _store_consent(consent)
        fixture = fixture[:2] + (consent, fixture[3])

    def slow_source(session, evidence, *, checked_at):
        assert evidence == fixture[2]
        session.execute(text("SELECT pg_sleep(1.1)"))
        return True

    _deny(fixture, source_verifier=slow_source)


def test_opaque_storage_only_ciphertext_cannot_pass_runtime_resolution():
    fixture = _ready()
    from tests.services.visit_prep_email_authority_model_test import _approval

    row, _ = _approval(fixture[1], sequence=2)
    row.save()
    _deny(fixture)
