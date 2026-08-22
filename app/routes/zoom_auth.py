"""Public OAuth routes for Zoom Authorization Code Grant flow."""

import base64
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from redis.exceptions import RedisError
from starlette import status

from app import log
from app.configuration.zoom import (
    ZOOM_CLIENT_ID,
    ZOOM_CLIENT_SECRET,
    ZOOM_OAUTH_TOKEN_URL,
    ZOOM_REDIRECT_URI,
    ZOOM_SUCCESS_REDIRECT,
)
from app.services import zoom_oauth_state
from app.services.external_webhook_inbox import provider_log_ref

from app.models.doctor import Doctor
from app.models.doctor_video_integration import DoctorVideoIntegration, VideoProvider

ZOOM_AUTHORIZE_URL = "https://zoom.us/oauth/authorize"
ZOOM_API_ME_URL = "https://api.zoom.us/v2/users/me"
ZOOM_PUBLIC_AUTHORIZATION_ERROR = "authorization_failed"

# Defensive timeout for outbound Zoom OAuth calls. See note in
# app/services/recall.py.
_HTTPX_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

zoom_auth_app = APIRouter(prefix="/external/v1", tags=["zoom-oauth"])


def _basic_auth_header() -> str:
    credentials = f"{ZOOM_CLIENT_ID}:{ZOOM_CLIENT_SECRET}"
    return "Basic " + base64.b64encode(credentials.encode()).decode()


def _authorization_error_response() -> RedirectResponse:
    return RedirectResponse(
        url=f"{ZOOM_SUCCESS_REDIRECT}?zoom_error={ZOOM_PUBLIC_AUTHORIZATION_ERROR}",
        status_code=status.HTTP_302_FOUND,
    )


def _clear_browser_binding(response: RedirectResponse) -> RedirectResponse:
    response.delete_cookie(
        key=zoom_oauth_state.ZOOM_OAUTH_BINDING_COOKIE,
        path=zoom_oauth_state.ZOOM_OAUTH_BINDING_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return response


@zoom_auth_app.get("/zoom-oauth-start", name="zoom_oauth_start")
def zoom_oauth_start(state: str | None = None) -> RedirectResponse:
    """Redirect only an issued, unexpired OAuth attempt to Zoom."""
    try:
        state_is_issued = zoom_oauth_state.is_issued_zoom_oauth_state(state)
    except RedisError:
        log.error(
            "zoom oauth state lookup unavailable",
            event_code="zoom_oauth_state_lookup_unavailable",
        )
        return _authorization_error_response()
    if not state_is_issued:
        log.warning(
            "zoom oauth start rejected",
            event_code="zoom_oauth_state_invalid",
        )
        return _authorization_error_response()
    assert state is not None
    # prompt=consent forces Zoom to re-render the consent screen even if the
    # user previously authorized this app. Without it, revoking the token does
    # not trigger a consent step on the next Connect because Zoom still sees
    # the app as installed on the user's account.
    url = f"{ZOOM_AUTHORIZE_URL}?" + urlencode(
        {
            "response_type": "code",
            "client_id": ZOOM_CLIENT_ID,
            "redirect_uri": ZOOM_REDIRECT_URI,
            "state": state,
            "prompt": "consent",
        }
    )
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


@zoom_auth_app.get("/zoom-oauth-callback", name="zoom_oauth_callback")
def zoom_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Handle Zoom's redirect after the doctor authorizes the app."""
    browser_binding = request.cookies.get(zoom_oauth_state.ZOOM_OAUTH_BINDING_COOKIE)
    try:
        doctor_id = zoom_oauth_state.consume_zoom_oauth_state(state, browser_binding)
    except RedisError:
        log.error(
            "zoom oauth state consume unavailable",
            event_code="zoom_oauth_state_consume_unavailable",
        )
        return _authorization_error_response()
    if doctor_id is None:
        log.warning(
            "zoom oauth callback rejected",
            event_code="zoom_oauth_state_invalid",
        )
        return _authorization_error_response()

    doctor = Doctor.get(id=doctor_id)
    if doctor is None:
        log.warning(
            "zoom oauth callback rejected",
            event_code="zoom_oauth_doctor_not_found",
        )
        return _clear_browser_binding(_authorization_error_response())

    if error:
        log.warning(
            "zoom oauth callback rejected",
            event_code="zoom_oauth_provider_rejected",
        )
        return _clear_browser_binding(_authorization_error_response())
    if not code:
        log.warning(
            "zoom oauth callback rejected",
            event_code="zoom_oauth_code_missing",
        )
        return _clear_browser_binding(_authorization_error_response())

    tokens = _exchange_code_for_tokens(code)
    zoom_user_id = _get_zoom_user_id(tokens["access_token"])
    log.info(
        "zoom oauth user identified",
        event_code="zoom_oauth_user_identified",
        zoom_user_ref=provider_log_ref(f"zoom-user:{zoom_user_id}"),
    )
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

    log.info(
        "zoom oauth connected",
        event_code="zoom_oauth_connected",
        doctor_ref=provider_log_ref(f"doctor:{doctor.id}"),
    )
    return _clear_browser_binding(
        RedirectResponse(
            url=ZOOM_SUCCESS_REDIRECT,
            status_code=status.HTTP_302_FOUND,
        )
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
