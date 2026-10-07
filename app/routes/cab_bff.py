"""Current-provider CAB facade; browser requests cannot name a source or workload."""

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import model_validator

from app.routes.billing_header import current_billing_actor
from app.services import cab_bridge_authority as authority
from app.services import cab_bridge_client as transport
from app.services.billing_header_contract import Closed, Digest
from app.services.cab_bridge_contract import (
    Purpose,
    canonical,
    digest,
    minimized_operation,
)

from activemodel.session_manager import get_session
from sqlalchemy.exc import SQLAlchemyError

router = APIRouter()
PURPOSE = {
    "accept": "completed-note.accept",
    "read": "header.read",
    "generate": "header.generate",
    "save": "header.save",
    "generation-status": "header.generation-status",
}


class CabActionRequest(Closed):
    action: Literal["accept", "read", "generate", "save", "generation-status"]
    command: dict[str, Any]

    @model_validator(mode="after")
    def shape(self):
        keys = (
            {"expectedSource"}
            if self.action == "accept"
            else {"encounterId", "expectedSource"}
        )
        if self.action in ("generate", "save", "generation-status"):
            keys.add("operationId")
        if self.action == "save":
            keys |= {"headerId", "expectedRevision", "draft", "reason"}
        c = self.command
        if self.action == "generate" and "responseMode" in c:
            keys.add("responseMode")
            if c["responseMode"] != "async":
                raise ValueError("invalid_response_mode")
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
    if (
        body.command.get("responseMode") == "async"
        and not transport.configuration.CAB_BRIDGE_SETTINGS.async_enabled
    ):
        raise HTTPException(
            409, "cab_async_not_configured", headers={"Cache-Control": "no-store"}
        )
    action_id = None
    asynchronous = (
        body.action == "generate" and body.command.get("responseMode") == "async"
    )
    try:
        if asynchronous:
            original, _, replay = authority.begin_async_action(
                actor, operation_id, body.command
            )
            if replay:
                # An existing local intent never resubmits inference, even if acceptance was lost.
                status_command = {
                    k: v for k, v in body.command.items() if k != "responseMode"
                }
                payload, action_id = authority.begin_action(
                    actor, operation_id, "header.generation-status", status_command
                )
            else:
                payload = original
        else:
            payload, action_id = authority.begin_action(
                actor, operation_id, PURPOSE[body.action], body.command
            )

        with get_session() as session:
            row, _, _ = authority._current(session, actor, operation_id)
            operation = minimized_operation(row)
            session.commit()  # No Core row locks during CAB's callback.
        result = transport.CabClient().invoke(payload, operation)
        if asynchronous and replay:
            result = {
                **result,
                "purpose": "header.generate",
                "requestSha256": digest(original),
            }
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
