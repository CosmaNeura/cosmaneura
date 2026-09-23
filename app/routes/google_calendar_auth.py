"""Public OAuth routes for Google Calendar's Authorization Code Grant flow.

Thin wrapper over app/services/scheduling_oauth_flow.py; see zoom_auth.py /
acuity_auth.py for the equivalents this generalizes.
"""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.configuration.google_calendar import (
    GOOGLE_CALENDAR_CLIENT_ID,
    GOOGLE_CALENDAR_CLIENT_SECRET,
    GOOGLE_CALENDAR_REDIRECT_URI,
    GOOGLE_CALENDAR_SCOPE,
    GOOGLE_CALENDAR_SUCCESS_REDIRECT,
    GOOGLE_OAUTH_AUTHORIZE_URL,
    GOOGLE_OAUTH_TOKEN_URL,
)
from app.services import scheduling_oauth_state
from app.services.scheduling_oauth_flow import (
    ExchangedSchedulingTokens,
    handle_oauth_callback,
    start_oauth,
)

from app.models.patient_external_identity import SchedulingProviderSource

_HTTPX_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_SOURCE = SchedulingProviderSource.google_calendar
_BINDING_COOKIE = scheduling_oauth_state.scheduling_oauth_binding_cookie(_SOURCE)
# The special calendarId alias for the connecting account's own primary
# calendar. v1 scopes one doctor to this one calendar (same simplification
# as Acuity's "first calendar" — see acuity_auth.py); a picker for a
# non-primary calendar is future work.
_PRIMARY_CALENDAR_REF = "primary"

google_calendar_auth_app = APIRouter(
    prefix="/external/v1", tags=["google-calendar-oauth"]
)


def _authorize_url(state: str) -> str:
    return f"{GOOGLE_OAUTH_AUTHORIZE_URL}?" + urlencode(
        {
            "response_type": "code",
            "client_id": GOOGLE_CALENDAR_CLIENT_ID,
            "redirect_uri": GOOGLE_CALENDAR_REDIRECT_URI,
            "state": state,
            "scope": GOOGLE_CALENDAR_SCOPE,
            # offline + consent guarantee a refresh_token comes back even if
            # the doctor previously connected and revoked, mirroring why
            # zoom_auth.py forces prompt=consent.
            "access_type": "offline",
            "prompt": "consent",
        }
    )


def _exchange_code_for_tokens(code: str) -> ExchangedSchedulingTokens:
    with httpx.Client(timeout=_HTTPX_TIMEOUT) as client:
        token_response = client.post(
            GOOGLE_OAUTH_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": GOOGLE_CALENDAR_REDIRECT_URI,
                "client_id": GOOGLE_CALENDAR_CLIENT_ID,
                "client_secret": GOOGLE_CALENDAR_CLIENT_SECRET,
            },
        )
    token_response.raise_for_status()
    data = token_response.json()

    refresh_token = data.get("refresh_token")
    if not refresh_token:
        # Should not happen with access_type=offline&prompt=consent, but
        # fail loudly rather than silently store an unrefreshable grant.
        raise ValueError("google calendar authorization returned no refresh_token")

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=data["expires_in"])

    return ExchangedSchedulingTokens(
        access_token=data["access_token"],
        refresh_token=refresh_token,
        token_expires_at=expires_at,
        provider_calendar_ref=_PRIMARY_CALENDAR_REF,
    )


@google_calendar_auth_app.get(
    "/google-calendar-oauth-start", name="google_calendar_oauth_start"
)
def google_calendar_oauth_start(
    request: Request, state: str | None = None
) -> RedirectResponse:
    return start_oauth(
        request,
        source=_SOURCE,
        state=state,
        authorize_url_builder=_authorize_url,
        error_redirect=GOOGLE_CALENDAR_SUCCESS_REDIRECT,
    )


@google_calendar_auth_app.get(
    "/google-calendar-oauth-callback", name="google_calendar_oauth_callback"
)
def google_calendar_oauth_callback(
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
        success_redirect=GOOGLE_CALENDAR_SUCCESS_REDIRECT,
        error_redirect=GOOGLE_CALENDAR_SUCCESS_REDIRECT,
    )
