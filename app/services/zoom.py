from datetime import datetime, timedelta, timezone

import httpx

from app import log
from app.configuration.zoom import (
    ZOOM_CLIENT_ID,
    ZOOM_CLIENT_SECRET,
    ZOOM_OAUTH_TOKEN_URL,
)

from app.models.doctor import Doctor

ZOOM_API_BASE = "https://api.zoom.us/v2"


def _basic_auth_header() -> str:
    import base64
    credentials = f"{ZOOM_CLIENT_ID}:{ZOOM_CLIENT_SECRET}"
    return "Basic " + base64.b64encode(credentials.encode()).decode()


def _is_token_expired(doctor: Doctor) -> bool:
    if not doctor.zoom_token_expires_at:
        return True
    expires_at = doctor.zoom_token_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= expires_at - timedelta(minutes=5)


def refresh_access_token(doctor: Doctor) -> Doctor:
    assert doctor.zoom_refresh_token, f"doctor {doctor.id} has no zoom_refresh_token"

    with httpx.Client() as client:
        response = client.post(
            ZOOM_OAUTH_TOKEN_URL,
            headers={"Authorization": _basic_auth_header()},
            params={
                "grant_type": "refresh_token",
                "refresh_token": doctor.zoom_refresh_token,
            },
        )
        response.raise_for_status()
        data = response.json()

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=data["expires_in"])

    doctor.zoom_access_token = data["access_token"]
    doctor.zoom_refresh_token = data["refresh_token"]
    doctor.zoom_token_expires_at = expires_at
    doctor.save()

    log.info("zoom access token refreshed", doctor_id=str(doctor.id))
    return doctor


def _zoom_get(doctor: Doctor, path: str, *, params: dict[str, str] | None = None) -> dict:
    if _is_token_expired(doctor):
        doctor = refresh_access_token(doctor)

    with httpx.Client() as client:
        response = client.get(
            f"{ZOOM_API_BASE}{path}",
            params=params,
            headers={"Authorization": f"Bearer {doctor.zoom_access_token}"},
        )

    if response.status_code == httpx.codes.UNAUTHORIZED:
        log.info("zoom access token rejected, refreshing", doctor_id=str(doctor.id), path=path)
        doctor = refresh_access_token(doctor)

        with httpx.Client() as client:
            response = client.get(
                f"{ZOOM_API_BASE}{path}",
                params=params,
                headers={"Authorization": f"Bearer {doctor.zoom_access_token}"},
            )

    response.raise_for_status()
    return response.json()


def get_zak_token(doctor: Doctor) -> str:
    assert doctor.zoom_user_id, f"doctor {doctor.id} has no zoom_user_id"
    assert doctor.zoom_access_token, f"doctor {doctor.id} has no zoom_access_token"

    data = _zoom_get(
        doctor,
        f"/users/{doctor.zoom_user_id}/token",
        params={"type": "zak"},
    )

    log.info("zoom zak token obtained", doctor_id=str(doctor.id))
    return data["token"]


def get_user_display_name(doctor: Doctor) -> str:
    data = _zoom_get(doctor, f"/users/{doctor.zoom_user_id}")

    return f"{data.get('first_name', '')} {data.get('last_name', '')}".strip() or data.get("email", "")


def get_meeting_join_url(doctor: Doctor, meeting_id: str) -> str:
    data = _zoom_get(doctor, f"/meetings/{meeting_id}")

    return data["join_url"]


def get_user_meetings(doctor: Doctor) -> list[dict]:
    """Fetch upcoming scheduled meetings for the authenticated doctor from Zoom API."""
    assert doctor.zoom_user_id, f"doctor {doctor.id} has no zoom_user_id"

    data = _zoom_get(
        doctor,
        f"/users/{doctor.zoom_user_id}/meetings",
        params={"type": "upcoming", "page_size": "100"},
    )

    log.info("zoom meetings fetched", doctor_id=str(doctor.id), count=len(data.get("meetings", [])))
    return data.get("meetings", [])


def delete_meeting(doctor: Doctor, zoom_meeting_id: str) -> None:
    """Cancel a Zoom meeting by ID."""
    if _is_token_expired(doctor):
        doctor = refresh_access_token(doctor)

    with httpx.Client() as client:
        response = client.delete(
            f"{ZOOM_API_BASE}/meetings/{zoom_meeting_id}",
            headers={"Authorization": f"Bearer {doctor.zoom_access_token}"},
        )

    response.raise_for_status()
    log.info("zoom meeting deleted", doctor_id=str(doctor.id), zoom_meeting_id=zoom_meeting_id)
