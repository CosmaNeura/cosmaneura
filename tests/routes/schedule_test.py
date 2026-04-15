from datetime import datetime, timedelta, timezone
from unittest.mock import ANY, patch

from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app

from app.models.doctor import Doctor
from app.models.doctor_video_integration import DoctorVideoIntegration, VideoProvider
from app.models.zoom_meeting import ZoomMeeting, ZoomMeetingStatus

from tests.routes.utils import get_clerk_dev_user

CLERK_DEV_USER_EMAIL = "dev@test.com"


def _get_or_create_doctor():
    """Get the dev doctor, ensuring email is set to avoid Clerk API calls."""
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)
    if not doctor.email:
        doctor.email = CLERK_DEV_USER_EMAIL
        doctor.save()
    return doctor


def _create_zoom_integration(doctor):
    return DoctorVideoIntegration(
        doctor_id=doctor.id,
        provider=VideoProvider.zoom,
        provider_user_id="zoom_user_abc",
        access_token="access_token",
        refresh_token="refresh_token",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ).save()


def _make_meeting(doctor, zoom_meeting_id="111222333", **kwargs):
    defaults = dict(
        doctor_id=doctor.id,
        zoom_meeting_id=zoom_meeting_id,
        topic="Test Meeting",
        start_time=datetime.now(timezone.utc) + timedelta(hours=1),
        duration=30,
        join_url="https://zoom.us/j/111222333",
    )
    defaults.update(kwargs)
    return ZoomMeeting(**defaults).save()


def test_schedule_list_no_zoom(authenticated_client: TestClient):
    """Doctor with no zoom integration returns zoom_connected=False and empty meetings; sync not called."""
    _get_or_create_doctor()

    with patch("app.routes.internal.sync_zoom_meetings") as mock_sync:
        response = authenticated_client.get(api_app.url_path_for("schedule_list"))

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["zoom_connected"] is False
    assert data["meetings"] == []
    mock_sync.queue.assert_not_called()


def test_schedule_list_with_zoom(authenticated_client: TestClient):
    """Doctor with zoom integration: sync is queued and meetings are returned."""
    doctor = _get_or_create_doctor()
    _create_zoom_integration(doctor)
    _make_meeting(doctor)

    with patch("app.routes.internal.sync_zoom_meetings") as mock_sync:
        response = authenticated_client.get(api_app.url_path_for("schedule_list"))

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["zoom_connected"] is True
    assert len(data["meetings"]) == 1
    mock_sync.queue.assert_called_once_with(str(doctor.id))


def test_schedule_list_excludes_cancelled(authenticated_client: TestClient):
    """Cancelled meetings are excluded from results."""
    doctor = _get_or_create_doctor()
    _create_zoom_integration(doctor)

    _make_meeting(doctor, zoom_meeting_id="111222333")
    _make_meeting(doctor, zoom_meeting_id="999888777", status=ZoomMeetingStatus.cancelled)

    with patch("app.routes.internal.sync_zoom_meetings"):
        response = authenticated_client.get(api_app.url_path_for("schedule_list"))

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["zoom_connected"] is True
    assert len(data["meetings"]) == 1
    assert data["meetings"][0]["zoom_meeting_id"] == "111222333"


def test_schedule_cancel(authenticated_client: TestClient):
    """Cancelling a meeting calls zoom_service.delete_meeting and sets status to cancelled."""
    doctor = _get_or_create_doctor()
    _create_zoom_integration(doctor)
    _make_meeting(doctor, zoom_meeting_id="111222333")

    with patch("app.routes.internal.zoom_service.delete_meeting") as mock_delete:
        response = authenticated_client.delete(
            api_app.url_path_for("cancel_meeting", zoom_meeting_id="111222333")
        )

    assert response.status_code == status.HTTP_204_NO_CONTENT
    mock_delete.assert_called_once_with(ANY, "111222333")

    updated = ZoomMeeting.get(doctor_id=doctor.id, zoom_meeting_id="111222333")
    assert updated.status == ZoomMeetingStatus.cancelled


def test_schedule_cancel_not_found(authenticated_client: TestClient):
    """Cancelling a non-existent meeting returns 404."""
    _get_or_create_doctor()

    with patch("app.routes.internal.zoom_service.delete_meeting"):
        response = authenticated_client.delete(
            api_app.url_path_for("cancel_meeting", zoom_meeting_id="000000000")
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND
