import secrets
from datetime import datetime, timedelta, timezone

import pytest

from app.services.public_entitlements import (
    MAX_ACTIVE_CLINICIAN_SEATS,
    ActiveClinicianSeatLimitReached,
    activate_existing_clinician_seat,
    active_clinician_seat_count,
    lock_practice_for_active_seat_mutation,
    public_subscription_in_force,
    record_active_clinician_seat_quantity,
)

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.practice import ClinicianSeatState, Practice, PracticeMember
from sqlmodel import select


def _practice() -> Practice:
    return Practice(
        legal_name="Seat Policy Practice",
        join_code=f"S{secrets.token_hex(4).upper()[:7]}",
        email_domain=f"seat-{secrets.token_hex(4)}.example.com",
        origin_channel="direct",
        is_solo=False,
    ).save()


def _member(
    practice: Practice,
    ordinal: int,
    *,
    seat_state: ClinicianSeatState = ClinicianSeatState.active,
    revoked: bool = False,
) -> PracticeMember:
    doctor = Doctor(
        clerk_id=f"user_seat_policy_{secrets.token_hex(8)}",
        email=f"clinician-{ordinal}-{secrets.token_hex(3)}@{practice.email_domain}",
        biography="",
    ).save()
    revoked_at = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc) if revoked else None
    return PracticeMember(
        practice_id=practice.id,
        doctor_id=doctor.id,
        role="workforce",
        seat_state=seat_state.value,
        access_revoked_at=revoked_at,
        access_revoked_by_ref="test-seat-policy" if revoked else None,
        access_revocation_reason="Test irreversible revocation" if revoked else None,
        access_records_retain_until=(
            revoked_at + timedelta(days=365 * 8) if revoked_at else None
        ),
    ).save()


def test_authoritative_count_excludes_inactive_and_revoked_members() -> None:
    practice = _practice()
    _member(practice, 1)
    _member(practice, 2, seat_state=ClinicianSeatState.inactive)
    _member(practice, 3, revoked=True)

    with get_session() as session:
        assert active_clinician_seat_count(session, practice.id) == 1


def test_public_subscription_gate_fails_closed_during_billing_incidents() -> None:
    practice = _practice()
    now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
    practice.billing_exempt = True
    practice.trial_ends_at = now + timedelta(days=14)
    practice.stripe_checkout_session_id = "cs_test_receiver_contract"

    for incident_state in ("pending_verification", "invalid", "future_state"):
        practice.subscription_status = incident_state
        assert not public_subscription_in_force(practice, at=now)

    practice.billing_exempt = False
    practice.subscription_status = None
    practice.stripe_checkout_session_id = None
    assert not public_subscription_in_force(practice, at=now)

    practice.stripe_checkout_session_id = "cs_test_receiver_contract"
    assert not public_subscription_in_force(practice, at=now)

    practice.subscription_status = "trialing"
    practice.trial_ends_at = None
    assert not public_subscription_in_force(practice, at=now)

    practice.trial_ends_at = now - timedelta(seconds=1)
    assert not public_subscription_in_force(practice, at=now)

    practice.trial_ends_at = now + timedelta(days=14)
    assert public_subscription_in_force(practice, at=now)


def test_reactivation_is_idempotent_but_rejects_twenty_first_seat() -> None:
    practice = _practice()
    active = None
    for ordinal in range(MAX_ACTIVE_CLINICIAN_SEATS):
        active = _member(practice, ordinal)
    assert active is not None
    inactive = _member(
        practice,
        MAX_ACTIVE_CLINICIAN_SEATS,
        seat_state=ClinicianSeatState.inactive,
    )

    with get_session() as session:
        locked_practice = lock_practice_for_active_seat_mutation(
            session,
            practice.id,
        )
        locked_inactive = session.exec(
            select(PracticeMember)
            .where(PracticeMember.id == inactive.id)
            .with_for_update()
        ).one()
        with pytest.raises(ActiveClinicianSeatLimitReached, match="at most 20"):
            activate_existing_clinician_seat(
                session,
                locked_practice,
                locked_inactive,
            )

    inactive.refresh()
    assert inactive.seat_state == ClinicianSeatState.inactive.value

    with get_session() as session:
        locked_practice = lock_practice_for_active_seat_mutation(
            session,
            practice.id,
        )
        locked_active = session.exec(
            select(PracticeMember)
            .where(PracticeMember.id == active.id)
            .with_for_update()
        ).one()
        assert not activate_existing_clinician_seat(
            session,
            locked_practice,
            locked_active,
        )


def test_reactivation_and_stripe_quantity_repair_commit_atomically() -> None:
    practice = _practice()
    practice.stripe_subscription_id = f"sub_seat_{secrets.token_hex(8)}"
    practice.stripe_quantity_sync_required = False
    practice.stripe_quantity_sync_target = 7
    practice.stripe_quantity_sync_version = 4
    practice.stripe_quantity_sync_error = "prior reconciliation failure"
    practice.save()
    _member(practice, 1)
    inactive = _member(
        practice,
        2,
        seat_state=ClinicianSeatState.inactive,
    )

    with get_session() as session:
        locked_practice = lock_practice_for_active_seat_mutation(
            session,
            practice.id,
        )
        locked_inactive = session.exec(
            select(PracticeMember)
            .where(PracticeMember.id == inactive.id)
            .with_for_update()
        ).one()
        assert activate_existing_clinician_seat(
            session,
            locked_practice,
            locked_inactive,
        )
        assert record_active_clinician_seat_quantity(session, locked_practice) == 2
        assert locked_practice.stripe_quantity_sync_required is True
        assert locked_practice.stripe_quantity_sync_target == 2
        assert locked_practice.stripe_quantity_sync_version == 5
        assert locked_practice.stripe_quantity_sync_error is None
        session.commit()

    inactive.refresh()
    practice.refresh()
    assert inactive.seat_state == ClinicianSeatState.active.value
    assert practice.stripe_quantity_sync_required is True
    assert practice.stripe_quantity_sync_target == 2
    assert practice.stripe_quantity_sync_version == 5
    assert practice.stripe_quantity_sync_error is None


def test_reactivation_and_stripe_quantity_repair_roll_back_together() -> None:
    practice = _practice()
    practice.stripe_subscription_id = f"sub_seat_{secrets.token_hex(8)}"
    practice.stripe_quantity_sync_required = False
    practice.stripe_quantity_sync_target = 3
    practice.stripe_quantity_sync_version = 9
    practice.save()
    _member(practice, 1)
    inactive = _member(
        practice,
        2,
        seat_state=ClinicianSeatState.inactive,
    )

    with get_session() as session:
        locked_practice = lock_practice_for_active_seat_mutation(
            session,
            practice.id,
        )
        locked_inactive = session.exec(
            select(PracticeMember)
            .where(PracticeMember.id == inactive.id)
            .with_for_update()
        ).one()
        assert activate_existing_clinician_seat(
            session,
            locked_practice,
            locked_inactive,
        )
        assert record_active_clinician_seat_quantity(session, locked_practice) == 2
        session.rollback()

    inactive.refresh()
    practice.refresh()
    assert inactive.seat_state == ClinicianSeatState.inactive.value
    assert practice.stripe_quantity_sync_required is False
    assert practice.stripe_quantity_sync_target == 3
    assert practice.stripe_quantity_sync_version == 9
