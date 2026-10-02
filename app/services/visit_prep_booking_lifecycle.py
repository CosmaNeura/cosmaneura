"""Reconcile current booking state atomically; never deliver or transfer answers."""

import hmac
from collections.abc import Callable
from datetime import datetime
from typing import Any, cast

from app.configuration import visit_prep
from app.services.visit_prep_access import resolve_visit_prep_access
from app.services.visit_prep_booking import BookingReceipt, record_current_booking
from app.services.visit_prep_booking_contract import (
    BookingSourceScope,
    BookingSourceUnavailable,
    CurrentVisitPrepBookingState,
)
from app.services.visit_prep_command_contract import (
    _normalize_recipient,
    _normalized_schedule,
    _recipient_plaintext,
)
from app.services.visit_prep_packages import _database_now
from app.services.visit_preps import (
    _lock_current_lineage,
    _lock_owner_scope,
    _lock_visit_prep,
    _visit_prep_session,
    stage_visit_prep_reschedule,
    stage_visit_prep_revocation,
)
from app.utils.encryption import decrypt_value

from app.models.doctor import Doctor
from app.models.visit_prep_booking import VisitPrepBookingReceipt
from app.models.visit_prep_booking_event import VisitPrepBookingEvent
from sqlmodel import select


def record_current_booking_state(
    *,
    state: CurrentVisitPrepBookingState,
    source: BookingSourceScope,
    require_current_source: Callable[[], None],
) -> BookingReceipt:
    require_current_source()
    with _visit_prep_session() as session:
        now = _database_now(session)
        if (
            not isinstance(state.observed_at, datetime)
            or state.observed_at.utcoffset() is None
            or state.observed_at > now
            or (
                state.active_booking is not None
                and (
                    state.active_booking.booking_digest != state.booking_digest
                    or state.active_booking.source_snapshot_hmac
                    != state.source_snapshot_hmac
                )
            )
        ):
            raise BookingSourceUnavailable()
        owner, practice = _lock_owner_scope(
            session,
            owner_practice_member_id=source.owner_practice_member_id,
            owner_doctor_id=source.owner_doctor_id,
            practice_id=source.practice_id,
            now=now,
        )
        doctor = session.get(Doctor, owner.doctor_id)

        def require_admission():
            require_current_source()
            if (
                doctor is None
                or not resolve_visit_prep_access(
                    doctor,
                    settings=visit_prep.VISIT_PREP_SETTINGS,
                    at=_database_now(session),
                ).can_manage
            ):
                raise BookingSourceUnavailable()

        require_admission()
        receipt = session.exec(
            select(VisitPrepBookingReceipt)
            .where(
                VisitPrepBookingReceipt.connection_id == source.connection_id,
                VisitPrepBookingReceipt.booking_digest == state.booking_digest,
            )
            .with_for_update()
        ).one_or_none()
        if receipt is None:
            require_admission()
            if state.active_booking is None:
                return BookingReceipt("", "not_found")
            # Release these read-only locks before the existing creator owns its
            # transaction. A concurrent creation is fenced by the source key.
            session.rollback()
            return record_current_booking(
                booking=state.active_booking,
                source=source,
                require_current_source=require_current_source,
            )
        if receipt.owner_practice_member_id != owner.id:
            raise BookingSourceUnavailable()
        prep = _lock_visit_prep(
            session,
            owner_practice_member_id=owner.id,
            visit_prep_id=receipt.visit_prep_id,
        )
        if (
            prep.practice_id != practice.id
            or prep.owner_doctor_id != source.owner_doctor_id
        ):
            raise BookingSourceUnavailable()
        schedule, credential = _lock_current_lineage(session, visit_prep=prep)
        prior = session.exec(
            select(VisitPrepBookingEvent)
            .where(VisitPrepBookingEvent.booking_receipt_id == receipt.id)
            .order_by(cast(Any, VisitPrepBookingEvent.sequence).desc())
            .limit(1)
        ).one_or_none()
        prior_time = prior.recorded_at if prior else receipt.recorded_at
        prior_snapshot = prior.source_snapshot_hmac if prior else receipt.snapshot_hmac
        # The adapter reads DB time BEFORE provider I/O. A newer reconciliation
        # wins even if an older API response finishes later; retry must re-fetch.
        if state.observed_at < prior_time:
            raise BookingSourceUnavailable()
        if hmac.compare_digest(state.source_snapshot_hmac, prior_snapshot):
            if prior is not None and prior.action == "recipient_changed":
                if (
                    prep.revoked_at is None
                    or prep.revocation_reason != "physician_revoked"
                    or credential.revoked_at is None
                    or schedule.id != prior.schedule_generation_id
                    or credential.id != prior.credential_generation_id
                ):
                    raise BookingSourceUnavailable()
            elif state.active_booking is not None:
                current_at, current_zone = _normalized_schedule(
                    state.active_booking.scheduled_at,
                    state.active_booking.timezone_name,
                    now=_database_now(session),
                    require_future=False,
                )
                if (
                    schedule.scheduled_at != current_at
                    or schedule.timezone_name != current_zone
                ):
                    # A physician-side change is not proof the source agrees.
                    # Keep the conflict explicit rather than acknowledge parity.
                    raise BookingSourceUnavailable()
            elif (
                prep.revoked_at is None
                or prep.revocation_reason != "appointment_cancelled"
            ):
                raise BookingSourceUnavailable()
            require_admission()
            return BookingReceipt(str(prep.id), "replayed")

        args = {
            "owner_practice_member_id": owner.id,
            "owner_doctor_id": source.owner_doctor_id,
            "practice_id": practice.id,
            "visit_prep_id": prep.id,
            "expected_credential_generation": credential.generation,
            "now": _database_now(session),
        }
        if state.active_booking is None:
            action = "cancelled"
            stage_visit_prep_revocation(session, **args, reason="appointment_cancelled")
        else:
            booking = state.active_booking
            recipient = _recipient_plaintext(_normalize_recipient(booking.recipient))
            if not hmac.compare_digest(
                decrypt_value(prep.recipient_snapshot_ciphertext).encode("utf-8"),
                recipient.encode("utf-8"),
            ):
                # A changed contact is not authorization to transfer patient
                # answers. Retain the original, revoke access, require recovery.
                action = "recipient_changed"
                stage_visit_prep_revocation(session, **args, reason="physician_revoked")
            else:
                current_at, current_zone = _normalized_schedule(
                    booking.scheduled_at,
                    booking.timezone_name,
                    now=_database_now(session),
                    require_future=False,
                )
                if (
                    schedule.scheduled_at == current_at
                    and schedule.timezone_name == current_zone
                ):
                    raise BookingSourceUnavailable()
                action = "rescheduled"
                stage_visit_prep_reschedule(
                    session,
                    **args,
                    expected_schedule_generation=schedule.generation,
                    scheduled_at=booking.scheduled_at,
                    timezone_name=booking.timezone_name,
                )
        schedule, credential = _lock_current_lineage(session, visit_prep=prep)
        session.add(
            VisitPrepBookingEvent(
                booking_receipt_id=receipt.id,
                visit_prep_id=prep.id,
                owner_practice_member_id=owner.id,
                question_set_version_id=receipt.question_set_version_id,
                schedule_generation_id=schedule.id,
                credential_generation_id=credential.id,
                sequence=(prior.sequence if prior else 0) + 1,
                action=action,
                prior_snapshot_hmac=prior_snapshot,
                source_snapshot_hmac=state.source_snapshot_hmac,
                observed_at=state.observed_at,
                recorded_at=_database_now(session),
            )
        )
        session.flush()
        require_admission()
        session.commit()
        return BookingReceipt(str(prep.id), action)
