"""Atomically bind one current booking to one chart-free preparation."""

import hmac
import json
from dataclasses import asdict, dataclass
from typing import cast
from uuid import uuid4

from typeid import TypeID

from app.configuration import visit_prep, visit_prep_acuity
from app.configuration.visit_prep_acuity import (
    AcuityBookingSettings,
    AcuityCalendarBinding,
)
from app.services.visit_prep_access import resolve_visit_prep_access
from app.services.visit_prep_acuity import (
    AcuityBookingUnavailable,
    CurrentAcuityBooking,
)
from app.services.visit_prep_command_contract import (
    _normalize_recipient,
    _normalized_schedule,
)
from app.services.visit_prep_packages import _database_now
from app.services.visit_preps import (
    _lock_owner_scope,
    _visit_prep_session,
    create_locked_visit_prep,
)
from app.utils.encryption import pseudonymous_digest

from activemodel.types import TypeIDType
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
    booking: CurrentAcuityBooking,
    binding: AcuityCalendarBinding,
    settings: AcuityBookingSettings,
) -> BookingReceipt:
    # Only the trusted provider adapter supplies current booking and mapping.
    # A booking/contact is not a patient chart, identity assertion or consent.
    if (
        not settings.enabled
        or settings.connection_id is None
        or settings != visit_prep_acuity.SETTINGS
        or binding not in settings.calendars
        or booking.calendar_id != binding.calendar_id
    ):
        raise AcuityBookingUnavailable()
    recipient = _normalize_recipient(booking.recipient)
    key = pseudonymous_digest(
        json.dumps(
            {
                "source": "acuity",
                "connection": str(settings.connection_id),
                "appointment": booking.appointment_id,
            },
            sort_keys=True,
        ),
        context="visit-prep-booking-reference-v1",
    )
    with _visit_prep_session() as session:
        now = _database_now(session)
        owner, practice = _lock_owner_scope(
            session,
            owner_practice_member_id=cast(
                TypeIDType, TypeID.from_string(binding.owner_practice_member_id)
            ),
            owner_doctor_id=cast(
                TypeIDType, TypeID.from_string(binding.owner_doctor_id)
            ),
            practice_id=cast(TypeIDType, TypeID.from_string(binding.practice_id)),
            now=now,
        )
        doctor = session.get(Doctor, owner.doctor_id)
        if (
            doctor is None
            or not resolve_visit_prep_access(
                doctor, settings=visit_prep.VISIT_PREP_SETTINGS, at=now
            ).can_manage
        ):
            raise AcuityBookingUnavailable()

        def require_current_admission():
            # Recheck after lock waits, including on a read-only replay.
            if (
                settings != visit_prep_acuity.SETTINGS
                or not visit_prep_acuity.SETTINGS.enabled
                or not resolve_visit_prep_access(
                    doctor,
                    settings=visit_prep.VISIT_PREP_SETTINGS,
                    at=_database_now(session),
                ).can_manage
            ):
                raise AcuityBookingUnavailable()

        scheduled, timezone = _normalized_schedule(
            booking.scheduled_at, booking.timezone_name, now=now, require_future=False
        )
        snapshot = pseudonymous_digest(
            json.dumps(
                {
                    "booking": key,
                    "owner": str(owner.id),
                    "practice": str(practice.id),
                    "calendar": booking.calendar_id,
                    "recipient": asdict(recipient),
                    "scheduled": scheduled.isoformat(),
                    "timezone": timezone,
                },
                sort_keys=True,
            ),
            context="visit-prep-booking-snapshot-v1",
        )
        existing = session.exec(
            select(VisitPrepBookingReceipt)
            .where(
                VisitPrepBookingReceipt.connection_id == settings.connection_id,
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
                raise AcuityBookingUnavailable()
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
            raise AcuityBookingUnavailable()
        result = create_locked_visit_prep(
            session,
            owner_practice_member_id=owner.id,
            owner_doctor_id=doctor.id,
            practice_id=practice.id,
            idempotency_key=str(uuid4()),
            recipient=booking.recipient,
            physician_display_name=binding.physician_display_name,
            scheduled_at=booking.scheduled_at,
            timezone_name=booking.timezone_name,
            question_set_version_id=version.id,
            creation_source="appointment_booking",
            now=now,
        )
        session.add(
            VisitPrepBookingReceipt(
                connection_id=settings.connection_id,
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
