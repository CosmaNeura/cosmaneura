from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from app.configuration.redis import get_redis
from app.jobs.start_zoom_capture import perform
from app.services.external_webhook_inbox import digest_provider_reference

from app.models.zoom_meeting import ZoomMeeting, ZoomMeetingStatus

from tests.factories import AppointmentFactory, DoctorFactory


def test_authorized_active_meeting_retry_rechecks_participants_under_lock(
    monkeypatch,
):
    from app.services import zoom_webhooks

    doctor = DoctorFactory.build().save()
    appointment = AppointmentFactory.build(doctor_id=doctor.id).save()
    occurrence_digest = digest_provider_reference("zoom-occurrence:active")
    meeting = ZoomMeeting(
        doctor_id=doctor.id,
        zoom_meeting_id="555111222",
        topic="Synthetic visit",
        start_time=datetime.now(timezone.utc),
        duration=30,
        join_url="https://zoom.us/j/555111222",
        status=ZoomMeetingStatus.started,
        appointment_id=appointment.id,
        capture_authorization_occurrence_digest=occurrence_digest,
        capture_authorization_expires_at=datetime.now(timezone.utc)
        + timedelta(hours=1),
    ).save()
    redis = get_redis()
    doctor_key = f"recall:meeting:{meeting.zoom_meeting_id}:doctor_id"
    occurrence_key = f"recall:meeting:{meeting.zoom_meeting_id}:occurrence_digest"
    doctor_present_key = f"recall:meeting:{meeting.zoom_meeting_id}:doctor_present"
    patient_count_key = f"recall:meeting:{meeting.zoom_meeting_id}:patient_count"
    redis.set(doctor_key, str(doctor.id))
    redis.set(occurrence_key, occurrence_digest)
    redis.set(doctor_present_key, "1")
    redis.set(patient_count_key, "1")
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        zoom_webhooks,
        "_resume_or_start_recording_locked",
        lambda _redis, *args: calls.append(args),
    )

    try:
        perform(str(meeting.id))
        assert calls == [(meeting.zoom_meeting_id, "", "")]

        @contextmanager
        def participant_left_before_lock(*_args):
            redis.delete(patient_count_key)
            yield

        monkeypatch.setattr(
            zoom_webhooks, "_lifecycle_lock", participant_left_before_lock
        )
        calls.clear()
        perform(str(meeting.id))
    finally:
        redis.delete(
            doctor_key,
            occurrence_key,
            doctor_present_key,
            patient_count_key,
        )

    assert calls == []
