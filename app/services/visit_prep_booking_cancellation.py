"""Preserve unknown-booking cancellations under the caller's owner locks.

The caller validates live source/practice authority and owns the transaction.
Observations precede provider I/O. No provider contact, commit or chart creation.
"""

from datetime import datetime

from app.services.visit_prep_booking_contract import (
    BookingSourceScope,
    BookingSourceUnavailable,
    CurrentVisitPrepBookingState,
)
from app.services.visit_prep_packages import _database_now

from app.models.visit_prep_booking_cancellation import VisitPrepBookingCancellation
from sqlmodel import Session, col, select


def last_unmatched_cancellation(
    session: Session,
    *,
    source: BookingSourceScope,
    booking_digest: str,
) -> VisitPrepBookingCancellation | None:
    row = session.exec(
        select(VisitPrepBookingCancellation)
        .where(
            VisitPrepBookingCancellation.connection_id == source.connection_id,
            VisitPrepBookingCancellation.booking_digest == booking_digest,
        )
        .order_by(col(VisitPrepBookingCancellation.sequence).desc())
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is not None and (
        row.source != source.source
        or row.owner_practice_member_id != source.owner_practice_member_id
        or row.practice_id != source.practice_id
        or row.owner_doctor_id != source.owner_doctor_id
    ):
        raise BookingSourceUnavailable()
    return row


def check_booking_observation(
    session: Session,
    *,
    source: BookingSourceScope,
    booking_digest: str,
    observed_at: datetime,
) -> None:
    now = _database_now(session)
    if (
        not isinstance(observed_at, datetime)
        or observed_at.utcoffset() is None
        or observed_at > now
    ):
        raise BookingSourceUnavailable()
    prior = last_unmatched_cancellation(
        session, source=source, booking_digest=booking_digest
    )
    if prior is not None and observed_at < prior.recorded_at:
        raise BookingSourceUnavailable()


def stage_unmatched_cancellation(
    session: Session,
    *,
    source: BookingSourceScope,
    state: CurrentVisitPrepBookingState,
) -> None:
    if state.active_booking is not None:
        raise BookingSourceUnavailable()
    check_booking_observation(
        session,
        source=source,
        booking_digest=state.booking_digest,
        observed_at=state.observed_at,
    )
    prior = last_unmatched_cancellation(
        session, source=source, booking_digest=state.booking_digest
    )
    # Even an unchanged current cancellation advances the source fence. An
    # older active fetch begun after the first cancellation must not win later.
    session.add(
        VisitPrepBookingCancellation(
            connection_id=source.connection_id,
            source=source.source,
            booking_digest=state.booking_digest,
            source_snapshot_hmac=state.source_snapshot_hmac,
            owner_practice_member_id=source.owner_practice_member_id,
            practice_id=source.practice_id,
            owner_doctor_id=source.owner_doctor_id,
            sequence=prior.sequence + 1 if prior else 1,
            observed_at=state.observed_at,
            recorded_at=_database_now(session),
        )
    )
    session.flush()
