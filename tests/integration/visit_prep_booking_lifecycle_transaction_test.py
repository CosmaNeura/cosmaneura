"""Real lifecycle writes can join an atomic, caller-owned booking transaction."""

from datetime import UTC, datetime, timedelta

import pytest

from app.services.visit_preps import (
    stage_visit_prep_reschedule,
    stage_visit_prep_revocation,
)

from activemodel.session_manager import get_engine
from sqlalchemy import text
from sqlmodel import Session

from tests.services.visit_preps_test import _create, _scope


def _snapshot(prep_id):
    with Session(get_engine()) as database:
        return {
            table: database.execute(
                text(
                    f"SELECT to_jsonb(t)::text FROM {table} t "
                    f"WHERE {key} = :prep ORDER BY id"
                ),
                {"prep": prep_id.uuid},
            )
            .scalars()
            .all()
            for table, key in (
                ("visit_prep", "id"),
                ("visit_prep_schedule_generation", "visit_prep_id"),
                ("visit_prep_credential_generation", "visit_prep_id"),
            )
        }


@pytest.mark.parametrize("operation", ["reschedule", "cancel"])
@pytest.mark.parametrize("commit", [False, True])
def test_booking_caller_owns_lifecycle_commit_and_failed_receipt_rolls_back(
    monkeypatch, operation, commit
):
    now = datetime.now(UTC) + timedelta(seconds=2)
    scope = _scope(f"booking-atomic-{operation}", now=now)
    created = _create(scope, now=now, scheduled_at=now + timedelta(days=7))
    before = _snapshot(created.visit_prep.id)
    arguments = {
        "owner_practice_member_id": scope.owner.id,
        "owner_doctor_id": scope.doctor.id,
        "practice_id": scope.practice.id,
        "visit_prep_id": created.visit_prep.id,
        "expected_credential_generation": 1,
        "now": now + timedelta(minutes=1),
    }

    def deny_helper_commit():
        raise AssertionError("The lifecycle helper must not commit booking work")

    with Session(get_engine()) as database:
        monkeypatch.setattr(database, "commit", deny_helper_commit)
        if operation == "reschedule":
            result = stage_visit_prep_reschedule(
                database,
                **arguments,
                expected_schedule_generation=1,
                scheduled_at=now + timedelta(days=8),
                timezone_name="UTC",
            )
            assert result.access_token is not None
            assert result.visit_prep.schedule_generation == 2
            assert result.visit_prep.credential_generation == 2
        else:
            result = stage_visit_prep_revocation(
                database, **arguments, reason="appointment_cancelled"
            )
            assert result.access_token is None
            assert result.visit_prep.credential_active is False
        # Independent readers see neither half of an uncommitted handoff.
        assert _snapshot(created.visit_prep.id) == before
        if commit:
            Session.commit(database)  # Explicit caller acknowledgment, not helper.
        else:
            # A failed booking receipt or final source gate must undo the writes.
            database.rollback()

    after = _snapshot(created.visit_prep.id)
    if not commit:
        assert after == before
    elif operation == "reschedule":
        assert len(after["visit_prep_schedule_generation"]) == 2
        assert len(after["visit_prep_credential_generation"]) == 2
    else:
        assert after != before
        assert "appointment_cancelled" in after["visit_prep"][0]
        assert "appointment_cancelled" in after["visit_prep_credential_generation"][0]
