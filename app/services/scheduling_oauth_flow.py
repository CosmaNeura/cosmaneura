"""Shared OAuth callback handling for SchedulingProvider adapters.

Generalizes app/routes/zoom_auth.py's callback safety logic (view-as
blocking, atomic state consumption, row-locked credential write) across
providers, parameterized by each provider's token exchange and identity
lookup. Each provider's route module supplies those two calls plus its own
config; this module owns everything else so the safety logic is written and
reviewed once.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from fastapi import Request
from fastapi.responses import RedirectResponse
from redis.exceptions import RedisError
from starlette import status

from app import log
from app.jobs import backfill_scheduling_roster, sync_scheduling_provider
from app.routes.dependencies.view_as import (
    deny_view_as_browser_activity,
    view_as_browser_session_present,
)
from app.services import scheduling_oauth_state
from app.services.doctor_access import doctor_access_in_force
from app.services.external_webhook_inbox import provider_log_ref

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from app.models.doctor_scheduling_integration import DoctorSchedulingIntegration
from app.models.patient_external_identity import SchedulingProviderSource
from app.models.view_as import ViewAsSession
from sqlmodel import select

SCHEDULING_PUBLIC_AUTHORIZATION_ERROR = "authorization_failed"


@dataclass(frozen=True)
class ExchangedSchedulingTokens:
    access_token: str
    refresh_token: str | None
    token_expires_at: datetime | None
    provider_calendar_ref: str


def _doctor_ref(doctor_id) -> str:
    return provider_log_ref(f"doctor:{doctor_id}")


def start_oauth(
    request: Request,
    *,
    source: SchedulingProviderSource,
    state: str | None,
    authorize_url_builder: Callable[[str], str],
    error_redirect: str,
) -> RedirectResponse:
    """Redirect only an issued, unexpired OAuth attempt to the provider."""
    deny_view_as_browser_activity(request)
    try:
        is_issued = scheduling_oauth_state.is_issued_scheduling_oauth_state(
            source, state
        )
    except RedisError:
        log.error(
            "scheduling oauth state lookup unavailable",
            event_code="scheduling_oauth_state_lookup_unavailable",
            source=source.value,
        )
        return _error_response(error_redirect)
    if not is_issued:
        log.warning(
            "scheduling oauth start rejected",
            event_code="scheduling_oauth_state_invalid",
            source=source.value,
        )
        return _error_response(error_redirect)
    assert state is not None
    return RedirectResponse(
        url=authorize_url_builder(state), status_code=status.HTTP_302_FOUND
    )


def _error_response(error_redirect: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"{error_redirect}?scheduling_error={SCHEDULING_PUBLIC_AUTHORIZATION_ERROR}",
        status_code=status.HTTP_302_FOUND,
    )


def handle_oauth_callback(
    request: Request,
    *,
    source: SchedulingProviderSource,
    code: str | None,
    state: str | None,
    error: str | None,
    binding_cookie: str,
    exchange_code_for_tokens: Callable[[str], ExchangedSchedulingTokens],
    success_redirect: str,
    error_redirect: str,
) -> RedirectResponse:
    """Handle a provider's redirect after the doctor authorizes the app."""

    def clear_binding(response: RedirectResponse) -> RedirectResponse:
        response.delete_cookie(
            key=binding_cookie, path="/", secure=True, httponly=True, samesite="lax"
        )
        return response

    if view_as_browser_session_present(request):
        log.warning(
            "scheduling oauth callback rejected",
            event_code="scheduling_oauth_view_as_blocked",
            source=source.value,
        )
        return clear_binding(_error_response(error_redirect))

    browser_binding = request.cookies.get(binding_cookie)
    try:
        doctor_id = scheduling_oauth_state.consume_scheduling_oauth_state(
            source, state, browser_binding
        )
    except RedisError:
        log.error(
            "scheduling oauth state consume unavailable",
            event_code="scheduling_oauth_state_consume_unavailable",
            source=source.value,
        )
        return _error_response(error_redirect)
    if doctor_id is None:
        log.warning(
            "scheduling oauth callback rejected",
            event_code="scheduling_oauth_state_invalid",
            source=source.value,
        )
        return _error_response(error_redirect)

    doctor = Doctor.get(id=doctor_id)
    if doctor is None or doctor.deleted_at is not None:
        log.warning(
            "scheduling oauth callback rejected",
            event_code="scheduling_oauth_doctor_unavailable",
            source=source.value,
        )
        return clear_binding(_error_response(error_redirect))
    if not doctor_access_in_force(doctor):
        log.warning(
            "scheduling oauth callback rejected",
            event_code="scheduling_oauth_access_not_in_force",
            source=source.value,
        )
        return clear_binding(_error_response(error_redirect))

    if error:
        log.warning(
            "scheduling oauth callback rejected",
            event_code="scheduling_oauth_provider_rejected",
            source=source.value,
        )
        return clear_binding(_error_response(error_redirect))
    if not code:
        log.warning(
            "scheduling oauth callback rejected",
            event_code="scheduling_oauth_code_missing",
            source=source.value,
        )
        return clear_binding(_error_response(error_redirect))

    with get_session() as session:
        unended_view_as = session.exec(
            select(ViewAsSession).where(
                ViewAsSession.actor_doctor_id == doctor.id,
                ViewAsSession.ended_at == None,  # noqa: E711
            )
        ).first()
    if unended_view_as is not None:
        log.warning(
            "scheduling oauth callback rejected",
            event_code="scheduling_oauth_view_as_blocked",
            source=source.value,
            doctor_ref=_doctor_ref(doctor.id),
        )
        return clear_binding(_error_response(error_redirect))

    tokens = exchange_code_for_tokens(code)

    # Serialize the credential write against administrative account closure
    # and concurrent view-as, exactly like zoom_auth.py.
    with get_session() as session:
        locked_doctor = session.exec(
            select(Doctor)
            .where(Doctor.id == doctor.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if locked_doctor is None or locked_doctor.deleted_at is not None:
            session.rollback()
            log.warning(
                "scheduling oauth callback rejected",
                event_code="scheduling_oauth_doctor_unavailable_after_exchange",
                source=source.value,
                doctor_ref=_doctor_ref(doctor.id),
            )
            return clear_binding(_error_response(error_redirect))
        unended_view_as = session.exec(
            select(ViewAsSession)
            .where(
                ViewAsSession.actor_doctor_id == locked_doctor.id,
                ViewAsSession.ended_at == None,  # noqa: E711
            )
            .with_for_update()
        ).first()
        if unended_view_as is not None:
            session.rollback()
            log.warning(
                "scheduling oauth callback rejected",
                event_code="scheduling_oauth_view_as_blocked_after_exchange",
                source=source.value,
                doctor_ref=_doctor_ref(doctor.id),
            )
            return clear_binding(_error_response(error_redirect))
        integration = session.exec(
            select(DoctorSchedulingIntegration)
            .where(
                DoctorSchedulingIntegration.doctor_id == locked_doctor.id,
                DoctorSchedulingIntegration.source == source,
            )
            .with_for_update()
        ).one_or_none()
        if integration is None:
            integration = DoctorSchedulingIntegration(
                doctor_id=locked_doctor.id,
                source=source,
                provider_calendar_ref=tokens.provider_calendar_ref,
                access_token=tokens.access_token,
                refresh_token=tokens.refresh_token,
                token_expires_at=tokens.token_expires_at,
            )
        else:
            integration.provider_calendar_ref = tokens.provider_calendar_ref
            integration.access_token = tokens.access_token
            integration.refresh_token = tokens.refresh_token
            integration.token_expires_at = tokens.token_expires_at
        session.add(integration)
        session.commit()

    log.info(
        "scheduling oauth connected",
        event_code="scheduling_oauth_connected",
        source=source.value,
        doctor_ref=_doctor_ref(doctor.id),
    )
    # Connecting must actually populate the roster, not just store a
    # credential. backfill_scheduling_roster.perform() is idempotent (it
    # no-ops once last_backfilled_at is set), so queuing it unconditionally
    # here — including on a reconnect after a revoked grant — is safe and
    # also retries a backfill that errored before completing the first time.
    backfill_scheduling_roster.queue(str(doctor.id), source.value)
    sync_scheduling_provider.queue(str(doctor.id), source.value)
    return clear_binding(
        RedirectResponse(url=success_redirect, status_code=status.HTTP_302_FOUND)
    )
