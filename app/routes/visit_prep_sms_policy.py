"""Explicit owner SMS policy review and choices, not outreach authority."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import AwareDatetime, BeforeValidator, Field

from app.routes.dependencies.visit_prep import require_visit_prep_package_read
from app.routes.visit_prep_email_policy import (
    VisitPrepStandardCadenceResponse,
    _explicit_timestamp,
    _owner_args,
)
from app.routes.visit_prep_packages import _query_keys
from app.routes.visit_prep_preparations import _COMMON_ERROR_RESPONSES, _StrictModel
from app.routes.visit_prep_synthesis import _SensitiveSynthesisRoute
from app.services.visit_prep_access import VisitPrepAccessDecision
from app.services.visit_prep_sms_contract import VisitPrepSMSUnavailable
from app.services.visit_prep_sms_policy import record_sms_policy_decision
from app.services.visit_prep_sms_policy_view import SMSPolicyStatus, get_sms_policy_view


class VisitPrepSMSTemplatesResponse(_StrictModel):
    booking_sms: str
    unopened_48h_sms: str
    unfinished_12h_sms: str


class VisitPrepSMSPolicyResponse(_StrictModel):
    contract: Literal["visit_prep.sms_policy.v1"] = "visit_prep.sms_policy.v1"
    policy_fingerprint: str
    policy_text: str
    template_version: Literal["visit_prep_sms_v1"] = "visit_prep_sms_v1"
    sender_digest: str | None
    templates: VisitPrepSMSTemplatesResponse
    status: SMSPolicyStatus
    event_sequence: int
    decision_policy_fingerprint: str | None
    decision_sender_digest: str | None
    recorded_at: datetime | None
    expires_at: datetime | None
    checked_at: datetime
    delivery_status: Literal["not_connected"] = "not_connected"
    standard_cadence: VisitPrepStandardCadenceResponse | None


class VisitPrepSMSPolicyDecisionRequest(_StrictModel):
    expected_event_sequence: int = Field(ge=0, lt=2_147_483_647, strict=True)
    event_kind: Literal["approved", "withdrawn"]
    policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$", strict=True)
    expected_sender_digest: str = Field(pattern=r"^[0-9a-f]{64}$", strict=True)
    expires_at: Annotated[AwareDatetime | None, BeforeValidator(_explicit_timestamp)]


class VisitPrepSMSPolicyDecisionResponse(_StrictModel):
    event_sequence: int
    current_event_sequence: int
    event_kind: Literal["approved", "withdrawn"]
    policy_fingerprint: str
    expires_at: datetime | None
    recorded_at: datetime
    disposition: Literal["recorded", "replayed"]


visit_prep_sms_policy_api_app = APIRouter(
    prefix="/visit-prep/sms-policy",
    tags=["visit-prep"],
    route_class=_SensitiveSynthesisRoute,
    responses={
        **_COMMON_ERROR_RESPONSES,
        413: {"description": "Request is too large."},
    },
)


def _invoke(operation):
    result = None
    try:
        result = operation()
    except VisitPrepSMSUnavailable:
        pass
    if result is None:
        raise HTTPException(
            409, "Visit Prep SMS policy is unavailable. Reload before another decision."
        )
    return result


@visit_prep_sms_policy_api_app.get(
    "", name="visit_prep_sms_policy_read", response_model=VisitPrepSMSPolicyResponse
)
def visit_prep_sms_policy_read(
    request: Request,
    decision: Annotated[
        VisitPrepAccessDecision, Depends(require_visit_prep_package_read)
    ],
) -> VisitPrepSMSPolicyResponse:
    _query_keys(request, frozenset())
    value = _invoke(lambda: get_sms_policy_view(**_owner_args(request, decision)))
    cadence = value.standard_cadence
    return VisitPrepSMSPolicyResponse(
        policy_fingerprint=value.policy_fingerprint,
        policy_text=value.policy_text,
        sender_digest=value.sender_digest,
        templates=VisitPrepSMSTemplatesResponse(**value.templates),
        status=value.status,
        event_sequence=value.event_sequence,
        decision_policy_fingerprint=value.decision_policy_fingerprint,
        decision_sender_digest=value.decision_sender_digest,
        recorded_at=value.recorded_at,
        expires_at=value.expires_at,
        checked_at=value.checked_at,
        standard_cadence=VisitPrepStandardCadenceResponse(
            version=cadence.version,
            fingerprint=cadence.fingerprint,
            quiet_start_hour=cadence.quiet_start_hour,
            quiet_end_hour=cadence.quiet_end_hour,
            minimum_channel_spacing_hours=cadence.minimum_channel_spacing_hours,
        )
        if cadence
        else None,
    )


@visit_prep_sms_policy_api_app.post(
    "",
    name="visit_prep_sms_policy_decide",
    response_model=VisitPrepSMSPolicyDecisionResponse,
)
def visit_prep_sms_policy_decide(
    request: Request,
    payload: VisitPrepSMSPolicyDecisionRequest,
    operation_id: Annotated[
        str, Header(alias="Idempotency-Key", min_length=36, max_length=36)
    ],
    decision: Annotated[
        VisitPrepAccessDecision, Depends(require_visit_prep_package_read)
    ],
) -> VisitPrepSMSPolicyDecisionResponse:
    _query_keys(request, frozenset())
    if len(request.headers.getlist("Idempotency-Key")) != 1:
        raise HTTPException(422, "Visit Prep request is invalid.")
    # This digest is an optimistic review binding, not a client authority claim.
    # The locked service compares new approvals to actual current routing and
    # withdrawals/retries to the exact retained decision; a stale tab cannot
    # approve a replacement sender. Original-key replay remains historical.
    value = _invoke(
        lambda: record_sms_policy_decision(
            **_owner_args(request, decision),
            operation_id=operation_id,
            expected_event_sequence=payload.expected_event_sequence,
            event_kind=payload.event_kind,
            sender_digest=payload.expected_sender_digest,
            policy_fingerprint=payload.policy_fingerprint,
            expires_at=payload.expires_at,
        )
    )
    return VisitPrepSMSPolicyDecisionResponse(
        event_sequence=value.event_sequence,
        current_event_sequence=value.current_event_sequence,
        event_kind=value.event_kind,
        policy_fingerprint=value.policy_fingerprint,
        expires_at=value.expires_at,
        recorded_at=value.recorded_at,
        disposition=value.disposition,
    )
