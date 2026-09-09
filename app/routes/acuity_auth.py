"""Public OAuth routes for Acuity Scheduling's Authorization Code Grant flow.

Thin wrapper over app/services/scheduling_oauth_flow.py; see zoom_auth.py for
the Zoom equivalent this generalizes.
"""

from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.configuration.acuity import (
    ACUITY_API_BASE,
    ACUITY_CLIENT_ID,
    ACUITY_CLIENT_SECRET,
    ACUITY_OAUTH_AUTHORIZE_URL,
    ACUITY_OAUTH_TOKEN_URL,
    ACUITY_REDIRECT_URI,
    ACUITY_SUCCESS_REDIRECT,
)
from app.services import scheduling_oauth_state
from app.services.scheduling_oauth_flow import (
    ExchangedSchedulingTokens,
    handle_oauth_callback,
    start_oauth,
)

from app.models.patient_external_identity import SchedulingProviderSource

_HTTPX_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_SOURCE = SchedulingProviderSource.acuity
_BINDING_COOKIE = scheduling_oauth_state.scheduling_oauth_binding_cookie(_SOURCE)

acuity_auth_app = APIRouter(prefix="/external/v1", tags=["acuity-oauth"])


def _authorize_url(state: str) -> str:
    return f"{ACUITY_OAUTH_AUTHORIZE_URL}?" + urlencode(
        {
            "response_type": "code",
            "scope": "api-v1",
            "client_id": ACUITY_CLIENT_ID,
            "redirect_uri": ACUITY_REDIRECT_URI,
            "state": state,
        }
    )


def _exchange_code_for_tokens(code: str) -> ExchangedSchedulingTokens:
    with httpx.Client(timeout=_HTTPX_TIMEOUT) as client:
        token_response = client.post(
            ACUITY_OAUTH_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": ACUITY_REDIRECT_URI,
                "client_id": ACUITY_CLIENT_ID,
                "client_secret": ACUITY_CLIENT_SECRET,
            },
        )
    token_response.raise_for_status()
    access_token = token_response.json()["access_token"]

    # v1 scopes one doctor to one Acuity calendar (see ARCHITECTURE.md §7 —
    # MCD-as-organization above practice tenancy is an explicitly open
    # question this does not resolve). An account with multiple calendars
    # picks the first; a calendar-picker UI is future work if a doctor's
    # Acuity account needs a different one.
    with httpx.Client(timeout=_HTTPX_TIMEOUT) as client:
        calendars_response = client.get(
            f"{ACUITY_API_BASE}/calendars",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    calendars_response.raise_for_status()
    calendars = calendars_response.json()
    if not calendars:
        raise ValueError("acuity account has no calendars to connect")
    calendar_id = str(calendars[0]["id"])

    return ExchangedSchedulingTokens(
        access_token=access_token,
        # No refresh token or expiry in Acuity's documented OAuth2 flow.
        refresh_token=None,
        token_expires_at=None,
        provider_calendar_ref=calendar_id,
    )


@acuity_auth_app.get("/acuity-oauth-start", name="acuity_oauth_start")
def acuity_oauth_start(request: Request, state: str | None = None) -> RedirectResponse:
    return start_oauth(
        request,
        source=_SOURCE,
        state=state,
        authorize_url_builder=_authorize_url,
        error_redirect=ACUITY_SUCCESS_REDIRECT,
    )


@acuity_auth_app.get("/acuity-oauth-callback", name="acuity_oauth_callback")
def acuity_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    return handle_oauth_callback(
        request,
        source=_SOURCE,
        code=code,
        state=state,
        error=error,
        binding_cookie=_BINDING_COOKIE,
        exchange_code_for_tokens=_exchange_code_for_tokens,
        success_redirect=ACUITY_SUCCESS_REDIRECT,
        error_redirect=ACUITY_SUCCESS_REDIRECT,
    )
