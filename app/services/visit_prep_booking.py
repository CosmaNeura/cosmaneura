"""Atomically bind one current booking to one chart-free preparation."""

import hmac
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from app.configuration import visit_prep
from app.services.visit_prep_access import resolve_visit_prep_access
from app.services.visit_prep_booking_cancellation import check_booking_observation
from app.services.visit_prep_booking_contract import (
    BookingSourceScope,
    BookingSourceUnavailable,
    NormalizedVisitPrepBooking,
)
from app.services.visit_prep_command_contract import (
    _normalized_schedule,
)
from app.services.visit_prep_packages import _database_now
from app.services.visit_preps import (
    _lock_owner_scope,
    _visit_prep_session,
    create_locked_visit_prep,
)

from app.models.doctor import Doctor
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_booking import VisitPrepBookingReceipt
from app.models.visit_prep_question_set import VisitPrepQuestionSetVersion
from sqlmodel import col, select


@dataclass(frozen=True)
class BookingReceipt:
    preparation_id: str
    disposition: str


def record_current_booking(
    *,
    booking: NormalizedVisitPrepBooking,
    source: BookingSourceScope,
    observed_at: datetime,
    require_current_source: Callable[[], None],
) -> BookingReceipt:
    # Only the trusted provider adapter supplies current booking and mapping.
    # A booking/contact is not a patient chart, identity assertion or consent.
    require_current_source()
    key = booking.booking_digest
    with _visit_prep_session() as session:
        now = _database_now(session)
        owner, practice = _lock_owner_scope(
            session,
            owner_practice_member_id=source.owner_practice_member_id,
            owner_doctor_id=source.owner_doctor_id,
            practice_id=source.practice_id,
            now=now,
        )
        doctor = session.get(Doctor, owner.doctor_id)
        if (
            doctor is None
            or not resolve_visit_prep_access(
                doctor, settings=visit_prep.VISIT_PREP_SETTINGS, at=now
            ).can_manage
        ):
            raise BookingSourceUnavailable()

        def require_current_admission():
            # Recheck after lock waits, including on a read-only replay.
            require_current_source()
            if not resolve_visit_prep_access(
                doctor,
                settings=visit_prep.VISIT_PREP_SETTINGS,
                at=_database_now(session),
            ).can_manage:
                raise BookingSourceUnavailable()

        _normalized_schedule(
            booking.scheduled_at, booking.timezone_name, now=now, require_future=False
        )
        check_booking_observation(
            session, source=source, booking_digest=key, observed_at=observed_at
        )
        snapshot = booking.source_snapshot_hmac
        existing = session.exec(
            select(VisitPrepBookingReceipt)
            .where(
                VisitPrepBookingReceipt.connection_id == source.connection_id,
                VisitPrepBookingReceipt.booking_digest == key,
            )
            .with_for_update()
        ).one_or_none()
        if existing is not None:
            prep = session.get(VisitPrep, existing.visit_prep_id)
            if (
                prep is None
                or existing.owner_practice_member_id != owner.id
                or prep.practice_id != practice.id
                or prep.owner_doctor_id != doctor.id
                or not hmac.compare_digest(existing.snapshot_hmac, snapshot)
            ):
                # Changed booking is not a new preparation or a stale replay.
                raise BookingSourceUnavailable()
            require_current_admission()
            return BookingReceipt(str(prep.id), "replayed")
        version = session.exec(
            select(VisitPrepQuestionSetVersion)
            .where(
                VisitPrepQuestionSetVersion.owner_practice_member_id == owner.id,
                col(VisitPrepQuestionSetVersion.is_default).is_(True),
            )
            .with_for_update()
        ).one_or_none()
        if version is None:
            raise BookingSourceUnavailable()
        result = create_locked_visit_prep(
            session,
            owner_practice_member_id=owner.id,
            owner_doctor_id=doctor.id,
            practice_id=practice.id,
            idempotency_key=str(uuid4()),
            recipient=booking.recipient,
            physician_display_name=source.physician_display_name,
            scheduled_at=booking.scheduled_at,
            timezone_name=booking.timezone_name,
            question_set_version_id=version.id,
            creation_source="appointment_booking",
            now=now,
        )
        session.add(
            VisitPrepBookingReceipt(
                connection_id=source.connection_id,
                source=source.source,
                booking_digest=key,
                snapshot_hmac=snapshot,
                visit_prep_id=result.visit_prep.id,
                owner_practice_member_id=owner.id,
                question_set_version_id=version.id,
                recorded_at=_database_now(session),
            )
        )
        # Recheck dark source and feature admission after writes and lock waits.
        require_current_admission()
        session.commit()
        # No invitation bearer in the webhook response or persistent receipt.
        # Initial-message consent is unresolved, so delivery is NOT enqueued.
        return BookingReceipt(str(result.visit_prep.id), "created")
