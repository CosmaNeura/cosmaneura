# app/services/zoom.py
import base64
from datetime import datetime, timedelta, timezone

import httpx

from app import log
from app.configuration.redis import get_redis
from app.configuration.zoom import (
    ZOOM_CLIENT_ID,
    ZOOM_CLIENT_SECRET,
    ZOOM_OAUTH_TOKEN_URL,
)

from app.models.doctor import Doctor
from app.models.doctor_video_integration import DoctorVideoIntegration, VideoProvider

ZOOM_API_BASE = "https://api.zoom.us/v2"
ZOOM_OAUTH_REVOKE_URL = "https://zoom.us/oauth/revoke"

# Defensive timeout for outbound Zoom API calls. See note in recall.py.
_HTTPX_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_TOKEN_EXPIRY_BUFFER = timedelta(minutes=5)
# Surfaces an unexpected disconnect to the doctor on next app load. 7 days
# is enough for the doctor to come back from a weekend off and still see
# the alert; if they never come back, the flag self-cleans.
_REVOKED_FLAG_TTL = timedelta(days=7)


def zoom_revoked_redis_key(doctor_id: str) -> str:
    return f"doctor:{doctor_id}:zoom_revoked"


class ZoomIntegrationInvalid(Exception):
    """Raised when the doctor's Zoom OAuth grant is no longer valid.

    Indicates that Zoom returned ``invalid_grant`` on a refresh — almost
    always because the user revoked the app from marketplace.zoom.us, or
    the refresh_token was rotated by a parallel refresh. The integration
    row has been removed; callers should treat the doctor as not connected
    and skip Zoom-dependent work cleanly instead of bubbling a 500.
    """


def get_zoom_integration(doctor: Doctor) -> DoctorVideoIntegration | None:
    return DoctorVideoIntegration.get(doctor_id=doctor.id, provider=VideoProvider.zoom)


def _basic_auth_header() -> str:
    credentials = f"{ZOOM_CLIENT_ID}:{ZOOM_CLIENT_SECRET}"
    return "Basic " + base64.b64encode(credentials.encode()).decode()


def _get_doctor_token(
    integration: DoctorVideoIntegration, *, force: bool = False
) -> str:
    """Return a valid access token for this integration, refreshing if needed.

    Refreshes proactively if the token expires within 5 minutes, or immediately
    when force=True (e.g. after a 401 response).
    """
    now = datetime.now(timezone.utc)

    # token_expires_at may be naive or aware depending on DB driver
    expires_at = integration.token_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if not force and expires_at > now + _TOKEN_EXPIRY_BUFFER:
        return integration.access_token

    with httpx.Client(timeout=_HTTPX_TIMEOUT) as client:
        response = client.post(
            ZOOM_OAUTH_TOKEN_URL,
            headers={"Authorization": _basic_auth_header()},
            data={
                "grant_type": "refresh_token",
                "refresh_token": integration.refresh_token,
            },
        )

    if response.status_code == 400:
        try:
            error_code = response.json().get("error")
        except ValueError:
            error_code = None
        if error_code == "invalid_grant":
            log.warning(
                "zoom refresh returned invalid_grant; removing integration",
                doctor_id=str(integration.doctor_id),
            )
            doctor_id = str(integration.doctor_id)
            integration.delete()
            get_redis().set(
                zoom_revoked_redis_key(doctor_id), "1", ex=_REVOKED_FLAG_TTL
            )
            raise ZoomIntegrationInvalid(f"doctor {doctor_id} revoked Zoom OAuth")

    response.raise_for_status()
    data = response.json()

    integration.access_token = data["access_token"]
    integration.refresh_token = data["refresh_token"]
    integration.token_expires_at = now + timedelta(seconds=data["expires_in"])
    integration.save()

    log.info("zoom token refreshed", doctor_id=str(integration.doctor_id))
    return integration.access_token


def _zoom_request(
    method: str,
    path: str,
    *,
    integration: DoctorVideoIntegration,
    params: dict | None = None,
) -> httpx.Response:
    """Make an authenticated Zoom API request, retrying once on 401 with a fresh token."""
    token = _get_doctor_token(integration)

    with httpx.Client(timeout=_HTTPX_TIMEOUT) as client:
        response = client.request(
            method,
            f"{ZOOM_API_BASE}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )

    if response.status_code == httpx.codes.UNAUTHORIZED:
        log.info("zoom token rejected, refreshing", path=path)
        token = _get_doctor_token(integration, force=True)
        with httpx.Client(timeout=_HTTPX_TIMEOUT) as client:
            response = client.request(
                method,
                f"{ZOOM_API_BASE}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )

    return response


def _zoom_get(
    path: str,
    *,
    integration: DoctorVideoIntegration,
    params: dict | None = None,
) -> dict:
    response = _zoom_request("GET", path, integration=integration, params=params)
    response.raise_for_status()
    return response.json()


def revoke_token(doctor: Doctor) -> None:
    """Revoke the doctor's Zoom OAuth token and delete the integration record.

    Errors from Zoom are logged but do not raise — the token may already be invalid.
    """
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    with httpx.Client(timeout=_HTTPX_TIMEOUT) as client:
        response = client.post(
            ZOOM_OAUTH_REVOKE_URL,
            headers={"Authorization": _basic_auth_header()},
            data={"token": integration.refresh_token},
        )

    if not response.is_success:
        log.warning(
            "zoom token revoke returned error",
            status=response.status_code,
            body=response.text,
        )
    else:
        log.info("zoom token revoked", doctor_id=str(doctor.id))

    integration.delete()


def get_zak_token(doctor: Doctor) -> str:
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    data = _zoom_get(
        f"/users/{integration.provider_user_id}/token",
        integration=integration,
        params={"type": "zak"},
    )
    log.info("zoom zak token obtained", doctor_id=str(doctor.id))
    return data["token"]


def get_user_display_name(doctor: Doctor) -> str:
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    data = _zoom_get(f"/users/{integration.provider_user_id}", integration=integration)
    return (
        f"{data.get('first_name', '')} {data.get('last_name', '')}".strip()
        or data.get("email", "")
    )


def get_meeting_join_url(doctor: Doctor, meeting_id: str) -> str:
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    data = _zoom_get(f"/meetings/{meeting_id}", integration=integration)
    return data["join_url"]


def get_user_meetings(doctor: Doctor) -> list[dict]:
    """Fetch upcoming scheduled meetings for the authenticated doctor from Zoom API."""
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    data = _zoom_get(
        f"/users/{integration.provider_user_id}/meetings",
        integration=integration,
        params={"type": "upcoming", "page_size": "100"},
    )
    log.info(
        "zoom meetings fetched",
        doctor_id=str(doctor.id),
        count=len(data.get("meetings", [])),
    )
    return data.get("meetings", [])


def delete_meeting(doctor: Doctor, zoom_meeting_id: str) -> None:
    """Cancel a Zoom meeting by ID."""
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    response = _zoom_request(
        "DELETE", f"/meetings/{zoom_meeting_id}", integration=integration
    )
    response.raise_for_status()
    log.info(
        "zoom meeting deleted",
        doctor_id=str(doctor.id),
        zoom_meeting_id=zoom_meeting_id,
    )
