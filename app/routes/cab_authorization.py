"""Separate app-only callback; not part of the browser-generated API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from app.configuration.cab_bridge import CAB_AUTHORIZE_POLICY
from app.routes.dependencies.entra_service import (
    EntraServiceAuthenticator,
    EntraServicePrincipal,
)
from app.services.billing_header_operations import BillingPreparationError
from app.services.cab_bridge_authority import authorize_action, complete_generation
from app.services.cab_bridge_contract import (
    CabAuthorizationRequest,
    CabGenerationCompletion,
)

from activemodel.session_manager import aglobal_session
from sqlalchemy.exc import SQLAlchemyError

router = APIRouter(dependencies=[Depends(aglobal_session)])
authenticate_cab = EntraServiceAuthenticator(CAB_AUTHORIZE_POLICY)


@router.post("/internal/cab/v1/operations/{operation_id}/authorize")
def authorize_cab_operation(
    operation_id: UUID,
    body: CabAuthorizationRequest,
    response: Response,
    principal: Annotated[EntraServicePrincipal, Depends(authenticate_cab)],
):
    del principal  # Verification above establishes the dedicated app role/caller.
    response.headers["Cache-Control"] = "no-store"
    try:
        return authorize_action(operation_id, body)
    except (BillingPreparationError, ValueError, KeyError):
        raise HTTPException(
            403,
            "billing_operation_not_authorized",
            headers={"Cache-Control": "no-store"},
        ) from None
    except SQLAlchemyError:
        raise HTTPException(
            503,
            "billing_authorization_unavailable",
            headers={"Cache-Control": "no-store"},
        ) from None


@router.post("/internal/cab/v1/operations/{operation_id}/complete-generation")
def complete_cab_generation(
    operation_id: UUID,
    body: CabGenerationCompletion,
    response: Response,
    principal: Annotated[EntraServicePrincipal, Depends(authenticate_cab)],
):
    del principal
    response.headers["Cache-Control"] = "no-store"
    try:
        return complete_generation(operation_id, body)
    except (BillingPreparationError, ValueError, KeyError):
        raise HTTPException(
            403,
            "billing_operation_not_authorized",
            headers={"Cache-Control": "no-store"},
        ) from None
    except SQLAlchemyError:
        raise HTTPException(
            503, "cab_action_not_confirmed", headers={"Cache-Control": "no-store"}
        ) from None
