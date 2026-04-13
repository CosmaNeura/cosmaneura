import httpx

from app import log
from app.models.doctor import Doctor
from app.models.doctor_video_integration import DoctorVideoIntegration, VideoProvider
from app.services.zoom_token import get_app_token

ZOOM_API_BASE = "https://api.zoom.us/v2"


def get_zoom_integration(doctor: Doctor) -> DoctorVideoIntegration | None:
    return DoctorVideoIntegration.get(doctor_id=doctor.id, provider=VideoProvider.zoom)


def _zoom_get(path: str, *, params: dict[str, str] | None = None) -> dict:
    token = get_app_token()

    with httpx.Client() as client:
        response = client.get(
            f"{ZOOM_API_BASE}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )

    if response.status_code == httpx.codes.UNAUTHORIZED:
        log.info("zoom app token rejected, refreshing", path=path)
        token = get_app_token(force=True)

        with httpx.Client() as client:
            response = client.get(
                f"{ZOOM_API_BASE}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )

    response.raise_for_status()
    return response.json()


def get_zak_token(doctor: Doctor) -> str:
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    data = _zoom_get(f"/users/{integration.provider_user_id}/token", params={"type": "zak"})

    log.info("zoom zak token obtained", doctor_id=str(doctor.id))
    return data["token"]


def get_user_display_name(doctor: Doctor) -> str:
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    data = _zoom_get(f"/users/{integration.provider_user_id}")
    return f"{data.get('first_name', '')} {data.get('last_name', '')}".strip() or data.get("email", "")


def get_meeting_join_url(doctor: Doctor, meeting_id: str) -> str:
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    data = _zoom_get(f"/meetings/{meeting_id}")
    return data["join_url"]


def get_user_meetings(doctor: Doctor) -> list[dict]:
    """Fetch upcoming scheduled meetings for the authenticated doctor from Zoom API."""
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    data = _zoom_get(
        f"/users/{integration.provider_user_id}/meetings",
        params={"type": "upcoming", "page_size": "100"},
    )

    log.info("zoom meetings fetched", doctor_id=str(doctor.id), count=len(data.get("meetings", [])))
    return data.get("meetings", [])


def get_user_by_email(email: str) -> dict | None:
    """Look up a Zoom user by email in this account. Returns None if not found."""
    token = get_app_token()

    with httpx.Client() as client:
        response = client.get(
            f"{ZOOM_API_BASE}/users/{email}",
            headers={"Authorization": f"Bearer {token}"},
        )

    if response.status_code == httpx.codes.NOT_FOUND:
        return None

    response.raise_for_status()
    return response.json()


def delete_meeting(doctor: Doctor, zoom_meeting_id: str) -> None:
    """Cancel a Zoom meeting by ID."""
    integration = get_zoom_integration(doctor)
    assert integration, f"doctor {doctor.id} has no zoom integration"

    token = get_app_token()

    with httpx.Client() as client:
        response = client.delete(
            f"{ZOOM_API_BASE}/meetings/{zoom_meeting_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    if response.status_code == httpx.codes.UNAUTHORIZED:
        log.info("zoom app token rejected, refreshing", meeting_id=zoom_meeting_id)
        token = get_app_token(force=True)

        with httpx.Client() as client:
            response = client.delete(
                f"{ZOOM_API_BASE}/meetings/{zoom_meeting_id}",
                headers={"Authorization": f"Bearer {token}"},
            )

    response.raise_for_status()
    log.info("zoom meeting deleted", doctor_id=str(doctor.id), zoom_meeting_id=zoom_meeting_id)
