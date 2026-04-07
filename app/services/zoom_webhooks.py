from datetime import datetime, timedelta
from typing import Any, cast

from app import log
from app.configuration.redis import get_redis
from app.services import recall
from app.services import zoom as zoom_service

from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient

BOT_NAME = "Cosma"


def handle_meeting_started(payload: dict[str, Any]):
    obj = payload.get("payload", {}).get("object", {})
    host_id = obj.get("host_id")
    meeting_id = obj.get("id")

    log.info("meeting.started received", host_id=host_id, meeting_id=meeting_id)

    doctor = cast(
        Doctor | None,
        Doctor.where(Doctor.zoom_user_id == host_id).first(),
    )
    if not doctor:
        log.info("no doctor found for zoom host_id, skipping bot creation", host_id=host_id)
        return

    if not doctor.zoom_access_token:
        log.info("doctor has no zoom access token, skipping bot creation", doctor_id=str(doctor.id))
        return

    redis = get_redis()
    existing_bot_id = redis.get(f"recall:meeting:{meeting_id}:bot_id")
    if existing_bot_id:
        log.info("bot already in meeting, skipping creation", meeting_id=meeting_id, bot_id=existing_bot_id)
        return

    zak_token = zoom_service.get_zak_token(doctor)
    meeting_url = zoom_service.get_meeting_join_url(doctor, meeting_id)
    bot_data = recall.create_bot(meeting_url=meeting_url, zak_token=zak_token, manual_recording=True)
    bot_id = bot_data["id"]

    doctor_name = zoom_service.get_user_display_name(doctor)

    ttl = timedelta(hours=24)
    redis.set(f"recall:meeting:{meeting_id}:bot_id", bot_id, ex=ttl)
    redis.set(f"recall:meeting:{meeting_id}:doctor_id", str(doctor.id), ex=ttl)
    redis.set(f"recall:meeting:{meeting_id}:host_id", host_id, ex=ttl)
    redis.set(f"recall:meeting:{meeting_id}:doctor_name", doctor_name, ex=ttl)
    redis.set(f"recall:bot:{bot_id}:doctor_id", str(doctor.id), ex=ttl)
    log.info("recall bot created, doctor identified", bot_id=bot_id, doctor_name=doctor_name)


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
        log.warning("no bot found for meeting, cannot start recording", meeting_id=meeting_id)
        return

    count_key = f"recall:meeting:{meeting_id}:patient_count"
    count = cast(int, redis.incr(count_key))
    redis.expire(count_key, int(timedelta(hours=24).total_seconds()))

    if count != 1:
        log.info("additional patient joined, already recording", meeting_id=meeting_id, count=count)
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
    redis.set(f"recall:recording:{recording_id}:appointment_id", str(appointment.id), ex=ttl)
    redis.set(f"recall:recording:{recording_id}:patient_id", str(appointment.patient_id), ex=ttl)
    # also key by bot_id since recording_id from start_recording != real recording_id in webhooks
    redis.set(f"recall:bot:{bot_id}:appointment_id", str(appointment.id), ex=ttl)
    redis.set(f"recall:bot:{bot_id}:patient_id", str(appointment.patient_id), ex=ttl)


def _ensure_appointment(doctor_id: str, patient_name: str, now: datetime) -> Appointment:
    """Find or create a Patient + Appointment for an ad-hoc Zoom session."""
    patient = cast(
        Patient | None,
        Patient.where((Patient.doctor_id == doctor_id) & (Patient.name == patient_name)).first(),
    )
    if not patient:
        # date_of_birth is required — use sentinel; doctor should update later
        patient = Patient.find_or_create_by(
            doctor_id=doctor_id,
            name=patient_name,
            date_of_birth=datetime(1900, 1, 1),
        )
        log.info("created patient for zoom participant", doctor_id=doctor_id, patient_name=patient_name)

    appointment = Appointment.find_or_create_by(
        doctor_id=doctor_id,
        patient_id=patient.id,
        appointment_date=now,
        created_by_system="recall",
    )
    log.info("created appointment for zoom session", doctor_id=doctor_id, appointment_id=str(appointment.id))
    return cast(Appointment, appointment)


def handle_participant_left(payload: dict[str, Any]):
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

    if count > 0:
        log.info("patient left but others still present, still recording", meeting_id=meeting_id, count=count)
        return

    redis.delete(count_key)
    recall.stop_recording(bot_id)
    log.info("last patient left, stopped recording", meeting_id=meeting_id, bot_id=bot_id)


def handle_meeting_ended(payload: dict[str, Any]):
    obj = payload.get("payload", {}).get("object", {})
    meeting_id = obj.get("id")

    redis = get_redis()
    count_key = f"recall:meeting:{meeting_id}:patient_count"
    count_str = _rget(redis, count_key)
    count = int(count_str) if count_str else 0

    redis.delete(count_key)

    if count <= 0:
        log.info("meeting ended, no active recording to stop", meeting_id=meeting_id)
        return

    bot_id = _rget(redis, f"recall:meeting:{meeting_id}:bot_id")
    if not bot_id:
        return

    log.info("meeting ended with active recording, stopping", meeting_id=meeting_id, bot_id=bot_id, count=count)
    recall.stop_recording(bot_id)


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
