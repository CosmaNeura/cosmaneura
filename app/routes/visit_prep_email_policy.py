"""Authenticated physician policy review/decision; never a send command."""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal, TypeVar

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import AwareDatetime, BeforeValidator, Field

from app.routes.dependencies.visit_prep import require_visit_prep_package_read
from app.routes.visit_prep_packages import _query_keys
from app.routes.visit_prep_preparations import (
    _COMMON_ERROR_RESPONSES,
    _scope,
    _StrictModel,
)
from app.routes.visit_prep_synthesis import _SensitiveSynthesisRoute
from app.services import visit_prep_access
from app.services.visit_prep_access import VisitPrepAccessDecision
from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_prep_email_policy import record_email_policy_decision
from app.services.visit_prep_email_policy_view import get_email_policy_view

_ResultT = TypeVar("_ResultT")


def _explicit_timestamp(value: object) -> object:
    if value is not None and (
        not isinstance(value, str) or len(value) > 64 or value[10:11] not in {"T", "t"}
    ):
        raise ValueError("an explicit timezone-aware timestamp is required")
    return value


class VisitPrepStandardCadenceResponse(_StrictModel):
    contract: Literal["visit_prep.standard_cadence.v1"] = (
        "visit_prep.standard_cadence.v1"
    )
    managed_by: Literal["cosmaneura"] = "cosmaneura"
    provider_editable: Literal[False] = False
    timezone_basis: Literal["current_appointment"] = "current_appointment"
    version: str
    fingerprint: str
    quiet_start_hour: int
    quiet_end_hour: int
    minimum_channel_spacing_hours: int
    unopened_reminder_hours_before_visit: Literal[48] = 48
    unfinished_reminder_hours_before_visit: Literal[12] = 12


class VisitPrepEmailPolicyResponse(_StrictModel):
    contract: Literal["visit_prep.email_policy.v1"] = "visit_prep.email_policy.v1"
    policy_fingerprint: str
    template_version: str
    subject: str
    template_text: str
    status: Literal[
        "not_approved", "approved", "withdrawn", "expired", "policy_changed"
    ]
    event_sequence: int
    decision_policy_fingerprint: str | None
    recorded_at: datetime | None
    expires_at: datetime | None
    checked_at: datetime
    delivery_status: Literal["not_connected"] = "not_connected"
    standard_cadence: VisitPrepStandardCadenceResponse | None = None


class VisitPrepEmailPolicyDecisionRequest(_StrictModel):
    expected_event_sequence: int = Field(ge=0, lt=2_147_483_647, strict=True)
    event_kind: Literal["approved", "withdrawn"]
    policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$", strict=True)
    expires_at: Annotated[AwareDatetime | None, BeforeValidator(_explicit_timestamp)]


class VisitPrepEmailPolicyDecisionResponse(_StrictModel):
    event_sequence: int
    current_event_sequence: int
    event_kind: Literal["approved", "withdrawn"]
    policy_fingerprint: str
    expires_at: datetime | None
    recorded_at: datetime
    disposition: Literal["recorded", "replayed"]


visit_prep_email_policy_api_app = APIRouter(
    prefix="/visit-prep/email-policy",
    tags=["visit-prep"],
    route_class=_SensitiveSynthesisRoute,
    responses={
        **_COMMON_ERROR_RESPONSES,
        413: {"description": "Request is too large."},
    },
)


def _invoke(operation: Callable[[], _ResultT]) -> _ResultT:
    failed = False
    result = None
    try:
        result = operation()
    except VisitPrepEmailInvalid:
        failed = True
    if failed or result is None:
        # The domain intentionally does not expose whether a private persistence,
        # authority or stale-state failure occurred. Never advise a fresh-key retry.
        raise HTTPException(
            409,
            "Visit Prep email policy is unavailable. Reload before another decision.",
        )
    return result


def _owner_args(request: Request, decision: VisitPrepAccessDecision) -> dict:
    owner, doctor, practice = _scope(decision)
    return {
        "owner_practice_member_id": owner,
        "owner_doctor_id": doctor,
        "practice_id": practice,
        "expected_clerk_subject": request.state.doctor.clerk_id,
        "settings": visit_prep_access.VISIT_PREP_SETTINGS,
        "view_as_active": getattr(request.state, "view_as", None) is not None,
    }


@visit_prep_email_policy_api_app.get(
    "", name="visit_prep_email_policy_read", response_model=VisitPrepEmailPolicyResponse
)
def visit_prep_email_policy_read(
    request: Request,
    decision: Annotated[
        VisitPrepAccessDecision, Depends(require_visit_prep_package_read)
    ],
) -> VisitPrepEmailPolicyResponse:
    _query_keys(request, frozenset())
    value = _invoke(lambda: get_email_policy_view(**_owner_args(request, decision)))
    return VisitPrepEmailPolicyResponse(
        policy_fingerprint=value.policy_fingerprint,
        template_version=value.template_version,
        subject=value.subject,
        template_text=value.template_text,
        status=value.status,
        event_sequence=value.event_sequence,
        decision_policy_fingerprint=value.decision_policy_fingerprint,
        recorded_at=value.recorded_at,
        expires_at=value.expires_at,
        checked_at=value.checked_at,
        standard_cadence=VisitPrepStandardCadenceResponse(
            version=value.standard_cadence.version,
            fingerprint=value.standard_cadence.fingerprint,
            quiet_start_hour=value.standard_cadence.quiet_start_hour,
            quiet_end_hour=value.standard_cadence.quiet_end_hour,
            minimum_channel_spacing_hours=value.standard_cadence.minimum_channel_spacing_hours,
        )
        if value.standard_cadence is not None
        else None,
    )


@visit_prep_email_policy_api_app.post(
    "",
    name="visit_prep_email_policy_decide",
    response_model=VisitPrepEmailPolicyDecisionResponse,
)
def visit_prep_email_policy_decide(
    request: Request,
    payload: VisitPrepEmailPolicyDecisionRequest,
    operation_id: Annotated[
        str, Header(alias="Idempotency-Key", min_length=36, max_length=36)
    ],
    decision: Annotated[
        VisitPrepAccessDecision, Depends(require_visit_prep_package_read)
    ],
) -> VisitPrepEmailPolicyDecisionResponse:
    _query_keys(request, frozenset())
    if len(request.headers.getlist("Idempotency-Key")) != 1:
        raise HTTPException(422, "Visit Prep request is invalid.")
    result = _invoke(
        lambda: record_email_policy_decision(
            **_owner_args(request, decision),
            operation_id=operation_id,
            expected_event_sequence=payload.expected_event_sequence,
            event_kind=payload.event_kind,
            policy_fingerprint=payload.policy_fingerprint,
            expires_at=payload.expires_at,
        )
    )
    return VisitPrepEmailPolicyDecisionResponse(
        event_sequence=result.event_sequence,
        current_event_sequence=result.current_event_sequence,
        event_kind=result.event_kind,
        policy_fingerprint=result.policy_fingerprint,
        expires_at=result.expires_at,
        recorded_at=result.recorded_at,
        disposition=result.disposition,
    )
