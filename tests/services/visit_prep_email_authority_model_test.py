"""Real synthetic PostgreSQL evidence streams; no permission resolver or send."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.utils.encryption import encrypt_value

from activemodel.session_manager import get_session
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_email_authority import (
    VisitPrepEmailApprovalEvent,
    VisitPrepEmailConsentEvent,
)
from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError
from sqlmodel import select

from tests.services.visit_prep_model_test import _change, _prep


def _approval(root=None, *, sequence=1, kind="approved", **changes):
    root = root or _prep().save()
    now = datetime.now(UTC)
    values = {
        "owner_practice_member_id": root.owner_practice_member_id,
        "practice_id": root.practice_id,
        "owner_doctor_id": root.owner_doctor_id,
        "event_sequence": sequence,
        "event_kind": kind,
        "policy_fingerprint": "a" * 64,
        "operation_id": uuid4(),
        "payload_hmac_sha256": "b" * 64,
        "evidence_ciphertext": encrypt_value("Invented approval provenance"),
        "recorded_at": now,
        "expires_at": now + timedelta(days=30) if kind == "approved" else None,
    }
    return VisitPrepEmailApprovalEvent(**(values | changes)), root


def _consent(root=None, *, sequence=1, kind="granted", **changes):
    root = root or _prep().save()
    now = datetime.now(UTC)
    values = {
        "visit_prep_id": root.id,
        "owner_practice_member_id": root.owner_practice_member_id,
        "question_set_version_id": root.question_set_version_id,
        "event_sequence": sequence,
        "event_kind": kind,
        "actor_role": "patient",
        "recipient_digest": "c" * 64,
        "operation_id": uuid4(),
        "source_event_id": uuid4(),
        "payload_hmac_sha256": "d" * 64,
        "evidence_ciphertext": encrypt_value("Invented consent provenance"),
        "recorded_at": now,
        "effective_at": now,
        "expires_at": now + timedelta(days=30) if kind == "granted" else None,
    }
    return VisitPrepEmailConsentEvent(**(values | changes)), root


def _reject(statement, message):
    with get_session() as session, pytest.raises(DBAPIError, match=message):
        with session.begin_nested():
            session.execute(statement)


def _insert_reject(row, message):
    with get_session() as session, pytest.raises(DBAPIError, match=message):
        with session.begin_nested():
            session.add(row)
            session.flush()


def test_separate_encrypted_streams_have_database_times_and_no_chart_requirement():
    approval, root = _approval()
    consent, _ = _consent(root)
    approval.recorded_at = consent.recorded_at = datetime(2000, 1, 1, tzinfo=UTC)
    approval.save()
    consent.save()
    with get_session() as session:
        for row in (approval, consent):
            stored = session.get(type(row), row.id)
            assert stored.recorded_at == stored.created_at == stored.updated_at
            assert stored.recorded_at.year >= 2026
            assert "evidence_ciphertext" not in stored.model_dump()
            assert "payload_hmac_sha256" not in stored.model_dump()
            assert "Invented" not in repr(stored)
        assert "recipient_digest" not in consent.model_dump()
        assert root.patient_id is None and root.appointment_id is None
        assert approval.event_kind == "approved" and consent.event_kind == "granted"


@pytest.mark.parametrize("factory", [_approval, _consent])
def test_streams_require_sequential_version_and_unique_operation(factory):
    first, root = factory()
    first.save()
    skipped, _ = factory(root, sequence=3)
    _insert_reject(skipped, "sequence is not current")
    duplicate, _ = factory(root, sequence=2, operation_id=first.operation_id)
    _insert_reject(duplicate, "operation")
    second, _ = factory(root, sequence=2)
    second.save()
    assert second.event_sequence == 2


def test_source_event_cannot_be_reused_under_a_different_command_key():
    first, root = _consent()
    first.save()
    duplicate, _ = _consent(root, sequence=2, source_event_id=first.source_event_id)
    _insert_reject(duplicate, "uq_visit_prep_email_consent_source")


@pytest.mark.parametrize("factory", [_approval, _consent])
def test_new_stream_cannot_start_with_a_missing_prior_sequence(factory):
    row, _ = factory(sequence=2)
    _insert_reject(row, "sequence is not current")


def test_approval_does_not_fabricate_recipient_consent_or_follow_admin_ownership():
    row, root = _approval()
    row.save()
    with get_session() as session:
        assert (
            session.exec(
                select(VisitPrepEmailConsentEvent).where(
                    VisitPrepEmailConsentEvent.visit_prep_id == root.id
                )
            ).all()
            == []
        )
    other = _prep().save()
    invalid, _ = _approval(root, sequence=2, owner_doctor_id=other.owner_doctor_id)
    _insert_reject(invalid, "owner is unavailable")
    invalid, _ = _approval(root, sequence=2, practice_id=other.practice_id)
    _insert_reject(invalid, "owner is unavailable")


def test_consent_requires_the_exact_root_owner_and_question_lineage():
    row, root = _consent()
    other = _prep().save()
    row.owner_practice_member_id = other.owner_practice_member_id
    _insert_reject(row, "root lineage is unavailable")
    row, _ = _consent(root, question_set_version_id=other.question_set_version_id)
    _insert_reject(row, "root lineage is unavailable")


@pytest.mark.parametrize("factory", [_approval, _consent])
@pytest.mark.parametrize(
    "changes,match",
    [
        ({"operation_id": UUID(int=0)}, "operation_shape"),
        ({"event_kind": "not_required"}, "shape"),
        ({"expires_at": None}, "time_shape"),
        ({"expires_at": datetime(2000, 1, 1, tzinfo=UTC)}, "expired|inactive"),
        ({"evidence_ciphertext": ""}, "evidence_shape"),
        ({"evidence_ciphertext": "x" * 8193}, "evidence_shape"),
        ({"evidence_ciphertext": "\u2603" * 3000}, "evidence_shape"),
        ({"envelope_format_version": 2}, "evidence_shape"),
        ({"payload_hmac_sha256": "PRIVATE"}, "digest_shape"),
    ],
)
def test_closed_common_shapes_are_database_enforced(factory, changes, match):
    row, _ = factory(**changes)
    _insert_reject(row, match)


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"actor_role": "physician"}, "consent_shape"),
        ({"purpose": "pastoral"}, "consent_shape"),
        ({"recipient_digest": "A" * 64}, "digest_shape"),
        ({"source_event_id": UUID(int=0)}, "operation_shape"),
        ({"effective_at": datetime(2100, 1, 1, tzinfo=UTC)}, "inactive or stale"),
    ],
)
def test_closed_consent_shapes(changes, match):
    row, _ = _consent(**changes)
    _insert_reject(row, match)


@pytest.mark.parametrize("role", ["patient", "caregiver", "trusted_source"])
def test_actor_role_is_retained_as_provenance_not_identity_verification(role):
    row, _ = _consent(actor_role=role)
    assert row.save().actor_role == role


@pytest.mark.parametrize(
    "changes", [{"template_version": "other"}, {"policy_fingerprint": "Z" * 64}]
)
def test_fixed_approval_policy_shape(changes):
    row, _ = _approval(**changes)
    _insert_reject(row, "policy_shape|digest_shape")


def test_withdrawal_is_an_append_and_old_grants_cannot_restore_permission():
    granted, root = _consent()
    granted.save()
    withdrawn, _ = _consent(
        root,
        sequence=2,
        kind="withdrawn",
        effective_at=granted.effective_at - timedelta(days=1),
    )
    withdrawn.save()
    stale, _ = _consent(root, sequence=3, effective_at=granted.effective_at)
    _insert_reject(stale, "inactive or stale")
    fresh, _ = _consent(root, sequence=3)
    fresh.save()
    assert granted.event_kind == "granted" and withdrawn.event_kind == "withdrawn"


@pytest.mark.parametrize("factory", [_approval, _consent])
def test_initial_withdrawal_is_recordable_but_cannot_expire_like_a_grant(factory):
    row, root = factory(kind="withdrawn")
    row.save()
    invalid, _ = factory(
        root,
        sequence=2,
        kind="withdrawn",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    _insert_reject(invalid, "time_shape")


def test_withdrawal_remains_recordable_after_root_revocation():
    grant, root = _consent()
    grant.save()
    _change(
        root,
        revoked_at=datetime.now(UTC),
        revoked_by_ref="actor:synthetic",
        revocation_reason="wrong_recipient",
    )
    denied, _ = _consent(root, sequence=2)
    _insert_reject(denied, "inactive or stale")
    withdrawal, _ = _consent(root, sequence=2, kind="withdrawn")
    withdrawal.save()


@pytest.mark.parametrize("factory", [_approval, _consent])
def test_sql_and_orm_cannot_update_or_delete_evidence(factory):
    row, _ = factory()
    row.save()
    _reject(
        update(type(row))
        .where(type(row).id == row.id)
        .values(evidence_ciphertext=encrypt_value("Replacement")),
        "append-only",
    )
    _reject(delete(type(row)).where(type(row).id == row.id), "append-only")
    with get_session() as session, pytest.raises(RuntimeError, match="append-only"):
        with session.begin_nested():
            stored = session.get(type(row), row.id)
            stored.payload_hmac_sha256 = "e" * 64
            session.flush()
    with (
        get_session() as session,
        pytest.raises(RuntimeError, match="qualified retention cleanup"),
    ):
        with session.begin_nested():
            session.delete(session.get(type(row), row.id))
            session.flush()


def test_dormant_consent_custody_does_not_silently_cascade_on_root_cleanup():
    row, root = _consent()
    row.save()
    _change(
        root,
        revoked_at=root.created_at + timedelta(seconds=10),
        revoked_by_ref="actor:synthetic",
        revocation_reason="wrong_recipient",
    )
    _reject(
        delete(VisitPrep).where(VisitPrep.id == root.id),
        "fk_visit_prep_email_consent_root",
    )
    with get_session() as session:
        assert session.get(VisitPrepEmailConsentEvent, row.id) is not None


@pytest.mark.parametrize(
    "table", ["visit_prep_email_approval_event", "visit_prep_email_consent_event"]
)
def test_authority_truncate_is_denied(table):
    # Fixed table names from this test, never client-provided identifiers.
    _reject(text(f"TRUNCATE public.{table}"), "authority evidence cannot be truncated")
