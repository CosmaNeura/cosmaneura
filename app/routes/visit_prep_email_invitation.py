"""Explicit provider handoff to the existing outbox, never synchronous send."""

from datetime import datetime
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request
from pydantic import Field

from app.configuration.visit_prep_cadence import current_standard_cadence
from app.constants import INTAKE_CHAT_DOMAIN
from app.routes.dependencies.visit_prep import require_visit_prep_package_read
from app.routes.visit_prep_email_policy import _owner_args
from app.routes.visit_prep_packages import _query_keys
from app.routes.visit_prep_preparations import (
    _StrictModel,
    _type_id,
)
from app.routes.visit_prep_synthesis import _SensitiveSynthesisRoute
from app.services.visit_prep_access import VisitPrepAccessDecision
from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_prep_email_enqueue import (
    _ERRORS,
    enqueue_locked_email_invitation,
)
from app.services.visit_prep_email_source import verify_patient_email_source
from app.services.visit_preps import _visit_prep_session


class VisitPrepEmailInvitationRequest(_StrictModel):
    expected_credential_generation: int = Field(gt=0, le=2_147_483_647, strict=True)
    access_token: str = Field(min_length=47, max_length=47, strict=True, repr=False)


EmailDeliveryState = Literal[
    "queued", "submitting", "accepted", "failed", "suppressed", "uncertain"
]


class VisitPrepEmailInvitationResponse(_StrictModel):
    contract: Literal["visit_prep.email_invitation.v1"] = (
        "visit_prep.email_invitation.v1"
    )
    preparation_id: str
    credential_generation: int
    state: EmailDeliveryState
    disposition: Literal["queued", "replayed"]
    requested_at: datetime
    expires_at: datetime
    dispatch_integration: Literal["default_off_worker"] = "default_off_worker"


visit_prep_email_invitation_api_app = APIRouter(
    prefix="/visit-prep/preps",
    tags=["visit-prep"],
    route_class=_SensitiveSynthesisRoute,
)


@visit_prep_email_invitation_api_app.post(
    "/{prep_id}/email-invitation",
    name="visit_prep_email_invitation",
    response_model=VisitPrepEmailInvitationResponse,
)
def visit_prep_email_invitation(
    request: Request,
    prep_id: Annotated[str, Path(max_length=80)],
    payload: VisitPrepEmailInvitationRequest,
    operation_id: Annotated[
        str, Header(alias="Idempotency-Key", min_length=36, max_length=36)
    ],
    decision: Annotated[
        VisitPrepAccessDecision, Depends(require_visit_prep_package_read)
    ],
) -> VisitPrepEmailInvitationResponse:
    _query_keys(request, frozenset())
    if len(request.headers.getlist("Idempotency-Key")) != 1:
        raise HTTPException(422, "Visit Prep request is invalid.")
    identifier = _type_id(prep_id, prefix="visit_prep", path=True)
    result = None
    try:
        cadence = current_standard_cadence()
        if cadence is None:
            raise VisitPrepEmailInvalid()
        with _visit_prep_session() as session:
            try:
                # Persist during quiet hours: the worker will wait on the SAME
                # encrypted intent. No browser timer needs to retain the bearer.
                result = enqueue_locked_email_invitation(
                    session,
                    **_owner_args(request, decision),
                    visit_prep_id=identifier,
                    expected_credential_generation=payload.expected_credential_generation,
                    access_token=payload.access_token,
                    operation_id=operation_id,
                    patient_origin=f"https://{INTAKE_CHAT_DOMAIN}",
                    source_verifier=verify_patient_email_source,
                )
                current = current_standard_cadence()
                if current is None or current.fingerprint != cadence.fingerprint:
                    raise VisitPrepEmailInvalid()
                session.commit()
            except _ERRORS:
                session.rollback()
                raise
    except _ERRORS:
        result = None
    if result is None:
        raise HTTPException(
            409,
            "Invitation handoff unavailable. Check status before retrying the same request.",
        )
    return VisitPrepEmailInvitationResponse(
        preparation_id=str(identifier),
        credential_generation=result.credential_generation,
        state=cast(EmailDeliveryState, result.state),
        disposition=result.disposition,
        requested_at=result.requested_at,
        expires_at=result.expires_at,
    )
