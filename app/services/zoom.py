from datetime import datetime, timedelta, timezone

import httpx

from app import log
from app.configuration.zoom import (
    ZOOM_CLIENT_ID,
    ZOOM_CLIENT_SECRET,
    ZOOM_OAUTH_TOKEN_URL,
)

from app.models.doctor import Doctor
from app.models.doctor_video_integration import DoctorVideoIntegration, VideoProvider

ZOOM_API_BASE = "https://api.zoom.us/v2"


def _basic_auth_header() -> str:
    import base64
    credentials = f"{ZOOM_CLIENT_ID}:{ZOOM_CLIENT_SECRET}"
    return "Basic " + base64.b64encode(credentials.encode()).decode()


def get_zoom_integration(doctor: Doctor) -> DoctorVideoIntegration | None:
    return DoctorVideoIntegration.get(doctor_id=doctor.id, provider=VideoProvider.zoom)


def _is_token_expired(integration: DoctorVideoIntegration) -> bool:
    expires_at = integration.token_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= expires_at - timedelta(minutes=5)


def refresh_access_token(integration: DoctorVideoIntegration) -> DoctorVideoIntegration:
    assert integration.refresh_token, f"integration {integration.id} has no refresh_token"

    with httpx.Client() as client:
        response = client.post(
            ZOOM_OAUTH_TOKEN_URL,
            headers={"Authorization": _basic_auth_header()},
            params={
                "grant_type": "refresh_token",
                "refresh_token": integration.refresh_token,
            },
        )
        response.raise_for_status()
        data = response.json()

    integration.access_token = data["access_token"]
    integration.refresh_token = data["refresh_token"]
    integration.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=data["expires_in"])
    integration.save()

    log.info("zoom access token refreshed", integration_id=str(integration.id))
    return integration


def _zoom_get(integration: DoctorVideoIntegration, path: str, *, params: dict[str, str] | None = None) -> dict:
    if _is_token_expired(integration):
        integration = refresh_access_token(integration)

    with httpx.Client() as client:
        response = client.get(
            f"{ZOOM_API_BASE}{path}",
            params=params,
            headers={"Authorization": f"Bearer {integration.access_token}"},
        )

    if response.status_code == httpx.codes.UNAUTHORIZED:
        log.info("zoom access token rejected, refreshing", integration_id=str(integration.id), path=path)
        integration = refresh_access_token(integration)

        with httpx.Client() as client:
            response = client.get(
                f"{ZOOM_API_BASE}{path}",
                params=params,
                headers={"Authorization": f"Bearer {integration.access_token}"},
            )

    response.raise_for_status()
    return response.json()


def get_zak_token(doctor: Doctor) -> str:
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    data = _zoom_get(
        integration,
        f"/users/{integration.provider_user_id}/token",
        params={"type": "zak"},
    )

    log.info("zoom zak token obtained", doctor_id=str(doctor.id))
    return data["token"]


def get_user_display_name(doctor: Doctor) -> str:
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    data = _zoom_get(integration, f"/users/{integration.provider_user_id}")
    return f"{data.get('first_name', '')} {data.get('last_name', '')}".strip() or data.get("email", "")


def get_meeting_join_url(doctor: Doctor, meeting_id: str) -> str:
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    data = _zoom_get(integration, f"/meetings/{meeting_id}")
    return data["join_url"]


def get_user_meetings(doctor: Doctor) -> list[dict]:
    """Fetch upcoming scheduled meetings for the authenticated doctor from Zoom API."""
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    data = _zoom_get(
        integration,
        f"/users/{integration.provider_user_id}/meetings",
        params={"type": "upcoming", "page_size": "100"},
    )

    log.info("zoom meetings fetched", doctor_id=str(doctor.id), count=len(data.get("meetings", [])))
    return data.get("meetings", [])


def delete_meeting(doctor: Doctor, zoom_meeting_id: str) -> None:
    """Cancel a Zoom meeting by ID."""
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    if _is_token_expired(integration):
        integration = refresh_access_token(integration)

    with httpx.Client() as client:
        response = client.delete(
            f"{ZOOM_API_BASE}/meetings/{zoom_meeting_id}",
            headers={"Authorization": f"Bearer {integration.access_token}"},
        )

    if response.status_code == httpx.codes.UNAUTHORIZED:
        log.info("zoom access token rejected, refreshing", integration_id=str(integration.id), meeting_id=zoom_meeting_id)
        integration = refresh_access_token(integration)

        with httpx.Client() as client:
            response = client.delete(
                f"{ZOOM_API_BASE}/meetings/{zoom_meeting_id}",
                headers={"Authorization": f"Bearer {integration.access_token}"},
            )

    response.raise_for_status()
    log.info("zoom meeting deleted", doctor_id=str(doctor.id), zoom_meeting_id=zoom_meeting_id)
