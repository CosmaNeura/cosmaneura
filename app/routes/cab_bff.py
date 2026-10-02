"""Current-provider CAB facade; browser requests cannot name a source or workload."""

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import model_validator

from app.routes.billing_header import current_billing_actor
from app.services import cab_bridge_authority as authority
from app.services import cab_bridge_client as transport
from app.services.billing_header_contract import Closed, Digest
from app.services.cab_bridge_contract import Purpose, canonical, minimized_operation

from activemodel.session_manager import get_session
from sqlalchemy.exc import SQLAlchemyError

router = APIRouter()
PURPOSE = {
    "accept": "completed-note.accept",
    "read": "header.read",
    "generate": "header.generate",
    "save": "header.save",
}


class CabActionRequest(Closed):
    action: Literal["accept", "read", "generate", "save"]
    command: dict[str, Any]

    @model_validator(mode="after")
    def shape(self):
        keys = (
            {"expectedSource"}
            if self.action == "accept"
            else {"encounterId", "expectedSource"}
        )
        if self.action in ("generate", "save"):
            keys.add("operationId")
        if self.action == "save":
            keys |= {"headerId", "expectedRevision", "draft", "reason"}
        c = self.command
        if set(c) != keys or len(canonical(c).encode()) > 1_400_000:
            raise ValueError("invalid_cab_command")
        if type(c["expectedSource"]) is not dict and not (
            self.action == "accept" and c["expectedSource"] is None
        ):
            raise ValueError("invalid_source_pin")
        if self.action != "accept":
            from pydantic import TypeAdapter

            from app.services.billing_header_contract import Ref

            TypeAdapter(Ref).validate_python(c["encounterId"], strict=True)
        for key in ("operationId", "headerId"):
            if key in c and (type(c[key]) is not str or str(UUID(c[key])) != c[key]):
                raise ValueError("invalid_operation_id")
        if self.action == "save" and (
            type(c["expectedRevision"]) is not int
            or not 0 <= c["expectedRevision"] <= 100
            or type(c["draft"]) is not dict
            or type(c["reason"]) is not str
            or not 1 <= len(c["reason"]) <= 600
            or not c["reason"].strip()
        ):
            raise ValueError("invalid_header_edit")
        return self


class CabActionResult(Closed):
    schemaVersion: Literal["core-cab-result.v1"]
    purpose: Purpose
    sourceOperationId: str
    requestSha256: Digest
    result: dict[str, Any]
    source: dict[str, Any] | None


@router.post(
    "/billing-header-operations/{operation_id}/cab", response_model=CabActionResult
)
def billing_header_cab(
    request: Request, response: Response, operation_id: UUID, body: CabActionRequest
) -> CabActionResult:
    actor = current_billing_actor(request)
    response.headers["Cache-Control"] = "no-store"
    if not transport.configured():
        raise HTTPException(
            503, "cab_not_configured", headers={"Cache-Control": "no-store"}
        )
    action_id = None
    try:
        payload, action_id = authority.begin_action(
            actor, operation_id, PURPOSE[body.action], body.command
        )
        with get_session() as session:
            row, _, _ = authority._current(session, actor, operation_id)
            operation = minimized_operation(row)
            session.commit()  # No Core row locks during CAB's callback.
        result = transport.CabClient().invoke(payload, operation)
        with get_session() as session:
            authority._current(
                session, actor, operation_id
            )  # Do not display data after local scope loss.
        return CabActionResult.model_validate(result)
    except transport.CabUnavailable as exc:
        raise HTTPException(
            exc.status, str(exc), headers={"Cache-Control": "no-store"}
        ) from None
    except (authority.preparation.BillingPreparationError, ValueError, KeyError):
        raise HTTPException(
            409, "billing_source_changed", headers={"Cache-Control": "no-store"}
        ) from None
    except SQLAlchemyError:
        raise HTTPException(
            503, "cab_action_not_confirmed", headers={"Cache-Control": "no-store"}
        ) from None
    finally:
        if action_id is not None:
            try:
                with get_session() as session:
                    session.rollback()
                authority.finish_action(action_id)
            except SQLAlchemyError:
                raise HTTPException(
                    503,
                    "cab_action_not_confirmed",
                    headers={"Cache-Control": "no-store"},
                ) from None
