import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from redis.exceptions import RedisError
from typeid import TypeID
from typeid.errors import TypeIDException

from app import log
from app.configuration.redis import get_redis
from app.services.doctor_access import doctor_access_in_force
from app.services.external_webhook_inbox import (
    digest_provider_reference,
    provider_log_ref,
)

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.zoom_meeting import ZoomMeeting, ZoomMeetingStatus
from sqlmodel import select

_AUTHORIZATION_GRACE = timedelta(minutes=30)
_EARLY_START_GRACE = timedelta(minutes=30)
_MAX_PENDING_TTL = timedelta(days=91)
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ZoomCaptureAuthorizationNotFound(Exception):
    pass


class ZoomCaptureAuthorizationConflict(Exception):
    pass


@dataclass(frozen=True)
class ZoomCaptureAuthorizationResult:
    appointment_id: str
    meeting_record_id: str
    armed: bool


def _redis_text(redis, key: str) -> str | None:
    value = redis.get(key)
    return value.decode() if isinstance(value, bytes) else cast(str | None, value)


def _parse_appointment_id(value: str) -> TypeIDType:
    try:
        parsed = TypeID.from_string(value)
    except TypeIDException as exc:
        raise ZoomCaptureAuthorizationNotFound() from exc
    if parsed.prefix != "appointment":
        raise ZoomCaptureAuthorizationNotFound()
    return cast(TypeIDType, parsed)


def capture_available_for_doctor(doctor: Doctor) -> bool:
    from app.services import zoom_webhooks

    return zoom_webhooks.zoom_bot_email_allowed(doctor.email)


def _validate_meeting(meeting: ZoomMeeting) -> None:
    if meeting.status not in {
        ZoomMeetingStatus.scheduled,
        ZoomMeetingStatus.started,
    }:
        raise ZoomCaptureAuthorizationConflict("meeting is not active")
    if not meeting.zoom_meeting_id or not meeting.zoom_meeting_id.strip():
        raise ZoomCaptureAuthorizationConflict("meeting id is unavailable")
    if not meeting.join_url or not meeting.join_url.strip():
        raise ZoomCaptureAuthorizationConflict("meeting join URL is unavailable")
    if meeting.start_time is None:
        raise ZoomCaptureAuthorizationConflict("meeting start time is unavailable")


def _validate_appointment(
    *,
    session,
    doctor_id: TypeIDType,
    appointment: Appointment | None,
) -> None:
    patient = (
        session.get(Patient, appointment.patient_id)
        if appointment is not None
        else None
    )
    if (
        appointment is None
        or appointment.doctor_id != doctor_id
        or patient is None
        or patient.doctor_id != doctor_id
    ):
        raise ZoomCaptureAuthorizationNotFound()
    if session.exec(
        select(AIVisitTranscript)
        .where(AIVisitTranscript.appointment_id == appointment.id)
        .limit(1)
    ).first():
        raise ZoomCaptureAuthorizationConflict("appointment already has a transcript")


def _active_occurrence_digest(
    *,
    redis,
    doctor_id: TypeIDType,
    meeting_id: str,
) -> str | None:
    active_doctor_id = _redis_text(
        redis,
        f"recall:meeting:{meeting_id}:doctor_id",
    )
    occurrence_digest = _redis_text(
        redis,
        f"recall:meeting:{meeting_id}:occurrence_digest",
    )
    if active_doctor_id != str(doctor_id):
        return None
    if occurrence_digest is None or not _DIGEST_PATTERN.fullmatch(occurrence_digest):
        return None
    return occurrence_digest


def _pending_key(meeting_id: str) -> str:
    return f"recall:meeting:{meeting_id}:capture_authorization_pending"


def _pending_marker(appointment_id: TypeIDType) -> str:
    return digest_provider_reference(f"zoom-authorization:{appointment_id}")


def _authorization_expiry(meeting: ZoomMeeting, *, now: datetime) -> datetime:
    duration = timedelta(minutes=max(meeting.duration or 0, 0))
    return now + duration + _AUTHORIZATION_GRACE


def _pending_ttl(meeting: ZoomMeeting, *, now: datetime) -> timedelta:
    start_time = meeting.start_time
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=UTC)
    else:
        # Normalize before arithmetic so a local-time DST fold cannot add an
        # extra hour to this short-lived authorization window.
        start_time = start_time.astimezone(UTC)
    duration = timedelta(minutes=max(meeting.duration or 0, 0))
    remaining = start_time + duration + _AUTHORIZATION_GRACE - now
    if remaining <= timedelta(0):
        raise ZoomCaptureAuthorizationConflict("scheduled occurrence has ended")
    return min(remaining, _MAX_PENDING_TTL)


def _scheduled_occurrence_window_open(
    meeting: ZoomMeeting,
    *,
    now: datetime,
) -> bool:
    start_time = meeting.start_time
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=UTC)
    else:
        start_time = start_time.astimezone(UTC)
    duration = timedelta(minutes=max(meeting.duration or 0, 0))
    return (
        start_time - _EARLY_START_GRACE
        <= now
        <= start_time + duration + _AUTHORIZATION_GRACE
    )


def capture_is_authorized(meeting: ZoomMeeting) -> bool:
    if meeting.appointment_id is None:
        return False
    if meeting.capture_authorization_occurrence_digest is not None:
        if meeting.capture_authorization_consumed_at is not None:
            return True
        expires_at = meeting.capture_authorization_expires_at
        if expires_at is None:
            return False
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at > datetime.now(UTC)
    if meeting.status == ZoomMeetingStatus.started:
        return False
    try:
        return _redis_text(
            get_redis(),
            _pending_key(meeting.zoom_meeting_id),
        ) == _pending_marker(meeting.appointment_id)
    except RedisError:
        log.warning(
            "zoom capture authorization state unavailable",
            event_code="zoom_capture_authorization_state_unavailable",
            meeting_ref=provider_log_ref(meeting.zoom_meeting_id),
        )
        return False


def authorize_capture_for_appointment(
    *,
    doctor_id: TypeIDType,
    zoom_meeting_id: str,
    appointment_id: str,
) -> ZoomCaptureAuthorizationResult:
    """Bind an explicitly selected appointment and arm its signed occurrence."""

    parsed_appointment_id = _parse_appointment_id(appointment_id)
    now = datetime.now(UTC)
    redis = get_redis()
    pending_ttl: timedelta | None = None

    from app.services import zoom_webhooks

    with zoom_webhooks._lifecycle_lock(redis, zoom_meeting_id):
        active_occurrence = _active_occurrence_digest(
            redis=redis,
            doctor_id=doctor_id,
            meeting_id=zoom_meeting_id,
        )

        with get_session() as session:
            doctor = session.get(Doctor, doctor_id)
            if (
                doctor is None
                or not doctor_access_in_force(doctor)
                or not capture_available_for_doctor(doctor)
            ):
                raise ZoomCaptureAuthorizationNotFound()

            meeting = session.exec(
                select(ZoomMeeting)
                .where(
                    ZoomMeeting.doctor_id == doctor_id,
                    ZoomMeeting.zoom_meeting_id == zoom_meeting_id,
                )
                .with_for_update()
            ).one_or_none()
            if meeting is None:
                raise ZoomCaptureAuthorizationNotFound()
            _validate_meeting(meeting)
            if (
                meeting.status == ZoomMeetingStatus.started
                and active_occurrence is None
            ):
                raise ZoomCaptureAuthorizationConflict(
                    "live occurrence authorization is unavailable"
                )
            if active_occurrence is not None and not _scheduled_occurrence_window_open(
                meeting,
                now=now,
            ):
                raise ZoomCaptureAuthorizationConflict(
                    "live occurrence is outside its scheduled authorization window"
                )

            appointment = session.exec(
                select(Appointment)
                .where(Appointment.id == parsed_appointment_id)
                .with_for_update()
            ).one_or_none()
            _validate_appointment(
                session=session,
                doctor_id=doctor_id,
                appointment=appointment,
            )
            assert appointment is not None

            other_binding = session.exec(
                select(cast(Any, ZoomMeeting.id))
                .where(
                    ZoomMeeting.doctor_id == doctor_id,
                    ZoomMeeting.appointment_id == appointment.id,
                    ZoomMeeting.id != meeting.id,
                )
                .limit(1)
            ).first()
            if other_binding is not None:
                raise ZoomCaptureAuthorizationConflict(
                    "appointment is already bound to another meeting"
                )

            if meeting.capture_authorization_consumed_at is not None:
                raise ZoomCaptureAuthorizationConflict("authorization already consumed")
            if (
                meeting.appointment_id is not None
                and meeting.appointment_id != appointment.id
            ):
                raise ZoomCaptureAuthorizationConflict(
                    "meeting is already bound to another appointment"
                )

            existing_digest = meeting.capture_authorization_occurrence_digest
            if existing_digest is not None:
                if active_occurrence != existing_digest:
                    raise ZoomCaptureAuthorizationConflict(
                        "meeting is authorized for another occurrence"
                    )
                expires_at = meeting.capture_authorization_expires_at
                if expires_at is None:
                    raise ZoomCaptureAuthorizationConflict(
                        "authorization expiry is unavailable"
                    )
                expires_at = (
                    expires_at.replace(tzinfo=UTC)
                    if expires_at.tzinfo is None
                    else expires_at
                )
                if expires_at <= now:
                    meeting.capture_authorization_expires_at = _authorization_expiry(
                        meeting,
                        now=now,
                    )
                    session.add(meeting)
                    session.commit()
            else:
                pending_ttl = (
                    _AUTHORIZATION_GRACE
                    if active_occurrence is not None
                    else _pending_ttl(meeting, now=now)
                )
                meeting.appointment_id = appointment.id
                session.add(meeting)
                session.commit()

            result = ZoomCaptureAuthorizationResult(
                appointment_id=str(appointment.id),
                meeting_record_id=str(meeting.id),
                armed=existing_digest is not None,
            )

        if not result.armed:
            assert pending_ttl is not None
            redis.set(
                _pending_key(zoom_meeting_id),
                _pending_marker(parsed_appointment_id),
                ex=pending_ttl,
            )
            if active_occurrence is not None:
                result = ZoomCaptureAuthorizationResult(
                    appointment_id=result.appointment_id,
                    meeting_record_id=result.meeting_record_id,
                    armed=arm_authorized_occurrence_locked(
                        redis=redis,
                        doctor_id=doctor_id,
                        zoom_meeting_id=zoom_meeting_id,
                        occurrence_digest=active_occurrence,
                    ),
                )

    log.info(
        "zoom capture authorized by provider",
        event_code="zoom_capture_provider_authorized",
        doctor_ref=provider_log_ref(str(doctor_id)),
        meeting_ref=provider_log_ref(zoom_meeting_id),
        appointment_ref=provider_log_ref(result.appointment_id),
        state="armed" if result.armed else "pending",
    )
    return result


def _delete_pending_marker(redis, zoom_meeting_id: str) -> None:
    try:
        redis.delete(_pending_key(zoom_meeting_id))
    except RedisError:
        log.warning(
            "zoom capture pending marker cleanup deferred",
            event_code="zoom_capture_pending_cleanup_deferred",
            meeting_ref=provider_log_ref(zoom_meeting_id),
        )


def arm_authorized_occurrence_locked(
    *,
    redis,
    doctor_id: TypeIDType,
    zoom_meeting_id: str,
    occurrence_digest: str,
) -> bool:
    now = datetime.now(UTC)
    if (
        _redis_text(redis, f"recall:meeting:{zoom_meeting_id}:doctor_id")
        != str(doctor_id)
        or _redis_text(redis, f"recall:meeting:{zoom_meeting_id}:occurrence_digest")
        != occurrence_digest
    ):
        return False

    with get_session() as session:
        doctor = session.get(Doctor, doctor_id)
        if (
            doctor is None
            or not doctor_access_in_force(doctor)
            or not capture_available_for_doctor(doctor)
        ):
            return False

        meeting = session.exec(
            select(ZoomMeeting)
            .where(
                ZoomMeeting.doctor_id == doctor_id,
                ZoomMeeting.zoom_meeting_id == zoom_meeting_id,
            )
            .with_for_update()
        ).one_or_none()
        if meeting is None or meeting.appointment_id is None:
            return False
        try:
            _validate_meeting(meeting)
        except ZoomCaptureAuthorizationConflict:
            return False

        appointment = session.exec(
            select(Appointment)
            .where(Appointment.id == meeting.appointment_id)
            .with_for_update()
        ).one_or_none()
        try:
            _validate_appointment(
                session=session,
                doctor_id=doctor_id,
                appointment=appointment,
            )
        except (
            ZoomCaptureAuthorizationConflict,
            ZoomCaptureAuthorizationNotFound,
        ):
            return False

        if meeting.capture_authorization_consumed_at is not None:
            return False
        existing_digest = meeting.capture_authorization_occurrence_digest
        if existing_digest is not None:
            expires_at = meeting.capture_authorization_expires_at
            if existing_digest != occurrence_digest or expires_at is None:
                return False
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= now:
                return False
            _delete_pending_marker(redis, zoom_meeting_id)
            return True

        pending_marker = _redis_text(redis, _pending_key(zoom_meeting_id))
        if pending_marker != _pending_marker(meeting.appointment_id):
            return False
        if not _scheduled_occurrence_window_open(meeting, now=now):
            return False

        meeting.capture_authorization_occurrence_digest = occurrence_digest
        meeting.capture_authorization_expires_at = _authorization_expiry(
            meeting,
            now=now,
        )
        session.add(meeting)
        session.commit()

    _delete_pending_marker(redis, zoom_meeting_id)
    return True
