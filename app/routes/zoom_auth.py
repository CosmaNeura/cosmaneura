import base64
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import log
from app.configuration.zoom import (
    ZOOM_CLIENT_ID,
    ZOOM_CLIENT_SECRET,
    ZOOM_OAUTH_TOKEN_URL,
    ZOOM_REDIRECT_URI,
    ZOOM_SUCCESS_REDIRECT,
)

from app.models.doctor import Doctor

ZOOM_OAUTH_AUTHORIZE_URL = "https://zoom.us/oauth/authorize"
ZOOM_API_ME_URL = "https://api.zoom.us/v2/users/me"

zoom_auth_app = APIRouter(tags=["zoom-auth"])


def _basic_auth_header() -> str:
    credentials = f"{ZOOM_CLIENT_ID}:{ZOOM_CLIENT_SECRET}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return f"Basic {encoded}"


@zoom_auth_app.get("/auth/zoom")
async def zoom_oauth_start(request: Request):
    """Redirects the doctor to Zoom's OAuth authorization page."""
    params = urlencode({
        "response_type": "code",
        "client_id": ZOOM_CLIENT_ID,
        "redirect_uri": ZOOM_REDIRECT_URI,
    })
    return RedirectResponse(url=f"{ZOOM_OAUTH_AUTHORIZE_URL}?{params}")


@zoom_auth_app.get("/auth/zoom/callback")
async def zoom_oauth_callback(request: Request):
    code = request.query_params.get("code")
    error = request.query_params.get("error")

    if error:
        log.info("zoom oauth error", error=error)
        raise HTTPException(status_code=400, detail=f"Zoom OAuth error: {error}")

    assert code, "missing code param from Zoom OAuth callback"

    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            ZOOM_OAUTH_TOKEN_URL,
            headers={"Authorization": _basic_auth_header()},
            params={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": ZOOM_REDIRECT_URI,
            },
        )
        token_response.raise_for_status()
        token_data = token_response.json()

        me_response = await client.get(
            ZOOM_API_ME_URL,
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
        me_response.raise_for_status()
        me_data = me_response.json()

    zoom_user_id = me_data["id"]
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=token_data["expires_in"])

    clerk_id = request.query_params.get("state")
    assert clerk_id, "missing state param from Zoom OAuth callback"

    doctor = Doctor.get(clerk_id=clerk_id)
    assert doctor, f"No doctor found with clerk_id {clerk_id}"

    doctor.zoom_user_id = zoom_user_id
    doctor.zoom_access_token = token_data["access_token"]
    doctor.zoom_refresh_token = token_data["refresh_token"]
    doctor.zoom_token_expires_at = expires_at
    doctor.save()

    log.info(
        "zoom oauth tokens saved",
        doctor_id=str(doctor.id),
        zoom_user_id=zoom_user_id,
    )

    return RedirectResponse(url=ZOOM_SUCCESS_REDIRECT)
