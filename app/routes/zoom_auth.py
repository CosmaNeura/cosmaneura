"""Public OAuth routes for Zoom Authorization Code Grant flow."""

import base64
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter
from fastapi.responses import RedirectResponse
from starlette import status

from app import log
from app.configuration.zoom import (
    ZOOM_CLIENT_ID,
    ZOOM_CLIENT_SECRET,
    ZOOM_OAUTH_TOKEN_URL,
    ZOOM_REDIRECT_URI,
    ZOOM_SUCCESS_REDIRECT,
)

from app.models.doctor import Doctor
from app.models.doctor_video_integration import DoctorVideoIntegration, VideoProvider

ZOOM_AUTHORIZE_URL = "https://zoom.us/oauth/authorize"
ZOOM_API_ME_URL = "https://api.zoom.us/v2/users/me"

# Defensive timeout for outbound Zoom OAuth calls. See note in
# app/services/recall.py.
_HTTPX_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

zoom_auth_app = APIRouter(prefix="/external/v1", tags=["zoom-oauth"])


def _basic_auth_header() -> str:
    credentials = f"{ZOOM_CLIENT_ID}:{ZOOM_CLIENT_SECRET}"
    return "Basic " + base64.b64encode(credentials.encode()).decode()


@zoom_auth_app.get("/zoom-oauth-start", name="zoom_oauth_start")
def zoom_oauth_start(state: str) -> RedirectResponse:
    """Redirect the doctor to Zoom's authorization page.

    `state` is the doctor's clerk_id, passed from the frontend.
    """
    # prompt=consent forces Zoom to re-render the consent screen even if the
    # user previously authorized this app. Without it, revoking the token does
    # not trigger a consent step on the next Connect because Zoom still sees
    # the app as installed on the user's account.
    url = (
        f"{ZOOM_AUTHORIZE_URL}"
        f"?response_type=code"
        f"&client_id={ZOOM_CLIENT_ID}"
        f"&redirect_uri={ZOOM_REDIRECT_URI}"
        f"&state={state}"
        f"&prompt=consent"
    )
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


@zoom_auth_app.get("/zoom-oauth-callback", name="zoom_oauth_callback")
def zoom_oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Handle Zoom's redirect after the doctor authorizes the app."""
    if error:
        log.warning("zoom oauth error returned", error=error)
        return RedirectResponse(
            url=f"{ZOOM_SUCCESS_REDIRECT}?zoom_error={error}",
            status_code=status.HTTP_302_FOUND,
        )

    assert code and state, "code and state are required"

    doctor = Doctor.get(clerk_id=state)
    if doctor is None or doctor.deleted_at is not None:
        log.warning("zoom oauth callback rejected: doctor unavailable")
        return RedirectResponse(
            url=f"{ZOOM_SUCCESS_REDIRECT}?zoom_error=account_disabled",
            status_code=status.HTTP_302_FOUND,
        )

    tokens = _exchange_code_for_tokens(code)
    zoom_user_id = _get_zoom_user_id(tokens["access_token"])
    log.info("zoom oauth user identified", zoom_user_id=zoom_user_id)

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=tokens["expires_in"])

    integration = DoctorVideoIntegration.find_or_initialize_by(
        doctor_id=doctor.id,
        provider=VideoProvider.zoom,
    )
    integration.provider_user_id = zoom_user_id
    integration.access_token = tokens["access_token"]
    integration.refresh_token = tokens["refresh_token"]
    integration.token_expires_at = expires_at
    integration.save()

    log.info("zoom oauth connected", doctor_id=str(doctor.id))
    return RedirectResponse(
        url=ZOOM_SUCCESS_REDIRECT, status_code=status.HTTP_302_FOUND
    )


def _exchange_code_for_tokens(code: str) -> dict:
    with httpx.Client(timeout=_HTTPX_TIMEOUT) as client:
        response = client.post(
            ZOOM_OAUTH_TOKEN_URL,
            headers={"Authorization": _basic_auth_header()},
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": ZOOM_REDIRECT_URI,
            },
        )
    response.raise_for_status()
    return response.json()


def _get_zoom_user_id(access_token: str) -> str:
    with httpx.Client(timeout=_HTTPX_TIMEOUT) as client:
        response = client.get(
            ZOOM_API_ME_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    response.raise_for_status()
    return response.json()["id"]
