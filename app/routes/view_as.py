"""Explicit begin/end/status API for read-only support view-as."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict
from typeid import TypeID
from typeid.errors import TypeIDException

from app.routes.dependencies.view_as import (
    SESSION_KEY_VIEW_AS_ID,
    SESSION_KEY_VIEW_AS_NONCE,
    _actor_identity,
    _clear_browser_view_as,
)
from app.services.superadmin_capability import (
    SuperadminCapabilityError,
    SuperadminCapabilityKind,
    configured_superadmin_capability_environment,
    get_runtime_superadmin_capability,
)
from app.services.view_as import (
    INACTIVE_VIEW_AS_REQUEST_BINDING,
    ResolvedViewAs,
    ViewAsError,
    audit_view_as_begin_rejection,
    target_diagnostic,
    view_as_request_binding,
)
from app.services.view_as import (
    begin_view_as as begin_view_as_session,
)
from app.services.view_as import (
    end_view_as as end_view_as_session,
)

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.doctor import Doctor
from sqlmodel import select


def _disable_view_as_caching(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


view_as_api_app = APIRouter(
    prefix="/view-as",
    tags=["view-as"],
    dependencies=[Depends(_disable_view_as_caching)],
)


class ViewAsAccountSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doctor_id: str
    email: str | None


class ViewAsStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    can_view_as: bool
    active: bool
    read_only: bool
    diagnostic_code: str | None
    expires_at: datetime | None
    actor: ViewAsAccountSummary | None
    target: ViewAsAccountSummary | None
    request_binding: str


class ViewAsTargetSummary(ViewAsAccountSummary):
    diagnostic_code: str | None


class ViewAsTargetListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targets: list[ViewAsTargetSummary]


class BeginViewAsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_doctor_id: str


def _summary(doctor: Doctor) -> ViewAsAccountSummary:
    return ViewAsAccountSummary(
        doctor_id=str(doctor.id),
        email=doctor.email,
    )


def _active_capability(request: Request, *, required: bool):
    actor: Doctor = request.state.view_as_actor
    identity = _actor_identity(request)
    try:
        with get_session() as session:
            capability = get_runtime_superadmin_capability(
                session,
                issuer=identity.issuer,
                subject=identity.subject,
                doctor_id=actor.id,
                capability_kind=SuperadminCapabilityKind.view_as,
            )
    except SuperadminCapabilityError:
        capability = None
    if required and capability is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="View-as capability is required.",
        )
    return capability


def _status_response(request: Request) -> ViewAsStatusResponse:
    actor: Doctor = request.state.view_as_actor
    active = cast(ResolvedViewAs | None, request.state.view_as)
    can_view_as = (
        active is not None or _active_capability(request, required=False) is not None
    )
    return ViewAsStatusResponse(
        can_view_as=can_view_as,
        active=active is not None,
        read_only=active is not None,
        diagnostic_code=active.diagnostic_code if active else None,
        expires_at=active.session.absolute_expires_at if active else None,
        actor=_summary(actor) if can_view_as else None,
        target=_summary(active.target)
        if active and active.target is not None
        else None,
        request_binding=(
            view_as_request_binding(active.session)
            if active is not None
            else INACTIVE_VIEW_AS_REQUEST_BINDING
        ),
    )


@view_as_api_app.get("/status", name="view_as_status")
def view_as_status(request: Request) -> ViewAsStatusResponse:
    return _status_response(request)


@view_as_api_app.get("/targets", name="view_as_targets")
def view_as_targets(request: Request) -> ViewAsTargetListResponse:
    _active_capability(request, required=True)
    actor: Doctor = request.state.view_as_actor
    with get_session() as session:
        doctors = list(
            session.exec(
                select(Doctor)
                .where(
                    Doctor.id != actor.id,
                    Doctor.deleted_at == None,  # noqa: E711
                )
                .order_by(
                    cast(Any, Doctor.email).asc().nulls_last(),
                    cast(Any, Doctor.id).asc(),
                )
            ).all()
        )
        targets = [
            ViewAsTargetSummary(
                **_summary(doctor).model_dump(),
                diagnostic_code=target_diagnostic(session, doctor),
            )
            for doctor in doctors
        ]
    return ViewAsTargetListResponse(targets=targets)


@view_as_api_app.post("/sessions", name="begin_view_as")
def begin_view_as(
    request: Request,
    data: BeginViewAsBody,
) -> ViewAsStatusResponse:
    # Establish immutable capability authority before parsing or auditing any
    # caller-controlled target value. Unauthorized malformed requests must not
    # be able to grow the append-only audit table.
    _active_capability(request, required=True)
    actor: Doctor = request.state.view_as_actor
    try:
        target_id = TypeID.from_string(data.target_doctor_id)
        if target_id.prefix != "doctor":
            raise ValueError
    except (TypeIDException, ValueError):
        try:
            environment = configured_superadmin_capability_environment()
            with get_session() as session:
                has_capability = audit_view_as_begin_rejection(
                    session,
                    actor=actor,
                    identity=_actor_identity(request),
                    environment=environment,
                    requested_reason_code="target_unavailable",
                )
        except (SuperadminCapabilityError, ViewAsError):
            has_capability = False
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
                if has_capability
                else status.HTTP_403_FORBIDDEN
            ),
            detail=(
                "View-as target is unavailable."
                if has_capability
                else "View-as capability is required."
            ),
        ) from None
    try:
        environment = configured_superadmin_capability_environment()
        with get_session() as session:
            result = begin_view_as_session(
                session,
                actor=actor,
                target_doctor_id=cast(TypeIDType, target_id),
                identity=_actor_identity(request),
                environment=environment,
            )
            target = session.get(Doctor, result.session.target_doctor_id)
            if target is None:
                raise ViewAsError("view-as target is unavailable")
    except (SuperadminCapabilityError, ViewAsError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="View-as could not be activated.",
        ) from None

    request.session[SESSION_KEY_VIEW_AS_ID] = str(result.session.id)
    request.session[SESSION_KEY_VIEW_AS_NONCE] = result.browser_nonce
    request.state.view_as = ResolvedViewAs(
        session=result.session,
        actor=actor,
        target=target,
        diagnostic_code=result.diagnostic_code,
    )
    return _status_response(request)


@view_as_api_app.delete("/sessions/current", name="end_view_as")
def end_view_as(request: Request) -> ViewAsStatusResponse:
    actor: Doctor = request.state.view_as_actor
    session_id = request.session.get(SESSION_KEY_VIEW_AS_ID)
    try:
        environment = configured_superadmin_capability_environment()
        if isinstance(session_id, str):
            with get_session() as session:
                end_view_as_session(
                    session,
                    view_as_session_id=session_id,
                    actor=actor,
                    identity=_actor_identity(request),
                    environment=environment,
                )
    except (SuperadminCapabilityError, ViewAsError):
        pass
    _clear_browser_view_as(request)
    request.state.view_as = None
    return _status_response(request)
