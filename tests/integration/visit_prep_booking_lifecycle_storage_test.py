"""Real append-only booking event constraints and existing source preservation."""

from datetime import timedelta

import pytest

from app.services.visit_prep_packages import _database_now
from app.services.visit_preps import (
    _lock_current_lineage,
    _lock_visit_prep,
    stage_visit_prep_reschedule,
    stage_visit_prep_revocation,
)

from activemodel.session_manager import get_engine
from app.models.visit_prep_booking import VisitPrepBookingReceipt
from app.models.visit_prep_booking_event import VisitPrepBookingEvent
from sqlalchemy import text
from sqlmodel import Session, select

from tests.integration.visit_prep_booking_test import _post, _ready


def _created(monkeypatch):
    with monkeypatch.context() as source:
        scope, _, settings, _, _, client = _ready(source)
        with client:
            assert _post(client, settings).status_code == 200
    with Session(get_engine()) as database:
        receipt = database.exec(
            select(VisitPrepBookingReceipt).where(
                VisitPrepBookingReceipt.owner_practice_member_id == scope.owner.id
            )
        ).one()
    return scope, receipt


def _event(database, scope, receipt, *, action="rescheduled", sequence=1):
    prep = _lock_visit_prep(
        database,
        owner_practice_member_id=scope.owner.id,
        visit_prep_id=receipt.visit_prep_id,
    )
    schedule, credential = _lock_current_lineage(database, visit_prep=prep)
    return VisitPrepBookingEvent(
        booking_receipt_id=receipt.id,
        visit_prep_id=prep.id,
        owner_practice_member_id=scope.owner.id,
        question_set_version_id=receipt.question_set_version_id,
        schedule_generation_id=schedule.id,
        credential_generation_id=credential.id,
        sequence=sequence,
        action=action,
        prior_snapshot_hmac=receipt.snapshot_hmac,
        source_snapshot_hmac="b" * 64,
        observed_at=_database_now(database),
        recorded_at=_database_now(database),
    )


def _stage(database, scope, receipt, action):
    prep = _lock_visit_prep(
        database,
        owner_practice_member_id=scope.owner.id,
        visit_prep_id=receipt.visit_prep_id,
    )
    schedule, credential = _lock_current_lineage(database, visit_prep=prep)
    args = {
        "owner_practice_member_id": scope.owner.id,
        "owner_doctor_id": scope.doctor.id,
        "practice_id": scope.practice.id,
        "visit_prep_id": prep.id,
        "expected_credential_generation": credential.generation,
        "now": _database_now(database),
    }
    if action == "rescheduled":
        stage_visit_prep_reschedule(
            database,
            **args,
            expected_schedule_generation=schedule.generation,
            scheduled_at=schedule.scheduled_at + timedelta(days=1),
            timezone_name="UTC",
        )
    else:
        stage_visit_prep_revocation(
            database,
            **args,
            reason=(
                "appointment_cancelled"
                if action == "cancelled"
                else "physician_revoked"
            ),
        )


@pytest.mark.parametrize("action", ["rescheduled", "cancelled", "recipient_changed"])
def test_lifecycle_receipt_commits_with_actual_state_and_cannot_be_mutated(
    monkeypatch, action
):
    scope, receipt = _created(monkeypatch)
    with Session(get_engine()) as database:
        _stage(database, scope, receipt, action)
        event = _event(database, scope, receipt, action=action)
        database.add(event)
        database.commit()
        identifier = event.id.uuid
    for statement in (
        "UPDATE visit_prep_booking_event SET source_snapshot_hmac = :digest WHERE id = :id",
        "DELETE FROM visit_prep_booking_event WHERE id = :id",
    ):
        with Session(get_engine()) as database:
            with pytest.raises(Exception, match="immutable"):
                database.execute(
                    text(statement), {"id": identifier, "digest": "c" * 64}
                )
            database.rollback()


@pytest.mark.parametrize(
    "fault", ["sequence", "prior", "time", "wrong_receipt", "unapplied", "wrong_action"]
)
def test_invalid_lifecycle_receipt_rejects_and_rolls_back_the_staged_change(
    monkeypatch, fault
):
    scope, receipt = _created(monkeypatch)
    with Session(get_engine()) as database:
        if fault != "unapplied":
            _stage(database, scope, receipt, "rescheduled")
        event = _event(database, scope, receipt)
        if fault == "sequence":
            event.sequence = 2
        elif fault == "prior":
            event.prior_snapshot_hmac = "c" * 64
        elif fault == "time":
            event.observed_at = receipt.recorded_at - timedelta(seconds=1)
        elif fault == "wrong_receipt":
            other_scope, other = _created(monkeypatch)
            event.booking_receipt_id = other.id
            assert other_scope.owner.id != scope.owner.id
        elif fault == "wrong_action":
            event.action = "cancelled"
        database.add(event)
        with pytest.raises(Exception, match="Visit Prep booking event"):
            database.commit()
        database.rollback()
    with Session(get_engine()) as database:
        prep = _lock_visit_prep(
            database,
            owner_practice_member_id=scope.owner.id,
            visit_prep_id=receipt.visit_prep_id,
        )
        schedule, credential = _lock_current_lineage(database, visit_prep=prep)
        assert schedule.generation == credential.generation == 1
        assert prep.revoked_at is credential.revoked_at is None
        assert not database.exec(
            select(VisitPrepBookingEvent).where(
                VisitPrepBookingEvent.booking_receipt_id == receipt.id
            )
        ).all()
