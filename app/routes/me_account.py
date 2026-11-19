"""DELETE /v1/me/account — authenticated self-delete for Scribe (Apple 5.1.1(v)).

Body ``{"confirm":"DELETE"}``; identity from Clerk JWT only. Status codes:
200, 403 (stale Clerk ``fva`` / re-auth required), 404 (latch off),
409 (capabilities), 410 (disabled/revoked pilot), 422 (confirm/body),
503 (retryable Clerk / DB / deletion_pending finish).
"""

from __future__ import annotations

from datetime import datetime, timezone

import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from starlette_context import context

from app.configuration.account_self_delete import ACCOUNT_SELF_DELETE_ENABLED
from app.configuration.clerk import CLERK_PRIVATE_KEY
from app.routes.dependencies.clerk import AuthenticateClerkRequest
from app.routes.dependencies.view_as import deny_view_as_browser_activity
from app.services.doctor_self_delete import (
    SELF_DELETE_PRIVACY_DISCLOSURE,
    DoctorSelfDeleteError,
    delete_clerk_only_identity,
    delete_own_account,
    require_fresh_clerk_authentication,
)
from app.services.external_webhook_inbox import provider_log_ref
from app.services.pilot_access_grandfather import revoked_pilot_access_grant

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from sqlmodel import Session

authenticate_clerk_request_middleware = AuthenticateClerkRequest(CLERK_PRIVATE_KEY)


def require_account_self_delete_enabled() -> None:
    """404 while the latch is OFF — before auth/inject side effects."""

    if not ACCOUNT_SELF_DELETE_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


def inject_doctor_for_self_delete(request: Request) -> None:
    """Resolve the caller without creating a Doctor row (never upserts)."""

    if not ACCOUNT_SELF_DELETE_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    request.state.self_delete_clerk_only = False
    request.state.self_delete_clerk_id = None

    clerk_id = request.state.auth_state.payload["sub"]
    doctor = Doctor.get(clerk_id=clerk_id)
    if doctor is None:
        if revoked_pilot_access_grant(clerk_id) is not None:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Your Account has Been Disabled",
            )
        request.state.self_delete_clerk_only = True
        request.state.self_delete_clerk_id = clerk_id
        request.state.doctor = None
        return

    if doctor.account_deletion_status in {"deleted", "deletion_pending"}:
        request.state.doctor = doctor
        doctor_ref = provider_log_ref(f"doctor:{doctor.id}")
        context["doctor_ref"] = doctor_ref
        sentry_sdk.set_user({"id": doctor_ref})
        return

    if doctor.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Your Account has Been Disabled"
        )

    # deny_view_as_browser_activity runs earlier, so this cannot fire in view-as.
    doctor.last_active_at = datetime.now(timezone.utc)
    doctor.save()
    request.state.doctor = doctor
    doctor_ref = provider_log_ref(f"doctor:{doctor.id}")
    context["doctor_ref"] = doctor_ref
    sentry_sdk.set_user({"id": doctor_ref})


me_account_api_app = APIRouter(
    prefix="/v1/me",
    tags=["me"],
    generate_unique_id_function=lambda route: route.name,
    dependencies=[
        Depends(require_account_self_delete_enabled),
        Depends(authenticate_clerk_request_middleware),
        Depends(deny_view_as_browser_activity),
        Depends(inject_doctor_for_self_delete),
    ],
)


class DeleteMyAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: str = Field(
        min_length=1,
        max_length=32,
        description=(
            "Must be the exact phrase DELETE. No target user id is accepted. "
            f"Confirmation UIs must show: {SELF_DELETE_PRIVACY_DISCLOSURE}"
        ),
    )


class DeleteMyAccountResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    practice_left_ownerless: bool
    clerk_identity_removed: bool


def _session_dependency():
    with get_session() as session:
        yield session


def _map_self_delete_error(error: DoctorSelfDeleteError) -> HTTPException:
    code = getattr(error, "status_code", status.HTTP_400_BAD_REQUEST)
    if code not in {
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_409_CONFLICT,
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        status.HTTP_503_SERVICE_UNAVAILABLE,
    }:
        code = status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail=str(error))


def _require_fresh_session(request: Request) -> None:
    """Step-up gate: Clerk ``fva`` must show a recent first-factor verification."""

    auth_state = getattr(request.state, "auth_state", None)
    payload = getattr(auth_state, "payload", None)
    try:
        require_fresh_clerk_authentication(payload)
    except DoctorSelfDeleteError as error:
        raise _map_self_delete_error(error) from error


@me_account_api_app.delete(
    "/account",
    name="delete_my_account",
    status_code=status.HTTP_200_OK,
)
def delete_my_account(
    request: Request,
    body: DeleteMyAccountRequest,
    session: Session = Depends(_session_dependency),
) -> DeleteMyAccountResponse:
    """Delete the signed-in user's Cosmaneura identity and personal data.

    Requires a fresh Clerk first-factor verification (session ``fva`` within
    10 minutes). Retention disclosure for confirmation UIs:
    ``SELF_DELETE_PRIVACY_DISCLOSURE``.
    """

    if not ACCOUNT_SELF_DELETE_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    if getattr(request.state, "self_delete_clerk_only", False):
        clerk_id = getattr(request.state, "self_delete_clerk_id", None)
        if not isinstance(clerk_id, str) or not clerk_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="clerk identity is invalid",
            )
        _require_fresh_session(request)
        try:
            result = delete_clerk_only_identity(
                clerk_id=clerk_id,
                confirm=body.confirm,
            )
        except DoctorSelfDeleteError as error:
            raise _map_self_delete_error(error) from error
        return DeleteMyAccountResponse(
            status=result.status,
            practice_left_ownerless=False,
            clerk_identity_removed=result.clerk_identity_removed,
        )

    doctor: Doctor | None = getattr(request.state, "doctor", None)
    if doctor is None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Your Account has Been Disabled",
        )

    if doctor.account_deletion_status == "deleted":
        return DeleteMyAccountResponse(
            status="already_deleted",
            practice_left_ownerless=False,
            clerk_identity_removed=True,
        )

    _require_fresh_session(request)
    try:
        result = delete_own_account(
            doctor_id=doctor.id,
            confirm=body.confirm,
            authenticated_doctor_id=doctor.id,
            session=session,
        )
    except DoctorSelfDeleteError as error:
        raise _map_self_delete_error(error) from error

    return DeleteMyAccountResponse(
        status=result.status,
        practice_left_ownerless=result.practice_left_ownerless,
        clerk_identity_removed=result.clerk_identity_removed,
    )
