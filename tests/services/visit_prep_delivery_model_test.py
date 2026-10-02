"""Real owned PostgreSQL outbox guards; no recipient contact or provider calls."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.utils.encryption import encrypt_value

from activemodel.session_manager import get_session
from app.models.visit_prep import VisitPrep, VisitPrepLegalHold
from app.models.visit_prep_delivery import VisitPrepEmailDelivery
from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError

from tests.services.visit_prep_model_test import _change, _credential, _prep, _schedule


def _delivery():
    root = _prep().save()
    now = datetime.now(UTC)
    schedule = _schedule(root, now + timedelta(days=14)).save()
    credential = _credential(root, schedule).save()
    row = VisitPrepEmailDelivery(
        visit_prep_id=root.id,
        owner_practice_member_id=root.owner_practice_member_id,
        question_set_version_id=root.question_set_version_id,
        schedule_generation_id=schedule.id,
        credential_generation=credential.generation,
        authorization_ciphertext=encrypt_value("Synthetic authorization evidence"),
        payload_ciphertext=encrypt_value("Synthetic private invitation"),
        requested_at=now,
        expires_at=now + timedelta(hours=1),
    )
    return row, root, schedule, credential


def _update(row, **changes):
    with get_session() as session:
        session.execute(
            update(VisitPrepEmailDelivery)
            .where(VisitPrepEmailDelivery.id == row.id)
            .values(**changes)
        )
        session.commit()
        return session.get(VisitPrepEmailDelivery, row.id)


def _reject(statement, match):
    with get_session() as session, pytest.raises(DBAPIError, match=match):
        with session.begin_nested():
            session.execute(statement)
            session.flush()


def _insert_reject(row, match):
    with get_session() as session, pytest.raises(DBAPIError, match=match):
        with session.begin_nested():
            session.add(row)
            session.flush()


def test_outbox_is_private_timed_and_deduplicated_without_a_chart():
    row, root, _, _ = _delivery()
    row.created_at = row.requested_at = datetime.now(UTC) + timedelta(days=1)
    before = datetime.now(UTC)
    row.save()
    assert before <= row.requested_at <= datetime.now(UTC)
    assert row.created_at == row.requested_at == row.updated_at
    assert root.patient_id is None and root.appointment_id is None
    assert "payload_ciphertext" not in row.model_dump()
    assert "authorization_ciphertext" not in row.model_dump()
    assert "Synthetic" not in repr(row)
    duplicate = VisitPrepEmailDelivery(
        **{
            **{key: value for key, value in row.model_dump().items() if key != "id"},
            "operation_id": uuid4(),
            "authorization_ciphertext": row.authorization_ciphertext,
            "payload_ciphertext": row.payload_ciphertext,
        }
    )
    _insert_reject(duplicate, "uq_visit_prep_email_invitation")


def test_single_submission_preserves_intent_and_terminal_secret_is_erased():
    row, _, _, _ = _delivery()
    row.save()
    before = datetime.now(UTC)
    submitted = _update(
        row,
        state="submitting",
        submitted_at=before,
        lease_until=before + timedelta(hours=1),
    )
    assert before <= submitted.submitted_at <= datetime.now(UTC)
    assert submitted.lease_until == submitted.submitted_at + timedelta(seconds=90)
    _reject(
        update(VisitPrepEmailDelivery)
        .where(VisitPrepEmailDelivery.id == row.id)
        .values(state="queued", submitted_at=None, lease_until=None),
        "cannot return to send lane",
    )
    accepted = _update(
        row,
        state="accepted",
        payload_ciphertext=None,
        lease_until=None,
        finalized_at=before,
    )
    assert accepted.payload_ciphertext is None
    assert accepted.operation_id == row.operation_id
    assert accepted.authorization_ciphertext == row.authorization_ciphertext
    assert accepted.finalized_at >= submitted.submitted_at
    _reject(
        update(VisitPrepEmailDelivery)
        .where(VisitPrepEmailDelivery.id == row.id)
        .values(
            state="queued",
            payload_ciphertext=row.payload_ciphertext,
            submitted_at=None,
            finalized_at=None,
        ),
        "terminal outcome is immutable",
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("operation_id", uuid4()),
        ("template_version", "other"),
        ("authorization_ciphertext", "other"),
        ("payload_ciphertext", "other"),
        ("expires_at", datetime.now(UTC) + timedelta(hours=2)),
    ],
)
def test_queued_intent_and_secret_cannot_be_changed(field, value):
    row, _, _, _ = _delivery()
    row.save()
    _reject(
        update(VisitPrepEmailDelivery)
        .where(VisitPrepEmailDelivery.id == row.id)
        .values(**{field: value}),
        "immutable|cannot return to send lane",
    )


@pytest.mark.parametrize(
    "state,code",
    [
        ("failed", "message_invalid"),
        ("suppressed", "consent_withdrawn"),
        ("uncertain", "submission_uncertain"),
    ],
)
def test_terminal_outcomes_require_closed_code_and_remove_secret(state, code):
    row, _, _, _ = _delivery()
    row.save()
    now = datetime.now(UTC)
    if state == "uncertain":
        _update(
            row,
            state="submitting",
            submitted_at=now,
            lease_until=now + timedelta(seconds=90),
        )
    finalized = _update(
        row,
        state=state,
        outcome_code=code,
        payload_ciphertext=None,
        lease_until=None,
        finalized_at=now,
    )
    assert finalized.state == state and finalized.payload_ciphertext is None


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"state": "delivered"}, "must start queued"),
        ({"state": "accepted"}, "must start queued"),
        ({"operation_id": UUID(int=0)}, "operation_shape"),
        ({"template_version": "clinical-content"}, "template_shape"),
        ({"authorization_ciphertext": ""}, "envelope_shape"),
        ({"payload_ciphertext": None}, "state_shape"),
        ({"outcome_code": "private words"}, "outcome_shape"),
        ({"expires_at": datetime.now(UTC) + timedelta(days=2)}, "lifetime is invalid"),
    ],
)
def test_insert_has_closed_bounded_shapes(changes, match):
    row, _, _, _ = _delivery()
    for field, value in changes.items():
        setattr(row, field, value)
    _insert_reject(row, match)


def test_no_terminal_null_code_or_null_submission_lease_can_pass_sql_checks():
    row, _, _, _ = _delivery()
    row.save()
    now = datetime.now(UTC)
    _reject(
        update(VisitPrepEmailDelivery)
        .where(VisitPrepEmailDelivery.id == row.id)
        .values(
            state="failed", payload_ciphertext=None, finalized_at=now, outcome_code=None
        ),
        "outcome_shape",
    )
    with get_session() as session:
        constraints = session.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'public.visit_prep_email_delivery'::regclass "
                "AND conname = 'visit_prep_email_delivery_state_shape_check'"
            )
        ).scalar_one()
    assert "lease_until IS NOT NULL" in constraints


def test_obsolete_and_cross_preparation_schedule_are_denied():
    row, root, _, credential = _delivery()
    other, _, _, _ = _delivery()
    row.schedule_generation_id = other.schedule_generation_id
    _insert_reject(row, "invitation is not current")
    row.schedule_generation_id = credential.schedule_generation_id
    _schedule(root, datetime.now(UTC) + timedelta(days=15), generation=2).save()
    _insert_reject(row, "invitation is not current")


def test_outbox_cannot_be_deleted_or_truncated_outside_parent_retention():
    row, root, _, _ = _delivery()
    row.save()
    _reject(
        delete(VisitPrepEmailDelivery).where(VisitPrepEmailDelivery.id == row.id),
        "aggregate cleanup",
    )
    # The committed integration test exercises the named truncate guard without
    # this rollback fixture's deferred foreign-key trigger events.
    _reject(delete(VisitPrep).where(VisitPrep.id == root.id), "not yet eligible")
    # Expire the owned synthetic root's unused schedule through the pre-existing
    # authorized cleanup fixture: immutable schedules cannot be moved by update.
    old = datetime.now(UTC) - timedelta(days=100)
    expired = _prep(created=old).save()
    schedule = _schedule(expired, old + timedelta(days=1)).save()
    credential = _credential(expired, schedule, issued=old).save()
    # Old/expired credentials cannot acquire a new outbox entry, even for cleanup.
    pending = VisitPrepEmailDelivery(
        visit_prep_id=expired.id,
        owner_practice_member_id=expired.owner_practice_member_id,
        question_set_version_id=expired.question_set_version_id,
        schedule_generation_id=schedule.id,
        credential_generation=credential.generation,
        authorization_ciphertext=encrypt_value("Synthetic authorization evidence"),
        payload_ciphertext=encrypt_value("Synthetic private invitation"),
        requested_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    _insert_reject(pending, "invitation is not current")


def test_existing_wrong_recipient_cleanup_cascades_only_after_legal_hold_release():
    row, root, _, _ = _delivery()
    row.save()
    # Existing retention guards use transaction time. Choose a past instant
    # after the synthetic root's creation, not a future wall-clock instant.
    now = root.created_at + timedelta(seconds=10)
    _change(
        root,
        revoked_at=now,
        revoked_by_ref="actor:synthetic",
        revocation_reason="wrong_recipient",
    )
    hold = VisitPrepLegalHold(
        visit_prep_id=root.id,
        owner_practice_member_id=root.owner_practice_member_id,
        generation=1,
        placed_at=now,
        placed_by_ref="actor:synthetic",
        placement_reason="synthetic_review",
    ).save()
    _reject(delete(VisitPrep).where(VisitPrep.id == root.id), "active legal hold")
    _change(
        hold,
        released_at=now,
        released_by_ref="actor:synthetic",
        release_reason="synthetic_review_complete",
    )
    with get_session() as session:
        session.execute(delete(VisitPrep).where(VisitPrep.id == root.id))
        session.commit()
        assert session.get(VisitPrepEmailDelivery, row.id) is None
