from datetime import datetime, timedelta
from typing import Any, cast

from app import log
from app.configuration.redis import get_redis
from app.services import recall
from app.services import zoom as zoom_service

from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.doctor_video_integration import DoctorVideoIntegration, VideoProvider
from app.models.patient import Patient
from app.models.zoom_meeting import ZoomMeeting, ZoomMeetingStatus
from sqlalchemy import select

BOT_NAME = "Cosma"


def handle_meeting_started(payload: dict[str, Any]):
    obj = payload.get("payload", {}).get("object", {})
    host_id = obj.get("host_id")
    meeting_id = obj.get("id")

    log.info("meeting.started received", host_id=host_id, meeting_id=meeting_id)

    redis = get_redis()
    # Atomic idempotency guard. Zoom occasionally fires meeting.started twice
    # for the same session (personal-meeting-room quirks, waiting-room
    # reactivation) which would otherwise create a duplicate bot. SET NX
    # succeeds only when the lock does not exist; handle_meeting_ended
    # releases it so a legitimate end+restart past the TTL still works.
    lock_key = f"recall:meeting:{meeting_id}:starting_lock"
    if not redis.set(lock_key, "1", nx=True, ex=60):
        log.info(
            "meeting.started duplicate within active lock window, skipping",
            meeting_id=meeting_id,
        )
        return

    doctor = cast(
        Doctor | None,
        Doctor.where(
            Doctor.id
            == select(cast(Any, DoctorVideoIntegration.doctor_id))
            .where(
                (DoctorVideoIntegration.provider == VideoProvider.zoom)
                & (DoctorVideoIntegration.provider_user_id == host_id)
            )
            .scalar_subquery()
        ).first(),
    )
    if not doctor:
        log.info(
            "no doctor found for zoom host_id, skipping session registration",
            host_id=host_id,
        )
        return

    integration = zoom_service.get_zoom_integration(doctor)
    if not integration:
        log.info(
            "doctor has no zoom integration, skipping session registration",
            doctor_id=str(doctor.id),
        )
        return

    # Clear any orphaned state from a previous session with this meeting_id.
    # Personal meeting rooms reuse the same ID, so meeting.ended may not have
    # fired (or may have been missed) before the next meeting.started arrives.
    # Preserve the lock we just acquired — it gates duplicate meeting.started
    # for the rest of this session.
    _clear_meeting_keys(redis, meeting_id, preserve_lock=True)
    log.info("cleared stale meeting keys", meeting_id=meeting_id)

    ttl = timedelta(hours=24)
    redis.set(f"recall:meeting:{meeting_id}:doctor_id", str(doctor.id), ex=ttl)
    redis.set(f"recall:meeting:{meeting_id}:host_id", host_id, ex=ttl)

    log.info(
        "meeting session started, awaiting doctor",
        meeting_id=meeting_id,
        doctor_id=str(doctor.id),
    )

    zoom_mtg = ZoomMeeting.get(doctor_id=doctor.id, zoom_meeting_id=str(meeting_id))
    if zoom_mtg and zoom_mtg.status == ZoomMeetingStatus.scheduled:
        zoom_mtg.status = ZoomMeetingStatus.started
        zoom_mtg.save()


def _rget(redis, key: str) -> str | None:
    val = redis.get(key)
    return val.decode() if isinstance(val, bytes) else val


def _is_doctor_or_bot(participant: dict, meeting_id: str) -> bool:
    user_name = participant.get("user_name", "")
    if user_name == BOT_NAME:
        return True
    redis = get_redis()
    doctor_name = _rget(redis, f"recall:meeting:{meeting_id}:doctor_name")
    return bool(doctor_name) and user_name == doctor_name


def handle_participant_joined(payload: dict[str, Any]):
    obj = payload.get("payload", {}).get("object", {})
    meeting_id = obj.get("id")
    participant = obj.get("participant", {})

    if _is_doctor_or_bot(participant, meeting_id):
        return

    redis = get_redis()
    bot_id = _rget(redis, f"recall:meeting:{meeting_id}:bot_id")
    doctor_id = _rget(redis, f"recall:meeting:{meeting_id}:doctor_id")

    if not bot_id:
        log.warning(
            "no bot found for meeting, cannot start recording", meeting_id=meeting_id
        )
        return

    count_key = f"recall:meeting:{meeting_id}:patient_count"
    count = cast(int, redis.incr(count_key))
    redis.expire(count_key, int(timedelta(hours=24).total_seconds()))

    if count != 1:
        log.info(
            "additional patient joined, already recording",
            meeting_id=meeting_id,
            count=count,
        )
        return

    now = datetime.utcnow()
    appointment = cast(
        Appointment | None,
        Appointment.where(
            (Appointment.doctor_id == doctor_id)
            & (Appointment.appointment_date >= now - timedelta(hours=4))
            & (Appointment.appointment_date <= now + timedelta(hours=1))
        )
        .order_by(cast(Any, Appointment.appointment_date).desc())
        .first(),
    )

    recording_data = recall.start_recording(bot_id)
    recording_id = recording_data.get("id")

    log.info(
        "patient joined, started recording",
        meeting_id=meeting_id,
        bot_id=bot_id,
        recording_id=recording_id,
        patient_name=participant.get("user_name"),
    )

    if not recording_id:
        return

    if not appointment:
        assert doctor_id, "doctor_id must be set to create appointment"
        appointment = _ensure_appointment(
            doctor_id=doctor_id,
            patient_name=participant.get("user_name") or "Unknown",
            now=now,
        )

    ttl = timedelta(hours=24)
    redis.set(f"recall:recording:{recording_id}:doctor_id", doctor_id or "", ex=ttl)
    redis.set(
        f"recall:recording:{recording_id}:appointment_id", str(appointment.id), ex=ttl
    )
    redis.set(
        f"recall:recording:{recording_id}:patient_id",
        str(appointment.patient_id),
        ex=ttl,
    )
    # also key by bot_id since recording_id from start_recording != real recording_id in webhooks
    redis.set(f"recall:bot:{bot_id}:appointment_id", str(appointment.id), ex=ttl)
    redis.set(f"recall:bot:{bot_id}:patient_id", str(appointment.patient_id), ex=ttl)

    # Link ZoomMeeting to Appointment if a pre-synced record exists
    if doctor_id:
        zoom_mtg = ZoomMeeting.get(doctor_id=doctor_id, zoom_meeting_id=str(meeting_id))
        if zoom_mtg:
            zoom_mtg.appointment_id = appointment.id
            zoom_mtg.status = ZoomMeetingStatus.started
            zoom_mtg.save()


def _ensure_appointment(
    doctor_id: str, patient_name: str, now: datetime
) -> Appointment:
    """Find or create a Patient + Appointment for an ad-hoc Zoom session."""
    patient = cast(
        Patient | None,
        Patient.where(
            (Patient.doctor_id == doctor_id) & (Patient.name == patient_name)
        ).first(),
    )
    if not patient:
        # date_of_birth is required — use sentinel; doctor should update later
        patient = Patient.find_or_create_by(
            doctor_id=doctor_id,
            name=patient_name,
            date_of_birth=datetime(1900, 1, 1),
        )
        log.info(
            "created patient for zoom participant",
            doctor_id=doctor_id,
            patient_name=patient_name,
        )

    appointment = Appointment.find_or_create_by(
        doctor_id=doctor_id,
        patient_id=patient.id,
        appointment_date=now,
        created_by_system="recall",
    )
    log.info(
        "created appointment for zoom session",
        doctor_id=doctor_id,
        appointment_id=str(appointment.id),
    )
    return cast(Appointment, appointment)


def handle_participant_left(payload: dict[str, Any]):
    """Decrement the patient counter only. The bot lifecycle is driven by
    meeting.ended, never by participant_left — third-party note-takers or a
    patient who rejoins should not pull the bot out of the call.
    """
    obj = payload.get("payload", {}).get("object", {})
    meeting_id = obj.get("id")
    participant = obj.get("participant", {})

    if _is_doctor_or_bot(participant, meeting_id):
        return

    redis = get_redis()
    bot_id = _rget(redis, f"recall:meeting:{meeting_id}:bot_id")
    if not bot_id:
        return

    count_key = f"recall:meeting:{meeting_id}:patient_count"
    count = cast(int, redis.decr(count_key))

    if count < 0:
        # Out-of-order events: participant_left arrived without a prior
        # participant_joined. Reset to 0 so future increments stay sane.
        log.warning(
            "patient_count went negative, resetting to 0",
            meeting_id=meeting_id,
            count=count,
        )
        redis.set(count_key, 0)
        return

    log.info(
        "patient left, recording continues until meeting ends",
        meeting_id=meeting_id,
        count=count,
        patient_name=participant.get("user_name"),
    )


def _clear_meeting_keys(redis, meeting_id: str, preserve_lock: bool = False) -> None:
    """Delete all per-meeting Redis state. Called on meeting.ended so keys
    don't linger for the full 24h TTL — especially important for personal
    meeting rooms that reuse the same meeting_id across sessions.

    preserve_lock keeps the starting_lock in place when we are mid-session
    (e.g. cleaning orphan state during handle_meeting_started) so duplicate
    meeting.started webhooks for the same session still get deduped.
    """
    suffixes = [
        "bot_id", "doctor_id", "host_id", "doctor_present", "recording_active",
        "participants_order", "participants_names", "patient_count",
        "grace_task_id",
    ]
    if not preserve_lock:
        suffixes.append("starting_lock")
    for suffix in suffixes:
        redis.delete(f"recall:meeting:{meeting_id}:{suffix}")


def handle_meeting_ended(payload: dict[str, Any]):
    """meeting.ended is the canonical wind-down signal for the bot.
    Stop the recording whenever a bot exists for this meeting, regardless of
    the patient counter — participant_left no longer stops recording, so the
    counter is no longer authoritative.
    """
    obj = payload.get("payload", {}).get("object", {})
    meeting_id = obj.get("id")

    redis = get_redis()
    bot_id = _rget(redis, f"recall:meeting:{meeting_id}:bot_id")

    _clear_meeting_keys(redis, meeting_id)

    if bot_id:
        log.info(
            "meeting ended, stopping recording",
            meeting_id=meeting_id,
            bot_id=bot_id,
        )
        recall.stop_recording(bot_id)
    else:
        log.info(
            "meeting ended, no bot tracked for this meeting",
            meeting_id=meeting_id,
        )

    zoom_mtg = cast(
        ZoomMeeting | None,
        ZoomMeeting.where(
            cast(Any, ZoomMeeting.zoom_meeting_id) == str(meeting_id)
        ).first(),
    )
    if zoom_mtg:
        zoom_mtg.status = ZoomMeetingStatus.ended
        zoom_mtg.save()
        log.info(
            "zoom meeting status updated to ended", zoom_meeting_id=str(meeting_id)
        )


HANDLERS = {
    "meeting.started": handle_meeting_started,
    "meeting.participant_joined": handle_participant_joined,
    "meeting.participant_left": handle_participant_left,
    "meeting.ended": handle_meeting_ended,
}


def dispatch(event_type: str, payload: dict[str, Any]):
    handler = HANDLERS.get(event_type)
    if not handler:
        log.info("no handler for zoom event type", event_type=event_type)
        return
    handler(payload)
