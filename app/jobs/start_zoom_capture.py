from datetime import UTC, datetime
from typing import cast

from app import log
from app.celery import celery_app
from app.configuration.redis import get_redis
from app.services import zoom_webhooks
from app.services.external_webhook_inbox import provider_log_ref

from app.models.zoom_meeting import ZoomMeeting


def _redis_text(redis, key: str) -> str | None:
    value = redis.get(key)
    return value.decode() if isinstance(value, bytes) else cast(str | None, value)


def perform(zoom_meeting_record_id: str) -> None:
    meeting = ZoomMeeting.get(id=zoom_meeting_record_id)
    if meeting is None:
        return

    redis = get_redis()
    with zoom_webhooks._lifecycle_lock(redis, meeting.zoom_meeting_id) as launch_lock:
        _perform_locked(meeting, redis, launch_lock=launch_lock)


def _perform_locked(meeting: ZoomMeeting, redis, *, launch_lock=None) -> None:
    meeting_id = meeting.zoom_meeting_id
    occurrence_digest = _redis_text(
        redis, f"recall:meeting:{meeting_id}:occurrence_digest"
    )
    active_doctor_id = _redis_text(redis, f"recall:meeting:{meeting_id}:doctor_id")
    expires_at = meeting.capture_authorization_expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if (
        active_doctor_id != str(meeting.doctor_id)
        or occurrence_digest is None
        or meeting.capture_authorization_occurrence_digest != occurrence_digest
        or expires_at is None
        or expires_at <= datetime.now(UTC)
        or meeting.capture_authorization_consumed_at is not None
    ):
        return

    doctor_present = bool(redis.get(f"recall:meeting:{meeting_id}:doctor_present"))
    raw_patient_count = _redis_text(redis, f"recall:meeting:{meeting_id}:patient_count")
    patient_count = (
        int(raw_patient_count)
        if raw_patient_count is not None and raw_patient_count.isdigit()
        else 0
    )
    if not doctor_present or patient_count < 1:
        log.info(
            "authorized zoom capture waiting for participants",
            event_code="zoom_capture_waiting_for_participants",
            meeting_ref=provider_log_ref(meeting_id),
            doctor_present=doctor_present,
            patient_count=min(patient_count, 10_000),
        )
        return

    zoom_webhooks._resume_or_start_recording_locked(
        redis, meeting_id, "", "", launch_lock=launch_lock
    )


perform_celery = celery_app.task(perform)


def queue(zoom_meeting_record_id: str) -> None:
    perform_celery.delay(zoom_meeting_record_id)
