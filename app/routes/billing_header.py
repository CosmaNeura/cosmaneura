"""Core-owned billing preparation; included only under the protected internal router."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Request, Response

from app.configuration import billing_header as configuration
from app.configuration.clerk import verified_provider_session
from app.routes.dependencies.view_as import deny_view_as_browser_activity
from app.services import billing_header_operations as operations
from app.services.billing_header_contract import (
    BillingHeaderOperation,
    BillingHeaderSource,
    PrepareBillingHeaderOperation,
)

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from sqlalchemy.exc import SQLAlchemyError

billing_header_api = APIRouter()


def current_billing_actor(request):
    deny_view_as_browser_activity(request)
    if getattr(request.state, "view_as", None) is not None:
        raise HTTPException(403, "billing_view_as_denied")
    try:
        actor = verified_provider_session(
            request.state.auth_state,
            expected_issuer=configuration.BILLING_SOURCE_POLICY.clerkIssuer
            if configuration.BILLING_SOURCE_POLICY.enabled
            else "",
        )
        if request.state.doctor.clerk_id != actor.subject:
            raise ValueError
        return actor
    except (ValueError, AttributeError):
        raise HTTPException(401, "billing_session_required") from None


def _call(response, callback):
    response.headers["Cache-Control"] = "no-store"
    try:
        return callback()
    except operations.BillingPreparationError as exc:
        raise HTTPException(
            exc.status, exc.code, headers={"Cache-Control": "no-store"}
        ) from None
    except SQLAlchemyError:
        raise HTTPException(
            503,
            "billing_preparation_unavailable",
            headers={"Cache-Control": "no-store"},
        ) from None


@billing_header_api.get(
    "/notes/revisions/{note_id}/billing-header-source",
    response_model=BillingHeaderSource,
)
def billing_header_source(
    request: Request, response: Response, note_id: Annotated[TypeIDType, Path()]
) -> BillingHeaderSource:
    actor = current_billing_actor(request)
    return _call(response, lambda: operations.read_source(actor, note_id))


@billing_header_api.post(
    "/notes/revisions/{note_id}/billing-header-operations",
    response_model=BillingHeaderOperation,
)
def prepare_billing_header_operation(
    request: Request,
    response: Response,
    note_id: Annotated[TypeIDType, Path()],
    body: PrepareBillingHeaderOperation,
) -> BillingHeaderOperation:
    actor = current_billing_actor(request)
    return _call(response, lambda: operations.prepare_operation(actor, note_id, body))


@billing_header_api.get(
    "/billing-header-operations/{operation_id}", response_model=BillingHeaderOperation
)
def billing_header_operation(
    request: Request, response: Response, operation_id: UUID
) -> BillingHeaderOperation:
    actor = current_billing_actor(request)
    return _call(response, lambda: operations.read_operation(actor, operation_id))


def note_billing_sources(request, notes):
    if (
        not configuration.BILLING_SOURCE_POLICY.enabled
        or getattr(request.state, "view_as", None) is not None
    ):
        return {}
    try:
        actor = current_billing_actor(request)
        with get_session() as session, session.begin_nested():
            return operations.admitted_source_map(actor, notes)
    except (HTTPException, operations.BillingPreparationError, SQLAlchemyError):
        return {}
