import json
from datetime import datetime, timedelta, timezone
from typing import cast
from unittest.mock import patch

from structlog.testing import capture_logs

from app.jobs.sync_zoom_meetings import perform

from app.models.doctor_video_integration import DoctorVideoIntegration, VideoProvider
from app.models.zoom_meeting import ZoomMeeting, ZoomMeetingStatus

from tests.factories import AppointmentFactory, DoctorFactory
from tests.routes.utils import provision_practice_in_force


def _create_zoom_integration(doctor):
    return DoctorVideoIntegration(
        doctor_id=doctor.id,
        provider=VideoProvider.zoom,
        provider_user_id="zoom_user_abc",
        access_token="access_token",
        refresh_token="refresh_token",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ).save()


def _authorized_doctor():
    doctor = DoctorFactory.build().save()
    provision_practice_in_force(doctor)
    return doctor


FAKE_MEETINGS = [
    {
        "id": "123456789",
        "topic": "Patient Follow-up",
        "start_time": "2026-04-07T14:00:00Z",
        "duration": 30,
        "join_url": "https://zoom.us/j/123456789",
    },
    {
        "id": "987654321",
        "topic": "New Patient Intake",
        "start_time": "2026-04-07T16:30:00Z",
        "duration": 60,
        "join_url": "https://zoom.us/j/987654321",
    },
]


def test_sync_creates_zoom_meetings(global_session):
    doctor = _authorized_doctor()
    _create_zoom_integration(doctor)

    with (
        patch(
            "app.jobs.sync_zoom_meetings.zoom_service.get_user_meetings",
            return_value=FAKE_MEETINGS,
        ),
        capture_logs() as logs,
    ):
        perform(str(doctor.id))

    meetings = cast(
        list[ZoomMeeting],
        list(ZoomMeeting.where(ZoomMeeting.doctor_id == doctor.id).all()),
    )
    assert len(meetings) == 2
    topics = {m.topic for m in meetings}
    assert topics == {"Patient Follow-up", "New Patient Intake"}

    serialized = json.dumps(logs, sort_keys=True)
    for private_value in (
        str(doctor.id),
        "zoom_user_abc",
        "123456789",
        "987654321",
        "Patient Follow-up",
        "New Patient Intake",
        "https://zoom.us/j/123456789",
    ):
        assert private_value not in serialized
    assert "hmac:" in serialized
    assert any(entry.get("event_code") == "zoom_sync_completed" for entry in logs)


def test_sync_upserts_existing_meetings(global_session):
    doctor = _authorized_doctor()
    _create_zoom_integration(doctor)
    appointment = AppointmentFactory.build(doctor_id=doctor.id).save()
    authorization_digest = "a" * 64
    authorization_expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    # pre-create one meeting
    ZoomMeeting(
        doctor_id=doctor.id,
        zoom_meeting_id="123456789",
        topic="Old Topic",
        start_time=datetime(2026, 4, 7, 14, 0, tzinfo=timezone.utc),
        duration=30,
        join_url="https://zoom.us/j/123456789",
        appointment_id=appointment.id,
        capture_authorization_occurrence_digest=authorization_digest,
        capture_authorization_expires_at=authorization_expiry,
    ).save()

    with patch(
        "app.jobs.sync_zoom_meetings.zoom_service.get_user_meetings",
        return_value=FAKE_MEETINGS,
    ):
        perform(str(doctor.id))

    meetings = cast(
        list[ZoomMeeting],
        list(ZoomMeeting.where(ZoomMeeting.doctor_id == doctor.id).all()),
    )
    assert len(meetings) == 2  # not duplicated

    updated = ZoomMeeting.get(doctor_id=doctor.id, zoom_meeting_id="123456789")
    assert updated is not None
    assert updated.topic == "Patient Follow-up"  # updated from Zoom
    assert updated.appointment_id == appointment.id
    assert updated.capture_authorization_occurrence_digest == authorization_digest
    assert updated.capture_authorization_expires_at == authorization_expiry


def test_sync_preserves_started_status(global_session):
    doctor = _authorized_doctor()
    _create_zoom_integration(doctor)

    ZoomMeeting(
        doctor_id=doctor.id,
        zoom_meeting_id="123456789",
        topic="Patient Follow-up",
        start_time=datetime(2026, 4, 7, 14, 0, tzinfo=timezone.utc),
        duration=30,
        join_url="https://zoom.us/j/123456789",
        status=ZoomMeetingStatus.started,
    ).save()

    with patch(
        "app.jobs.sync_zoom_meetings.zoom_service.get_user_meetings",
        return_value=FAKE_MEETINGS,
    ):
        perform(str(doctor.id))

    meeting = ZoomMeeting.get(doctor_id=doctor.id, zoom_meeting_id="123456789")
    assert meeting is not None
    assert meeting.status == ZoomMeetingStatus.started  # not reset to scheduled


def test_sync_skips_doctor_without_zoom(global_session):
    doctor = _authorized_doctor()

    with patch(
        "app.jobs.sync_zoom_meetings.zoom_service.get_user_meetings"
    ) as mock_get:
        perform(str(doctor.id))
        mock_get.assert_not_called()


# A recurring meeting with no fixed time (Zoom type 3): no start_time, no
# duration. This shape raised KeyError('start_time') in production from
# 2026-07-29, killing the sync mid-loop.
RECURRING_NO_FIXED_TIME = {
    "id": "555000555",
    "topic": "Open Office Hours",
    "type": 3,
    "join_url": "https://zoom.us/j/555000555",
}


def test_sync_skips_recurring_meeting_without_start_time(global_session):
    doctor = _authorized_doctor()
    _create_zoom_integration(doctor)

    with patch(
        "app.jobs.sync_zoom_meetings.zoom_service.get_user_meetings",
        return_value=[RECURRING_NO_FIXED_TIME],
    ):
        perform(str(doctor.id))

    assert ZoomMeeting.where(ZoomMeeting.doctor_id == doctor.id).count() == 0


def test_sync_continues_past_untimed_meeting(global_session):
    """Regression: the untimed meeting sits *between* two timed ones, so a
    raise here would persist the first and silently drop the last —
    truncating the agenda instead of failing loudly."""
    doctor = _authorized_doctor()
    _create_zoom_integration(doctor)

    payload = [FAKE_MEETINGS[0], RECURRING_NO_FIXED_TIME, FAKE_MEETINGS[1]]

    with patch(
        "app.jobs.sync_zoom_meetings.zoom_service.get_user_meetings",
        return_value=payload,
    ):
        perform(str(doctor.id))

    meetings = cast(
        list[ZoomMeeting],
        list(ZoomMeeting.where(ZoomMeeting.doctor_id == doctor.id).all()),
    )
    assert {m.topic for m in meetings} == {"Patient Follow-up", "New Patient Intake"}
    assert "Open Office Hours" not in {m.topic for m in meetings}


def test_sync_tolerates_missing_optional_fields(global_session):
    "topic/duration/join_url must not be able to abort the sync either."
    doctor = _authorized_doctor()
    _create_zoom_integration(doctor)

    sparse = {"id": "777000777", "start_time": "2026-04-07T18:00:00Z"}

    with patch(
        "app.jobs.sync_zoom_meetings.zoom_service.get_user_meetings",
        return_value=[sparse],
    ):
        perform(str(doctor.id))

    meeting = cast(
        ZoomMeeting,
        ZoomMeeting.get(doctor_id=doctor.id, zoom_meeting_id="777000777"),
    )
    assert meeting is not None
    assert meeting.duration == 0


def test_sync_skips_revoked_doctor_before_provider_call(global_session):
    from app.models.practice import PracticeMember

    doctor = _authorized_doctor()
    _create_zoom_integration(doctor)
    member = PracticeMember.get(doctor_id=doctor.id)
    assert member is not None
    now = datetime.now(timezone.utc)
    member.access_revoked_at = now
    member.access_revoked_by_ref = "test-suite"
    member.access_revocation_reason = "Test offboarding"
    member.access_records_retain_until = now + timedelta(days=365 * 8)
    member.save()

    with patch(
        "app.jobs.sync_zoom_meetings.zoom_service.get_user_meetings"
    ) as mock_get:
        perform(str(doctor.id))

    mock_get.assert_not_called()
    assert ZoomMeeting.where(ZoomMeeting.doctor_id == doctor.id).count() == 0
