"""Internal physician commands for creating and managing Visit Preps.

This router is deliberately separate from the patient-facing surface.  It
never delivers invitations and never accepts caller-supplied ownership or
actor identifiers.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Annotated, Any, Literal, TypeVar, cast
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Path,
    Request,
    Response,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse
from typeid import TypeID
from typeid.errors import TypeIDException

import app.services.visit_preps as visit_prep_service
from app import log
from app.constants import INTAKE_CHAT_DOMAIN
from app.routes.dependencies.visit_prep import require_visit_prep_write
from app.services.visit_prep_access import VisitPrepAccessDecision
from app.services.visit_preps import (
    RecipientInput,
    VisitPrepConflict,
    VisitPrepMutationResult,
    VisitPrepNotFound,
    VisitPrepUnavailable,
    VisitPrepValidation,
)

from activemodel.types import TypeIDType
from sqlalchemy.exc import SQLAlchemyError

_NOT_FOUND_DETAIL = "Not found"
_VALIDATION_DETAIL = "Visit Prep request is invalid."
_CONFLICT_DETAIL = "Visit Prep operation conflicts with its current state."
_UNAVAILABLE_DETAIL = "Visit Prep is temporarily unavailable."

_ResultT = TypeVar("_ResultT")


class _SensitiveVisitPrepRoute(APIRoute):
    """Redact rejected contact, schedule, identifier, and header values."""

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        route_handler = super().get_route_handler()

        async def redacted_route_handler(request: Request) -> Response:
            try:
                response = await route_handler(request)
            except RequestValidationError:
                response = JSONResponse(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    content={"detail": _VALIDATION_DETAIL},
                    headers={
                        "Cache-Control": "no-store",
                        "Referrer-Policy": "no-referrer",
                    },
                )
            except HTTPException as error:
                response = JSONResponse(
                    status_code=error.status_code,
                    content={"detail": error.detail},
                    headers=error.headers,
                )
            _private_headers(response)
            return response

        return redacted_route_handler


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VisitPrepCommandErrorResponse(_StrictModel):
    detail: str


_COMMON_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_403_FORBIDDEN: {
        "model": VisitPrepCommandErrorResponse,
        "description": "The authenticated context cannot perform this operation.",
    },
    status.HTTP_404_NOT_FOUND: {
        "model": VisitPrepCommandErrorResponse,
        "description": "Visit Prep is unavailable or the resource was not found.",
    },
    status.HTTP_409_CONFLICT: {
        "model": VisitPrepCommandErrorResponse,
        "description": "The command conflicts with current Visit Prep state.",
    },
    status.HTTP_422_UNPROCESSABLE_ENTITY: {
        "model": VisitPrepCommandErrorResponse,
        "description": "The request is invalid.",
    },
    status.HTTP_503_SERVICE_UNAVAILABLE: {
        "model": VisitPrepCommandErrorResponse,
        "description": "Visit Prep persistence is temporarily unavailable.",
    },
}


visit_prep_preparations_api_app = APIRouter(
    prefix="/visit-prep",
    tags=["visit-prep"],
    route_class=_SensitiveVisitPrepRoute,
    responses=_COMMON_ERROR_RESPONSES,
)


class VisitPrepRecipientRequest(_StrictModel):
    first_name: str = Field(min_length=1, max_length=120, repr=False)
    role: Literal["patient", "caregiver"] = "patient"
    email: str | None = Field(default=None, max_length=320, repr=False)
    phone: str | None = Field(default=None, max_length=16, repr=False)


class VisitPrepCreateRequest(_StrictModel):
    recipient: VisitPrepRecipientRequest = Field(repr=False)
    physician_display_name: str = Field(min_length=1, max_length=160)
    scheduled_at: datetime
    timezone_name: str = Field(min_length=1, max_length=64)
    question_set_version_id: str
    patient_id: str | None = None
    appointment_id: str | None = None


class VisitPrepCopyLinkRequest(_StrictModel):
    expected_credential_generation: int = Field(gt=0, strict=True)


class VisitPrepRevokeRequest(VisitPrepCopyLinkRequest):
    reason: Literal[
        "physician_revoked",
        "wrong_recipient",
        "appointment_cancelled",
    ]


class VisitPrepRescheduleRequest(VisitPrepCopyLinkRequest):
    expected_schedule_generation: int = Field(gt=0, strict=True)
    scheduled_at: datetime
    timezone_name: str = Field(min_length=1, max_length=64)


class VisitPrepReconcileRequest(_StrictModel):
    patient_id: str | None = None
    appointment_id: str | None = None


class VisitPrepCommandStateResponse(_StrictModel):
    id: str
    question_set_version_id: str
    patient_id: str | None
    appointment_id: str | None
    lifecycle_state: str
    has_email_contact: bool
    has_sms_contact: bool
    physician_display_name: str
    practice_display_name: str
    scheduled_at: datetime
    timezone_name: str
    schedule_generation: int
    credential_generation: int
    credential_expires_at: datetime
    credential_active: bool
    revoked_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None


class VisitPrepCommandResponse(_StrictModel):
    visit_prep: VisitPrepCommandStateResponse
    disposition: Literal[
        "created",
        "replayed",
        "rotated",
        "revoked",
        "rescheduled",
        "reconciled",
    ]
    access_url: str | None = Field(repr=False)


def _scope(
    decision: VisitPrepAccessDecision,
) -> tuple[TypeIDType, TypeIDType, TypeIDType]:
    if decision.practice_member_id is None or decision.practice_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_NOT_FOUND_DETAIL,
        )
    return decision.practice_member_id, decision.doctor_id, decision.practice_id


def _type_id(value: str, *, prefix: str, path: bool) -> TypeIDType:
    try:
        parsed = TypeID.from_string(value)
        if parsed.prefix != prefix or str(parsed) != value:
            raise ValueError
    except (TypeIDException, ValueError):
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
                if path
                else status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=_NOT_FOUND_DETAIL if path else _VALIDATION_DETAIL,
        ) from None
    return cast(TypeIDType, parsed)


def _optional_type_id(value: str | None, *, prefix: str) -> TypeIDType | None:
    if value is None:
        return None
    return _type_id(value, prefix=prefix, path=False)


def _invoke(operation: Callable[[], _ResultT]) -> _ResultT:
    try:
        return operation()
    except VisitPrepNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_NOT_FOUND_DETAIL,
        ) from None
    except VisitPrepValidation:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_VALIDATION_DETAIL,
        ) from None
    except VisitPrepConflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_CONFLICT_DETAIL,
        ) from None
    except (VisitPrepUnavailable, SQLAlchemyError):
        # Never log tokens, contacts, identifiers, idempotency keys, database
        # errors, or request/model representations.
        log.warning(
            "Visit Prep command persistence unavailable",
            action="visit_prep.command_persistence_unavailable",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_UNAVAILABLE_DETAIL,
        ) from None


def _private_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


def _access_url(access_token: str | None) -> str | None:
    if access_token is None:
        return None
    encoded_token = quote(access_token, safe="")
    # The fragment is not sent in HTTP request targets or Referer headers.  A
    # later public UI will exchange it and immediately remove it from history.
    return f"https://{INTAKE_CHAT_DOMAIN}/visit-prep#access_token={encoded_token}"


def _state_response(
    visit_prep: visit_prep_service.VisitPrepResult,
) -> VisitPrepCommandStateResponse:
    return VisitPrepCommandStateResponse(
        id=str(visit_prep.id),
        question_set_version_id=str(visit_prep.question_set_version_id),
        patient_id=(
            str(visit_prep.patient_id) if visit_prep.patient_id is not None else None
        ),
        appointment_id=(
            str(visit_prep.appointment_id)
            if visit_prep.appointment_id is not None
            else None
        ),
        lifecycle_state=visit_prep.lifecycle_state,
        has_email_contact=visit_prep.has_email_contact,
        has_sms_contact=visit_prep.has_sms_contact,
        physician_display_name=visit_prep.physician_display_name,
        practice_display_name=visit_prep.practice_display_name,
        scheduled_at=visit_prep.scheduled_at,
        timezone_name=visit_prep.timezone_name,
        schedule_generation=visit_prep.schedule_generation,
        credential_generation=visit_prep.credential_generation,
        credential_expires_at=visit_prep.credential_expires_at,
        credential_active=visit_prep.credential_active,
        revoked_at=visit_prep.revoked_at,
        created_at=visit_prep.created_at,
        updated_at=visit_prep.updated_at,
    )


def _response(result: VisitPrepMutationResult) -> VisitPrepCommandResponse:
    return VisitPrepCommandResponse(
        visit_prep=_state_response(result.visit_prep),
        disposition=result.disposition,
        access_url=_access_url(result.access_token),
    )


@visit_prep_preparations_api_app.post(
    "/preps",
    name="visit_prep_create",
    response_model=VisitPrepCommandResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "model": VisitPrepCommandResponse,
            "description": "An idempotent replay; no bearer is returned.",
        }
    },
)
def visit_prep_create(
    data: VisitPrepCreateRequest,
    response: Response,
    decision: Annotated[VisitPrepAccessDecision, Depends(require_visit_prep_write)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=36, max_length=36),
    ],
) -> VisitPrepCommandResponse:
    owner_id, doctor_id, practice_id = _scope(decision)
    result = _invoke(
        lambda: visit_prep_service.create_visit_prep(
            owner_practice_member_id=owner_id,
            owner_doctor_id=doctor_id,
            practice_id=practice_id,
            idempotency_key=idempotency_key,
            recipient=RecipientInput(
                first_name=data.recipient.first_name,
                role=data.recipient.role,
                email=data.recipient.email,
                phone=data.recipient.phone,
            ),
            physician_display_name=data.physician_display_name,
            scheduled_at=data.scheduled_at,
            timezone_name=data.timezone_name,
            question_set_version_id=_type_id(
                data.question_set_version_id,
                prefix="visit_prep_question_set_version",
                path=False,
            ),
            patient_id=_optional_type_id(data.patient_id, prefix="patient"),
            appointment_id=_optional_type_id(
                data.appointment_id,
                prefix="appointment",
            ),
        )
    )
    if result.disposition == "replayed":
        response.status_code = status.HTTP_200_OK
    _private_headers(response)
    return _response(result)


@visit_prep_preparations_api_app.post(
    "/preps/{prep_id}/copy-link",
    name="visit_prep_copy_link",
    response_model=VisitPrepCommandResponse,
)
def visit_prep_copy_link(
    prep_id: Annotated[str, Path()],
    data: VisitPrepCopyLinkRequest,
    response: Response,
    decision: Annotated[VisitPrepAccessDecision, Depends(require_visit_prep_write)],
) -> VisitPrepCommandResponse:
    owner_id, doctor_id, practice_id = _scope(decision)
    result = _invoke(
        lambda: visit_prep_service.rotate_visit_prep_credential(
            owner_practice_member_id=owner_id,
            owner_doctor_id=doctor_id,
            practice_id=practice_id,
            visit_prep_id=_type_id(prep_id, prefix="visit_prep", path=True),
            expected_credential_generation=data.expected_credential_generation,
        )
    )
    _private_headers(response)
    return _response(result)


@visit_prep_preparations_api_app.post(
    "/preps/{prep_id}/revoke",
    name="visit_prep_revoke",
    response_model=VisitPrepCommandResponse,
)
def visit_prep_revoke(
    prep_id: Annotated[str, Path()],
    data: VisitPrepRevokeRequest,
    response: Response,
    decision: Annotated[VisitPrepAccessDecision, Depends(require_visit_prep_write)],
) -> VisitPrepCommandResponse:
    owner_id, doctor_id, practice_id = _scope(decision)
    result = _invoke(
        lambda: visit_prep_service.revoke_visit_prep(
            owner_practice_member_id=owner_id,
            owner_doctor_id=doctor_id,
            practice_id=practice_id,
            visit_prep_id=_type_id(prep_id, prefix="visit_prep", path=True),
            expected_credential_generation=data.expected_credential_generation,
            reason=data.reason,
        )
    )
    _private_headers(response)
    return _response(result)


@visit_prep_preparations_api_app.post(
    "/preps/{prep_id}/reschedule",
    name="visit_prep_reschedule",
    response_model=VisitPrepCommandResponse,
)
def visit_prep_reschedule(
    prep_id: Annotated[str, Path()],
    data: VisitPrepRescheduleRequest,
    response: Response,
    decision: Annotated[VisitPrepAccessDecision, Depends(require_visit_prep_write)],
) -> VisitPrepCommandResponse:
    owner_id, doctor_id, practice_id = _scope(decision)
    result = _invoke(
        lambda: visit_prep_service.reschedule_visit_prep(
            owner_practice_member_id=owner_id,
            owner_doctor_id=doctor_id,
            practice_id=practice_id,
            visit_prep_id=_type_id(prep_id, prefix="visit_prep", path=True),
            expected_schedule_generation=data.expected_schedule_generation,
            expected_credential_generation=data.expected_credential_generation,
            scheduled_at=data.scheduled_at,
            timezone_name=data.timezone_name,
        )
    )
    _private_headers(response)
    return _response(result)


@visit_prep_preparations_api_app.put(
    "/preps/{prep_id}/canonical-links",
    name="visit_prep_reconcile",
    response_model=VisitPrepCommandResponse,
)
def visit_prep_reconcile(
    prep_id: Annotated[str, Path()],
    data: VisitPrepReconcileRequest,
    response: Response,
    decision: Annotated[VisitPrepAccessDecision, Depends(require_visit_prep_write)],
) -> VisitPrepCommandResponse:
    owner_id, doctor_id, practice_id = _scope(decision)
    result = _invoke(
        lambda: visit_prep_service.reconcile_visit_prep(
            owner_practice_member_id=owner_id,
            owner_doctor_id=doctor_id,
            practice_id=practice_id,
            visit_prep_id=_type_id(prep_id, prefix="visit_prep", path=True),
            patient_id=_optional_type_id(data.patient_id, prefix="patient"),
            appointment_id=_optional_type_id(
                data.appointment_id,
                prefix="appointment",
            ),
        )
    )
    _private_headers(response)
    return _response(result)
