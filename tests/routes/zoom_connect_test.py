"""Tests for the Zoom OAuth Authorization Code Grant flow."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.server import api_app

from app.models.doctor import Doctor
from app.models.doctor_video_integration import DoctorVideoIntegration, VideoProvider

from tests.routes.utils import get_clerk_dev_user

CLERK_DEV_USER_EMAIL = "dev@test.com"


def _get_or_create_doctor():
    _, _, clerk_user = get_clerk_dev_user()
    doctor = Doctor.find_or_create_by(clerk_id=clerk_user.id)
    if not doctor.email:
        doctor.email = CLERK_DEV_USER_EMAIL
        doctor.save()
    return doctor


def _make_integration(
    doctor: Doctor, provider_user_id: str = "zoom_abc123"
) -> DoctorVideoIntegration:
    return DoctorVideoIntegration(
        doctor_id=doctor.id,
        provider=VideoProvider.zoom,
        provider_user_id=provider_user_id,
        access_token="access_token",
        refresh_token="refresh_token",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ).save()


# --- zoom-auth-url ---


def test_zoom_auth_url_returns_start_url(authenticated_client: TestClient):
    """GET /zoom-auth-url returns a URL pointing to the OAuth start endpoint."""
    doctor = _get_or_create_doctor()

    response = authenticated_client.get(api_app.url_path_for("zoom_auth_url"))

    assert response.status_code == status.HTTP_200_OK
    url = response.json()["url"]
    assert "/external/v1/zoom-oauth-start" in url
    assert doctor.clerk_id in url


# --- zoom-oauth-start ---


def test_zoom_oauth_start_redirects(client: TestClient):
    """GET /zoom-oauth-start redirects to Zoom authorization page."""
    response = client.get(
        api_app.url_path_for("zoom_oauth_start"),
        params={"state": "clerk_test_123"},
        follow_redirects=False,
    )

    assert response.status_code == status.HTTP_302_FOUND
    assert "zoom.us/oauth/authorize" in response.headers["location"]
    assert "clerk_test_123" in response.headers["location"]


# --- zoom-oauth-callback ---

_FAKE_TOKENS = {
    "access_token": "new_access",
    "refresh_token": "new_refresh",
    "expires_in": 3600,
}


def test_zoom_oauth_callback_creates_integration(client: TestClient):
    """Callback with valid code + state upserts a DoctorVideoIntegration."""
    doctor = _get_or_create_doctor()

    with (
        patch(
            "app.routes.zoom_auth._exchange_code_for_tokens", return_value=_FAKE_TOKENS
        ),
        patch("app.routes.zoom_auth._get_zoom_user_id", return_value="zoom_xyz"),
        patch(
            "app.routes.zoom_auth.ZOOM_SUCCESS_REDIRECT", "https://example.com/settings"
        ),
    ):
        response = client.get(
            api_app.url_path_for("zoom_oauth_callback"),
            params={"code": "auth_code_123", "state": doctor.clerk_id},
            follow_redirects=False,
        )

    assert response.status_code == status.HTTP_302_FOUND
    integration = DoctorVideoIntegration.get(
        doctor_id=doctor.id, provider=VideoProvider.zoom
    )
    assert integration is not None
    assert integration.provider_user_id == "zoom_xyz"
    assert integration.access_token == "new_access"


def test_zoom_oauth_callback_updates_existing_integration(client: TestClient):
    """Callback upserts when an integration already exists."""
    doctor = _get_or_create_doctor()
    _make_integration(doctor, provider_user_id="old_zoom_id")

    with (
        patch(
            "app.routes.zoom_auth._exchange_code_for_tokens", return_value=_FAKE_TOKENS
        ),
        patch("app.routes.zoom_auth._get_zoom_user_id", return_value="new_zoom_id"),
        patch(
            "app.routes.zoom_auth.ZOOM_SUCCESS_REDIRECT", "https://example.com/settings"
        ),
    ):
        response = client.get(
            api_app.url_path_for("zoom_oauth_callback"),
            params={"code": "auth_code_123", "state": doctor.clerk_id},
            follow_redirects=False,
        )

    assert response.status_code == status.HTTP_302_FOUND
    integration = DoctorVideoIntegration.get(
        doctor_id=doctor.id, provider=VideoProvider.zoom
    )
    assert integration.provider_user_id == "new_zoom_id"


def test_zoom_oauth_callback_error_param(client: TestClient):
    """Callback with error param redirects without creating an integration."""
    doctor = _get_or_create_doctor()

    with patch(
        "app.routes.zoom_auth.ZOOM_SUCCESS_REDIRECT", "https://example.com/settings"
    ):
        response = client.get(
            api_app.url_path_for("zoom_oauth_callback"),
            params={"error": "access_denied", "state": doctor.clerk_id},
            follow_redirects=False,
        )

    assert response.status_code == status.HTTP_302_FOUND
    assert "zoom_error" in response.headers["location"]


# --- zoom-disconnect ---


def test_zoom_disconnect_revokes_and_deletes(authenticated_client: TestClient):
    """DELETE /zoom-connect calls revoke_token, deleting the integration."""
    doctor = _get_or_create_doctor()
    _make_integration(doctor)

    with patch("app.routes.internal.zoom_service.revoke_token") as mock_revoke:
        response = authenticated_client.delete(api_app.url_path_for("zoom_disconnect"))

    assert response.status_code == status.HTTP_204_NO_CONTENT
    mock_revoke.assert_called_once()


def test_zoom_disconnect_not_connected_raises(authenticated_client: TestClient):
    """DELETE /zoom-connect raises AssertionError when no integration exists."""
    _get_or_create_doctor()

    with (
        patch(
            "app.routes.internal.zoom_service.revoke_token", side_effect=AssertionError
        ),
        pytest.raises(AssertionError),
    ):
        authenticated_client.delete(api_app.url_path_for("zoom_disconnect"))
