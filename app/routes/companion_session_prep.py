"""CosmaBot's dedicated current-encounter brief receiver."""

from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.configuration.authority import AuthorityKernelSettings
from app.configuration.session_prep import SessionPrepSyntheticSettings
from app.routes.agent import (
    OpaqueRef,
    _invoke_tool,
    get_agent_authority_settings,
    get_session_prep_synthetic_settings,
)
from app.routes.dependencies.entra_service import (
    EntraServicePrincipal,
    authenticate_companion_session_prep_request,
)
from app.services.session_prep import SessionPrepToolResponse

from activemodel.session_manager import aglobal_session

PATH = "/service/v1/companion/session-prep/brief"
router = APIRouter(tags=["companion"], dependencies=[Depends(aglobal_session)])


class CompanionBriefArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_turn_ref: OpaqueRef
    context_snapshot_ref: OpaqueRef
    purpose: Literal["session_prep"]
    detail: Literal["brief"]


class CompanionBriefInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: Literal["session-prep.context.read"]
    tool_version: Literal["1.0.0"]
    subject_ref: None
    arguments: CompanionBriefArguments


@router.post(
    PATH,
    response_model=SessionPrepToolResponse,
    status_code=status.HTTP_200_OK,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": CompanionBriefInvocation.model_json_schema()
                }
            },
        }
    },
)
async def read_companion_session_prep_brief(
    request: Request,
    response: Response,
    principal: Annotated[
        EntraServicePrincipal,
        Depends(authenticate_companion_session_prep_request),
    ],
    mount_context_token: Annotated[
        str,
        Header(
            alias="X-Cosmaneura-Mount-Context",
            min_length=16,
            max_length=8192,
        ),
    ],
    interaction_ref: Annotated[
        str,
        Header(
            alias="X-Cosmaneura-Interaction-Ref",
            min_length=16,
            max_length=256,
            pattern=r"^[A-Za-z0-9._~-]{16,256}$",
        ),
    ],
    client_turn_id: Annotated[
        UUID,
        Header(alias="X-Cosmaneura-Client-Turn-ID"),
    ],
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=16,
            max_length=128,
            pattern=r"^[A-Za-z0-9._~-]{16,128}$",
        ),
    ],
    authority_settings: Annotated[
        AuthorityKernelSettings,
        Depends(get_agent_authority_settings),
    ],
    session_prep_settings: Annotated[
        SessionPrepSyntheticSettings,
        Depends(get_session_prep_synthetic_settings),
    ],
    trace_id: Annotated[
        str | None,
        Header(alias="X-Trace-ID"),
    ] = None,
) -> SessionPrepToolResponse | JSONResponse:
    result = await _invoke_tool(
        request=request,
        response=response,
        principal=principal,
        mount_context_token=mount_context_token,
        interaction_ref=interaction_ref,
        client_turn_id=client_turn_id,
        idempotency_key=idempotency_key,
        authority_settings=authority_settings,
        session_prep_settings=session_prep_settings,
        trace_id=trace_id,
        companion_brief=True,
    )
    return cast(SessionPrepToolResponse | JSONResponse, result)
