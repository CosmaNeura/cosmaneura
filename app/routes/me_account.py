"""Authenticated self-delete surface for Scribe (Apple 5.1.1(v)).

Path contract for the Scribe iOS app:

    DELETE /v1/me/account
    Authorization: Bearer <Clerk session JWT>
    Body: {"confirm": "DELETE"}

Identity comes only from the verified token. The body must not (and cannot)
name a target user. Response is JSON with status deleted|already_deleted.
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
    DoctorSelfDeleteError,
    delete_own_account,
)
from app.services.external_webhook_inbox import provider_log_ref

from activemodel.session_manager import get_session
from app.models.doctor import Doctor
from sqlmodel import Session

authenticate_clerk_request_middleware = AuthenticateClerkRequest(CLERK_PRIVATE_KEY)


def require_account_self_delete_enabled() -> None:
    """Refuse the surface while the latch is OFF.

    Runs before auth and doctor injection so a dark deployment performs no
    Clerk call, DB write, or audit side effect beyond normal request logging.
    """

    if not ACCOUNT_SELF_DELETE_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


def inject_doctor_for_self_delete(request: Request) -> None:
    """Resolve the caller without recreating a fully deleted account.

    Unlike the general inject_doctor_record dependency, a completed self-delete
    is admitted so the handler can return already_deleted instead of 410.
    """

    # Defense in depth: never touch the DB while the latch is dark, even if a
    # dependency solver runs this after a failed gate check.
    if not ACCOUNT_SELF_DELETE_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    clerk_id = request.state.auth_state.payload["sub"]
    doctor = Doctor.get(clerk_id=clerk_id)
    if doctor is None:
        doctor = Doctor.upsert(data={"clerk_id": clerk_id}, unique_by="clerk_id")
    elif doctor.account_deletion_status in {"deleted", "deletion_pending"}:
        # Admit completed deletes (already_deleted) and mid-flight retries so
        # Clerk identity removal can finish after a transient provider failure.
        request.state.doctor = doctor
        doctor_ref = provider_log_ref(f"doctor:{doctor.id}")
        context["doctor_ref"] = doctor_ref
        sentry_sdk.set_user({"id": doctor_ref})
        return

    if doctor.deleted_at is not None:
        # Operator closure or incomplete state — do not treat as self-delete
        # idempotency; the account is disabled.
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Your Account has Been Disabled"
        )

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
        Depends(inject_doctor_for_self_delete),
    ],
)


class DeleteMyAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: str = Field(min_length=1, max_length=32)
    "Must be the exact phrase DELETE. No target user id is accepted."


class DeleteMyAccountResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    practice_left_ownerless: bool
    clerk_identity_removed: bool


def _session_dependency():
    with get_session() as session:
        yield session


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
    """Delete the signed-in user's Cosmaneura identity and personal data."""

    if not ACCOUNT_SELF_DELETE_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    deny_view_as_browser_activity(request)
    doctor: Doctor = request.state.doctor

    if doctor.account_deletion_status == "deleted":
        return DeleteMyAccountResponse(
            status="already_deleted",
            practice_left_ownerless=False,
            clerk_identity_removed=True,
        )

    try:
        result = delete_own_account(
            doctor_id=doctor.id,
            confirm=body.confirm,
            session=session,
        )
    except DoctorSelfDeleteError as error:
        detail = str(error)
        code = status.HTTP_400_BAD_REQUEST
        if "confirm" in detail:
            code = status.HTTP_422_UNPROCESSABLE_ENTITY
        elif "capabilities" in detail:
            code = status.HTTP_409_CONFLICT
        raise HTTPException(status_code=code, detail=detail) from error

    return DeleteMyAccountResponse(
        status=result.status,
        practice_left_ownerless=result.practice_left_ownerless,
        clerk_identity_removed=result.clerk_identity_removed,
    )
