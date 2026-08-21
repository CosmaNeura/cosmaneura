"""
Admin-only routes:

- login_as / user impersonation, similar to https://clerk.com/docs/users/user-impersonation but without a $100/month sub
"""

import csv
import io
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel as BasePydanticModel
from pydantic import ConfigDict
from starlette import status
from typeid import TypeID

from app import log
from app.services import external_webhook_inbox
from app.services import usage as usage_service
from app.services.external_webhook_inbox import provider_log_ref
from app.services.usage import (
    DailyVolumeRow,
    DoctorUsageRow,
    PendingUploadRow,
    RecentAppointmentRow,
    TimeToNoteRow,
)

from app.models.baa import BaaAcceptance, BaaVersion
from app.models.doctor import Doctor, DoctorRole

SESSION_KEY_LOGIN_AS_USER = "login_as_user"


def require_admin(request: Request):
    "Protect routes that require admin access"

    admin_user = getattr(request.state, "doctor", None)
    if admin_user is None or admin_user.role != DoctorRole.admin:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


admin_api_app = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


class UserSwitchData(BasePydanticModel):
    model_config = ConfigDict(from_attributes=True)

    clerk_id: str
    id: TypeID
    email: str | None


class UserListResponse(BasePydanticModel):
    current_user: UserSwitchData | None
    users: list[UserSwitchData]


@admin_api_app.get("/users")
def user_list(request: Request) -> UserListResponse:
    # remember, these routes are protected from the login_as functionality
    login_as_user = None

    if SESSION_KEY_LOGIN_AS_USER in request.session:
        login_as_clerk_id = request.session[SESSION_KEY_LOGIN_AS_USER]
        login_as_user = Doctor.get(clerk_id=login_as_clerk_id)

    return UserListResponse(
        current_user=login_as_user,  # type: ignore
        users=list(
            Doctor.select(Doctor.clerk_id, Doctor.email, Doctor.id)
            .where(Doctor.role != DoctorRole.admin)
            .all()
        ),  # type: ignore
    )


@admin_api_app.post("/login_as/{user_id}")
def login_as_user(request: Request, user_id: Annotated[str, Path()]):
    if request.state.doctor.clerk_id == user_id:
        log.info("removing login_as")
        request.session[SESSION_KEY_LOGIN_AS_USER] = None
        return

    login_as_user = Doctor.where(
        Doctor.role != DoctorRole.admin, Doctor.clerk_id == user_id
    ).one()

    log.info(
        "login_as_user",
        user_ref=provider_log_ref(str(cast(Any, login_as_user).id)),
    )

    request.session[SESSION_KEY_LOGIN_AS_USER] = login_as_user.clerk_id  # type: ignore
    return


class UsageResponse(BasePydanticModel):
    recent_appointments: list[RecentAppointmentRow]
    doctor_usage: list[DoctorUsageRow]
    daily_volume: list[DailyVolumeRow]
    pending_uploads: list[PendingUploadRow]
    time_to_note: list[TimeToNoteRow]


class WebhookInboxHealthResponse(BasePydanticModel):
    pending: int
    processing: int
    retry_wait: int
    completed: int
    dead_letter: int
    oldest_actionable_age_seconds: float | None
    uncertain_operations: int


class WebhookRedriveResponse(BasePydanticModel):
    status: str


@admin_api_app.get("/usage")
def usage_dashboard() -> UsageResponse:
    """Five platform-usage queries the boss runs by hand, lifted to a
    single authenticated endpoint. Admin-only via require_admin on the
    router. Issue #34."""
    return UsageResponse(
        recent_appointments=usage_service.recent_appointments(),
        doctor_usage=usage_service.doctor_usage(),
        daily_volume=usage_service.daily_volume(),
        pending_uploads=usage_service.pending_uploads(),
        time_to_note=usage_service.time_to_note(),
    )


@admin_api_app.get("/webhook-inbox/health", include_in_schema=False)
def webhook_inbox_health() -> WebhookInboxHealthResponse:
    """Metadata-only health view; encrypted payloads are never returned."""

    return WebhookInboxHealthResponse(**external_webhook_inbox.inbox_health())


@admin_api_app.post(
    "/webhook-inbox/{delivery_id}/redrive",
    include_in_schema=False,
)
def webhook_inbox_redrive(
    delivery_id: Annotated[str, Path()],
) -> WebhookRedriveResponse:
    """Redrive an unexpired dead letter without exposing its provider payload."""

    try:
        accepted = external_webhook_inbox.redrive_delivery(delivery_id)
    except Exception:
        accepted = False
    if not accepted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Delivery cannot be redriven.",
        )
    return WebhookRedriveResponse(status="accepted")


class BaaAcceptanceExportRow(BasePydanticModel):
    practice_legal_name: str
    doc_id: str
    version: str
    accepted_by_name: str
    accepted_by_title: str
    accepted_by_email: str
    accepted_at_utc: str
    ip_address: str | None
    user_agent: str | None
    content_sha256: str


@admin_api_app.get("/baa-acceptances")
def baa_acceptances_export(format: str = "json") -> Response:
    """Compliance export of every BAA acceptance record (CSV or JSON).
    Admin-only via require_admin on the router."""

    versions = {v.id: v for v in BaaVersion.all()}
    rows = [
        BaaAcceptanceExportRow(
            practice_legal_name=r.covered_entity_legal_name,
            doc_id=versions[r.baa_version_id].doc_id,
            version=versions[r.baa_version_id].version,
            accepted_by_name=r.accepted_by_name,
            accepted_by_title=r.accepted_by_title,
            accepted_by_email=r.accepted_by_email,
            accepted_at_utc=r.accepted_at_utc.isoformat(),
            ip_address=r.ip_address,
            user_agent=r.user_agent,
            content_sha256=r.content_sha256,
        )
        for r in BaaAcceptance.all()
    ]

    if format == "csv":
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer, fieldnames=list(BaaAcceptanceExportRow.model_fields)
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row.model_dump())
        return Response(
            content=buffer.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=baa-acceptances.csv"},
        )
    return JSONResponse(content=[row.model_dump() for row in rows])
