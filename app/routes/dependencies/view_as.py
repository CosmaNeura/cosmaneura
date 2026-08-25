"""Apply a server-side, capability-bound read-only view-as session."""

from __future__ import annotations

import hmac

import sentry_sdk
from fastapi import HTTPException, Request, status
from starlette_context import context

from app import log
from app.services.external_webhook_inbox import provider_log_ref
from app.services.superadmin_capability import (
    SuperadminCapabilityError,
    configured_superadmin_capability_environment,
)
from app.services.view_as import (
    INACTIVE_VIEW_AS_REQUEST_BINDING,
    VIEW_AS_REQUEST_BINDING_HEADER,
    ViewAsActorIdentity,
    ViewAsError,
    actor_bound_unended_view_as_exists,
    reconcile_view_as_without_browser_binding,
    resolve_view_as,
    view_as_request_binding,
)

from activemodel.session_manager import get_session
from app.models.doctor import Doctor

SESSION_KEY_VIEW_AS_ID = "view_as_session_id"
SESSION_KEY_VIEW_AS_NONCE = "view_as_browser_nonce"
LEGACY_SESSION_KEY_LOGIN_AS_USER = "login_as_user"

# These GET endpoints create credentials or provider-side authorization state.
# They are not read-only even though their historical HTTP method says GET.
SIDE_EFFECTING_GET_ROUTE_NAMES = {
    "medication_drug_reference",
    "zoom_auth_url",
    "open_sandbox_labs_record",
}

# These routes operate on the real actor and are the only writes permitted
# while view-as is active.  Every other unsafe method is rejected centrally.
VIEW_AS_CONTROL_ROUTE_NAMES = {
    "begin_view_as",
    "end_view_as",
    "view_as_status",
    "view_as_targets",
}


def _clear_browser_view_as(request: Request) -> None:
    request.session.pop(SESSION_KEY_VIEW_AS_ID, None)
    request.session.pop(SESSION_KEY_VIEW_AS_NONCE, None)
    request.session.pop(LEGACY_SESSION_KEY_LOGIN_AS_USER, None)


def view_as_browser_session_present(request: Request) -> bool:
    """Return whether the signed browser cookie claims any view-as state.

    Credential-issuance routes use this conservative check. A stale cookie may
    briefly deny issuance, but it can never mint a credential while the UI is
    presenting another provider's account.
    """

    # Some isolated API mounts intentionally omit SessionMiddleware. Treat the
    # absence of a browser session as no view-as state instead of turning a
    # disabled credential endpoint into a server error.
    session = request.scope.get("session")
    if not isinstance(session, dict):
        return False
    return (
        SESSION_KEY_VIEW_AS_ID in session
        or SESSION_KEY_VIEW_AS_NONCE in session
        or LEGACY_SESSION_KEY_LOGIN_AS_USER in session
    )


def deny_view_as_browser_activity(request: Request) -> None:
    """Block non-internal browser surfaces while a view-as cookie is present."""

    if view_as_browser_session_present(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action is unavailable during read-only view-as.",
        )


def _actor_identity(request: Request) -> ViewAsActorIdentity:
    payload = getattr(request.state.auth_state, "payload", None)
    issuer = payload.get("iss") if isinstance(payload, dict) else None
    subject = payload.get("sub") if isinstance(payload, dict) else None
    clerk_session_id = payload.get("sid") if isinstance(payload, dict) else None
    if (
        not isinstance(issuer, str)
        or not issuer
        or not isinstance(subject, str)
        or not subject
        or not isinstance(clerk_session_id, str)
        or not clerk_session_id
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A verified Clerk session is required for view-as.",
        )
    return ViewAsActorIdentity(
        issuer=issuer,
        subject=subject,
        clerk_session_id=clerk_session_id,
    )


def _route_name(request: Request) -> str | None:
    route = request.scope.get("route")
    name = getattr(route, "name", None)
    return name if isinstance(name, str) else None


def _unsafe_for_view_as(request: Request, route_name: str | None) -> bool:
    return request.method.upper() not in {"GET", "HEAD", "OPTIONS"} or (
        route_name in SIDE_EFFECTING_GET_ROUTE_NAMES
    )


def _reject_stale_request_binding() -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "VIEW_AS_BINDING_STALE",
            "message": "The account view changed. Reload before continuing.",
        },
    )


def _presented_request_binding(request: Request) -> str | None:
    values = request.headers.getlist(VIEW_AS_REQUEST_BINDING_HEADER)
    return values[0] if len(values) == 1 else None


def _require_request_binding(request: Request, expected: str) -> None:
    presented = _presented_request_binding(request)
    if presented is None or not hmac.compare_digest(presented, expected):
        _reject_stale_request_binding()


def apply_view_as(request: Request) -> None:
    """Resolve view-as before practice authorization and enforce read-only use."""

    actor: Doctor = request.state.doctor
    request.state.view_as_actor = actor
    request.state.view_as = None

    # Retire pre-P0 client state. It held a raw target Clerk subject and is not
    # valid authority under the environment-scoped capability contract.
    legacy_binding_claimed = LEGACY_SESSION_KEY_LOGIN_AS_USER in request.session
    request.session.pop(LEGACY_SESSION_KEY_LOGIN_AS_USER, None)

    session_id = request.session.get(SESSION_KEY_VIEW_AS_ID)
    browser_nonce = request.session.get(SESSION_KEY_VIEW_AS_NONCE)
    route_name = _route_name(request)
    is_control_route = route_name in VIEW_AS_CONTROL_ROUTE_NAMES
    unsafe_request = _unsafe_for_view_as(request, route_name) and not is_control_route
    # Read-only admin reporting continues to describe the real actor's control
    # plane. Admin mutations remain blocked exactly like customer mutations.
    use_real_actor = is_control_route or (
        request.url.path.startswith("/internal/v1/admin") and not unsafe_request
    )
    complete_browser_binding = isinstance(session_id, str) and isinstance(
        browser_nonce, str
    )
    browser_binding_claimed = (
        legacy_binding_claimed
        or SESSION_KEY_VIEW_AS_ID in request.session
        or SESSION_KEY_VIEW_AS_NONCE in request.session
    )

    try:
        environment = configured_superadmin_capability_environment()
        # ``aglobal_session`` makes this context manager yield the existing
        # request-scoped transaction rather than opening a second connection.
        with get_session() as database_session:
            if complete_browser_binding:
                assert isinstance(session_id, str)
                assert isinstance(browser_nonce, str)
                resolution = resolve_view_as(
                    database_session,
                    view_as_session_id=session_id,
                    browser_nonce=browser_nonce,
                    actor=actor,
                    identity=_actor_identity(request),
                    environment=environment,
                    unsafe_request=unsafe_request,
                )
            else:
                resolution = reconcile_view_as_without_browser_binding(
                    database_session,
                    actor=actor,
                    identity=_actor_identity(request),
                    environment=environment,
                    unsafe_request=unsafe_request,
                )
    except SuperadminCapabilityError:
        _clear_browser_view_as(request)
        server_binding_claimed = False
        if not browser_binding_claimed and not is_control_route:
            try:
                with get_session() as database_session:
                    server_binding_claimed = actor_bound_unended_view_as_exists(
                        database_session,
                        actor=actor,
                        identity=_actor_identity(request),
                    )
            except ViewAsError:
                server_binding_claimed = True
        if not is_control_route and (browser_binding_claimed or server_binding_claimed):
            _reject_stale_request_binding()
        if not is_control_route:
            _require_request_binding(request, INACTIVE_VIEW_AS_REQUEST_BINDING)
        return
    except ViewAsError:
        _clear_browser_view_as(request)
        if not is_control_route:
            _reject_stale_request_binding()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This account is not ready for interactive view-as.",
        ) from None

    stale_browser_binding = resolution.clear_browser_session or (
        browser_binding_claimed and not complete_browser_binding
    )
    if stale_browser_binding:
        _clear_browser_view_as(request)
    if not is_control_route and (
        stale_browser_binding
        or resolution.rejected_unsafe_fallback
        or (legacy_binding_claimed and unsafe_request)
    ):
        _reject_stale_request_binding()

    if not is_control_route:
        expected_request_binding = (
            view_as_request_binding(resolution.active.session)
            if resolution.active is not None
            else INACTIVE_VIEW_AS_REQUEST_BINDING
        )
        _require_request_binding(request, expected_request_binding)
    if resolution.active is None:
        return

    active = resolution.active
    request.state.view_as = active
    if use_real_actor:
        return
    if unsafe_request:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="View-as is read-only. Exit view-as before making changes.",
        )
    if active.diagnostic_code is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This account is not ready for interactive view-as.",
        )
    if active.target is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This account is not ready for interactive view-as.",
        )

    request.state.doctor = active.target
    target_ref = provider_log_ref(f"doctor:{active.target.id}")
    actor_ref = provider_log_ref(f"doctor:{actor.id}")
    log.info(
        "read-only view-as active",
        event_code="view_as_active",
        actor_ref=actor_ref,
        target_ref=target_ref,
    )
    context["doctor_ref"] = target_ref
    context["view_as_actor_ref"] = actor_ref
    sentry_sdk.set_extra("view_as_actor_ref", actor_ref)
    sentry_sdk.set_user({"id": target_ref})
