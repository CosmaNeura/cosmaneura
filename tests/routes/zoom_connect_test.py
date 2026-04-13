from unittest.mock import patch

from fastapi import status
from fastapi.testclient import TestClient

from app.models.doctor import Doctor
from app.models.doctor_video_integration import DoctorVideoIntegration, VideoProvider
from app.server import api_app

from tests.routes.utils import get_clerk_dev_user

CLERK_DEV_USER_EMAIL = "dev@test.com"


def _get_or_create_doctor():
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)
    if not doctor.email:
        doctor.email = CLERK_DEV_USER_EMAIL
        doctor.save()
    return doctor


def test_zoom_connect_success(authenticated_client: TestClient):
    """Valid Zoom email creates a DoctorVideoIntegration record."""
    doctor = _get_or_create_doctor()

    with patch("app.routes.internal.zoom_service.get_user_by_email", return_value={"id": "zoom_abc123"}):
        response = authenticated_client.post(
            api_app.url_path_for("zoom_connect"),
            json={"email": "doctor@example.com"},
        )

    assert response.status_code == status.HTTP_200_OK
    integration = DoctorVideoIntegration.get(doctor_id=doctor.id, provider=VideoProvider.zoom)
    assert integration is not None
    assert integration.provider_user_id == "zoom_abc123"


def test_zoom_connect_unknown_email(authenticated_client: TestClient):
    """Email not in Zoom account returns 422 with a clear message."""
    with patch("app.routes.internal.zoom_service.get_user_by_email", return_value=None):
        response = authenticated_client.post(
            api_app.url_path_for("zoom_connect"),
            json={"email": "unknown@example.com"},
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "No Zoom user found" in response.json()["detail"]


def test_zoom_connect_replaces_existing(authenticated_client: TestClient):
    """Connecting when already connected updates the provider_user_id."""
    doctor = _get_or_create_doctor()
    DoctorVideoIntegration(
        doctor_id=doctor.id,
        provider=VideoProvider.zoom,
        provider_user_id="old_zoom_id",
    ).save()

    with patch("app.routes.internal.zoom_service.get_user_by_email", return_value={"id": "new_zoom_id"}):
        response = authenticated_client.post(
            api_app.url_path_for("zoom_connect"),
            json={"email": "doctor@example.com"},
        )

    assert response.status_code == status.HTTP_200_OK
    integration = DoctorVideoIntegration.get(doctor_id=doctor.id, provider=VideoProvider.zoom)
    assert integration.provider_user_id == "new_zoom_id"


def test_zoom_disconnect_success(authenticated_client: TestClient):
    """Disconnecting removes the DoctorVideoIntegration record."""
    doctor = _get_or_create_doctor()
    DoctorVideoIntegration(
        doctor_id=doctor.id,
        provider=VideoProvider.zoom,
        provider_user_id="zoom_abc123",
    ).save()

    response = authenticated_client.delete(api_app.url_path_for("zoom_disconnect"))

    assert response.status_code == status.HTTP_204_NO_CONTENT
    integration = DoctorVideoIntegration.get(doctor_id=doctor.id, provider=VideoProvider.zoom)
    assert integration is None


def test_zoom_disconnect_not_connected(authenticated_client: TestClient):
    """Disconnecting when not connected returns 404."""
    response = authenticated_client.delete(api_app.url_path_for("zoom_disconnect"))
    assert response.status_code == status.HTTP_404_NOT_FOUND
