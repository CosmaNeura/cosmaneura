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


def get_zak_token(doctor: Doctor) -> str:
    assert doctor.zoom_user_id, f"doctor {doctor.id} has no zoom_user_id"
    assert doctor.zoom_access_token, f"doctor {doctor.id} has no zoom_access_token"

    if _is_token_expired(doctor):
        doctor = refresh_access_token(doctor)

    with httpx.Client() as client:
        response = client.get(
            f"{ZOOM_API_BASE}/users/{doctor.zoom_user_id}/token",
            params={"type": "zak"},
            headers={"Authorization": f"Bearer {doctor.zoom_access_token}"},
        )
        response.raise_for_status()
        data = response.json()

    log.info("zoom zak token obtained", doctor_id=str(doctor.id))
    return data["token"]


def get_user_display_name(doctor: Doctor) -> str:
    if _is_token_expired(doctor):
        doctor = refresh_access_token(doctor)

    with httpx.Client() as client:
        response = client.get(
            f"{ZOOM_API_BASE}/users/{doctor.zoom_user_id}",
            headers={"Authorization": f"Bearer {doctor.zoom_access_token}"},
        )
        response.raise_for_status()
        data = response.json()

    return f"{data.get('first_name', '')} {data.get('last_name', '')}".strip() or data.get("email", "")


def get_meeting_join_url(doctor: Doctor, meeting_id: str) -> str:
    if _is_token_expired(doctor):
        doctor = refresh_access_token(doctor)

    with httpx.Client() as client:
        response = client.get(
            f"{ZOOM_API_BASE}/meetings/{meeting_id}",
            headers={"Authorization": f"Bearer {doctor.zoom_access_token}"},
        )
        response.raise_for_status()
        data = response.json()

    return data["join_url"]
