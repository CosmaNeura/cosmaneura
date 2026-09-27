import secrets
from datetime import datetime, timedelta, timezone

import pytest

from app.services.public_entitlements import (
    ClinicianSeatPolicyError,
    lock_practice_for_active_seat_mutation,
    record_active_clinician_seat_quantity,
)

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.practice import (
    Practice,
    PracticeMember,
    PracticeSeatLockNotHeld,
    lock_practice_seats,
)

from tests.factories import save_practice_member


def _practice() -> Practice:
    return Practice(
        legal_name="Seat Lock Practice, PLLC",
        join_code=f"L{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.com",
        is_solo=False,
    ).save()


def _doctor() -> Doctor:
    nonce = secrets.token_hex(4)
    return Doctor(
        clerk_id=f"user_seat_lock_{nonce}",
        email=f"seat-lock-{nonce}@example.com",
        biography="",
    ).save()


def _member(practice: Practice) -> PracticeMember:
    return save_practice_member(
        PracticeMember(
            practice_id=practice.id,
            doctor_id=_doctor().id,
            role="workforce",
        )
    )


def test_membership_insert_without_practice_lock_fails():
    practice = _practice()

    with pytest.raises(PracticeSeatLockNotHeld):
        PracticeMember(
            practice_id=practice.id,
            doctor_id=_doctor().id,
            role="workforce",
        ).save()

    assert PracticeMember.get(practice_id=practice.id) is None


def test_membership_insert_under_practice_lock_succeeds():
    practice = _practice()
    doctor = _doctor()

    with get_session() as session:
        lock_practice_for_active_seat_mutation(session, practice.id)
        session.add(
            PracticeMember(
                practice_id=practice.id,
                doctor_id=doctor.id,
                role="workforce",
            )
        )
        session.commit()

    assert PracticeMember.get(doctor_id=doctor.id) is not None


@pytest.mark.parametrize(
    "change", ["seat_state", "access_ends_at", "access_revoked_at"]
)
def test_seat_occupancy_change_without_practice_lock_fails(change: str):
    member = _member(_practice())
    now = datetime.now(timezone.utc)
    if change == "access_ends_at":
        member.seat_state = "inactive"
        save_practice_member(member)

    with get_session() as session:
        loaded = session.get(PracticeMember, member.id)
        assert loaded is not None
        if change == "seat_state":
            loaded.seat_state = "inactive"
        elif change == "access_ends_at":
            loaded.access_ends_at = now + timedelta(hours=1)
        else:
            loaded.access_revoked_at = now
            loaded.access_revoked_by_ref = "operator"
            loaded.access_revocation_reason = "test"
            loaded.access_records_retain_until = now + timedelta(days=366 * 7)
        session.add(loaded)
        with pytest.raises(PracticeSeatLockNotHeld):
            session.flush()


def test_membership_delete_without_practice_lock_fails():
    member = _member(_practice())

    with get_session() as session:
        loaded = session.get(PracticeMember, member.id)
        session.delete(loaded)
        with pytest.raises(PracticeSeatLockNotHeld):
            session.flush()


def test_moving_a_member_requires_both_practice_locks():
    source = _practice()
    destination = _practice()
    member = _member(source)

    with get_session() as session:
        loaded = session.get(PracticeMember, member.id)
        assert loaded is not None
        lock_practice_seats(session, destination.id)
        loaded.practice_id = destination.id
        session.add(loaded)
        with pytest.raises(PracticeSeatLockNotHeld):
            session.flush()
        session.rollback()

    with get_session() as session:
        lock_practice_seats(session, source.id)
        lock_practice_seats(session, destination.id)
        loaded = session.get(PracticeMember, member.id)
        assert loaded is not None
        loaded.practice_id = destination.id
        session.add(loaded)
        session.commit()

    moved = PracticeMember.get(id=member.id)
    assert moved is not None
    assert moved.practice_id == destination.id


def test_moving_an_expired_member_still_requires_the_source_practice_lock():
    source = _practice()
    destination = _practice()
    member = _member(source)

    with get_session() as session:
        loaded = session.get(PracticeMember, member.id)
        assert loaded is not None
        session.commit()
        lock_practice_seats(session, destination.id)
        loaded.practice_id = destination.id
        session.add(loaded)
        with pytest.raises(PracticeSeatLockNotHeld):
            session.flush()


def test_moving_a_member_by_relationship_requires_both_practice_locks():
    source = _practice()
    destination = _practice()
    member = _member(source)

    with get_session() as session:
        loaded = session.get(PracticeMember, member.id)
        assert loaded is not None
        loaded.practice = lock_practice_for_active_seat_mutation(
            session, destination.id
        )
        session.add(loaded)
        with pytest.raises(PracticeSeatLockNotHeld):
            session.flush()
        session.rollback()

    with get_session() as session:
        lock_practice_seats(session, source.id)
        loaded = session.get(PracticeMember, member.id)
        assert loaded is not None
        loaded.practice = lock_practice_for_active_seat_mutation(
            session, destination.id
        )
        session.add(loaded)
        session.commit()

    moved = PracticeMember.get(id=member.id)
    assert moved is not None
    assert moved.practice_id == destination.id


def test_membership_insert_by_relationship_under_practice_lock_succeeds():
    practice = _practice()
    doctor = _doctor()

    with get_session() as session:
        session.add(
            PracticeMember(
                practice=lock_practice_for_active_seat_mutation(session, practice.id),
                doctor_id=doctor.id,
                role="workforce",
            )
        )
        session.commit()

    assert PracticeMember.get(doctor_id=doctor.id) is not None


def test_lock_from_an_earlier_transaction_does_not_authorize_a_later_change():
    practice = _practice()

    with get_session() as session:
        lock_practice_seats(session, practice.id)
        session.commit()
        session.add(
            PracticeMember(
                practice_id=practice.id,
                doctor_id=_doctor().id,
                role="workforce",
            )
        )
        with pytest.raises(PracticeSeatLockNotHeld):
            session.flush()


def test_lock_taken_in_a_rolled_back_savepoint_does_not_authorize_a_change():
    practice = _practice()

    with get_session() as session:
        savepoint = session.begin_nested()
        lock_practice_seats(session, practice.id)
        savepoint.rollback()
        session.add(
            PracticeMember(
                practice_id=practice.id,
                doctor_id=_doctor().id,
                role="workforce",
            )
        )
        with pytest.raises(PracticeSeatLockNotHeld):
            session.flush()


def test_lock_survives_savepoint_release_and_unrelated_savepoint_rollback():
    released = _practice()
    outer = _practice()

    with get_session() as session:
        lock_practice_seats(session, outer.id)
        with session.begin_nested():
            lock_practice_seats(session, released.id)
        session.begin_nested().rollback()
        for practice in (released, outer):
            session.add(
                PracticeMember(
                    practice_id=practice.id,
                    doctor_id=_doctor().id,
                    role="workforce",
                )
            )
        session.commit()

    assert PracticeMember.get(practice_id=released.id) is not None
    assert PracticeMember.get(practice_id=outer.id) is not None


def test_non_seat_membership_fields_do_not_require_the_practice_lock():
    member = _member(_practice())

    with get_session() as session:
        loaded = session.get(PracticeMember, member.id)
        assert loaded is not None
        loaded.role = "owner"
        loaded.attestation_name = "Renamed Clinician"
        session.add(loaded)
        session.commit()

    updated = PracticeMember.get(id=member.id)
    assert updated is not None
    assert updated.role == "owner"


def test_recording_stripe_quantity_requires_the_practice_lock():
    practice = _practice()
    _member(practice)

    with get_session() as session:
        loaded = session.get(Practice, practice.id)
        assert loaded is not None
        with pytest.raises(ClinicianSeatPolicyError, match="not locked"):
            record_active_clinician_seat_quantity(session, loaded)
