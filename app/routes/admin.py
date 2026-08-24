"""Capability-protected platform administration routes."""

import csv
import io
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel as BasePydanticModel
from starlette import status

from app.services import external_webhook_inbox
from app.services import usage as usage_service
from app.services.superadmin_capability import (
    SuperadminCapabilityKind,
    get_runtime_superadmin_capability,
)
from app.services.usage import (
    DailyVolumeRow,
    DoctorUsageRow,
    PendingUploadRow,
    RecentAppointmentRow,
    TimeToNoteRow,
)

from activemodel.session_manager import get_session
from app.models.baa import BaaAcceptance, BaaVersion


def require_admin(request: Request) -> None:
    """Require an exact, revocable environment-scoped platform capability."""

    actor = getattr(request.state, "view_as_actor", None)
    payload = getattr(getattr(request.state, "auth_state", None), "payload", None)
    issuer = payload.get("iss") if isinstance(payload, dict) else None
    subject = payload.get("sub") if isinstance(payload, dict) else None
    capability = None
    if (
        actor is not None
        and isinstance(issuer, str)
        and isinstance(subject, str)
        and actor.clerk_id == subject
    ):
        with get_session() as session:
            capability = get_runtime_superadmin_capability(
                session,
                issuer=issuer,
                subject=subject,
                doctor_id=actor.id,
                capability_kind=SuperadminCapabilityKind.platform_admin,
            )
    if capability is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


admin_api_app = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


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
    failed_operations: int


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
