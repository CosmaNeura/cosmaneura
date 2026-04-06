from datetime import datetime, timezone
from unittest.mock import patch

from tests.factories import DoctorFactory
from app.jobs.sync_zoom_meetings import perform
from app.models.zoom_meeting import ZoomMeeting, ZoomMeetingStatus


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
    doctor = DoctorFactory.build(zoom_user_id="zoom_user_abc", zoom_access_token="tok").save()

    with patch("app.jobs.sync_zoom_meetings.zoom_service.get_user_meetings", return_value=FAKE_MEETINGS):
        perform(str(doctor.id))

    meetings = list(ZoomMeeting.where(ZoomMeeting.doctor_id == doctor.id).all())
    assert len(meetings) == 2
    topics = {m.topic for m in meetings}
    assert topics == {"Patient Follow-up", "New Patient Intake"}


def test_sync_upserts_existing_meetings(global_session):
    doctor = DoctorFactory.build(zoom_user_id="zoom_user_abc", zoom_access_token="tok").save()

    # pre-create one meeting
    ZoomMeeting(
        doctor_id=doctor.id,
        zoom_meeting_id="123456789",
        topic="Old Topic",
        start_time=datetime(2026, 4, 7, 14, 0, tzinfo=timezone.utc),
        duration=30,
        join_url="https://zoom.us/j/123456789",
    ).save()

    with patch("app.jobs.sync_zoom_meetings.zoom_service.get_user_meetings", return_value=FAKE_MEETINGS):
        perform(str(doctor.id))

    meetings = list(ZoomMeeting.where(ZoomMeeting.doctor_id == doctor.id).all())
    assert len(meetings) == 2  # not duplicated

    updated = ZoomMeeting.get(doctor_id=doctor.id, zoom_meeting_id="123456789")
    assert updated.topic == "Patient Follow-up"  # updated from Zoom


def test_sync_preserves_started_status(global_session):
    doctor = DoctorFactory.build(zoom_user_id="zoom_user_abc", zoom_access_token="tok").save()

    ZoomMeeting(
        doctor_id=doctor.id,
        zoom_meeting_id="123456789",
        topic="Patient Follow-up",
        start_time=datetime(2026, 4, 7, 14, 0, tzinfo=timezone.utc),
        duration=30,
        join_url="https://zoom.us/j/123456789",
        status=ZoomMeetingStatus.started,
    ).save()

    with patch("app.jobs.sync_zoom_meetings.zoom_service.get_user_meetings", return_value=FAKE_MEETINGS):
        perform(str(doctor.id))

    meeting = ZoomMeeting.get(doctor_id=doctor.id, zoom_meeting_id="123456789")
    assert meeting.status == ZoomMeetingStatus.started  # not reset to scheduled


def test_sync_skips_doctor_without_zoom(global_session):
    doctor = DoctorFactory.build(zoom_user_id=None).save()

    with patch("app.jobs.sync_zoom_meetings.zoom_service.get_user_meetings") as mock_get:
        perform(str(doctor.id))
        mock_get.assert_not_called()
