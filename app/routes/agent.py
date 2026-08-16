from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

import app.services.authority
from app.configuration.authority import (
    AUTHORITY_KERNEL_SETTINGS,
    AuthorityKernelSettings,
)

from activemodel.session_manager import aglobal_session
from sqlalchemy.exc import SQLAlchemyError

from .authority_errors import agent_problem_response, new_trace_id
from .dependencies.entra_service import (
    EntraServicePrincipal,
    authenticate_agent_api_request,
)

agent_api_app = APIRouter(
    prefix="/service/v1/agent",
    tags=["agent"],
    dependencies=[Depends(aglobal_session)],
)


class MountContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surface: Literal["public", "provider_assistant"]
    auth_tier: Literal["anonymous", "prospect", "identified", "enrolled", "provider"]
    capabilities: list[str]
    tenant_id: str | None
    actor_id: str | None


def get_agent_authority_settings() -> AuthorityKernelSettings:
    return AUTHORITY_KERNEL_SETTINGS


def _resolved_trace_id(trace_id: str | None) -> str:
    if trace_id is None or not 16 <= len(trace_id) <= 128:
        return new_trace_id()
    if not all(character.isalnum() or character in "._~-" for character in trace_id):
        return new_trace_id()
    return trace_id


@agent_api_app.post(
    "/mount-context/resolve",
    response_model=MountContextResponse,
    status_code=status.HTTP_200_OK,
)
def resolve_mount_context(
    response: Response,
    principal: Annotated[
        EntraServicePrincipal,
        Depends(authenticate_agent_api_request),
    ],
    mount_context_token: Annotated[
        str,
        Header(
            alias="X-Cosmaneura-Mount-Context",
            min_length=16,
            max_length=8192,
        ),
    ],
    client_turn_id: Annotated[
        UUID,
        Header(alias="X-Cosmaneura-Client-Turn-ID"),
    ],
    settings: Annotated[
        AuthorityKernelSettings,
        Depends(get_agent_authority_settings),
    ],
    trace_id: Annotated[
        str | None,
        Header(alias="X-Trace-ID"),
    ] = None,
) -> MountContextResponse | JSONResponse:
    resolved_trace_id = _resolved_trace_id(trace_id)
    try:
        result = app.services.authority.resolve_mount_context(
            workload_client_id=principal.client_id,
            mount_context_token=mount_context_token,
            client_turn_id=client_turn_id,
            settings=settings,
        )
    except app.services.authority.AuthorityKernelDisabledError:
        return agent_problem_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="PLATFORM_UNAVAILABLE",
            title="Platform unavailable",
            detail="Mount-context resolution is not available.",
            trace_id=resolved_trace_id,
        )
    except app.services.authority.ContextTokenExpiredError:
        return agent_problem_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="CONTEXT_EXPIRED",
            title="Context expired",
            detail="The mount context has expired.",
            trace_id=resolved_trace_id,
        )
    except app.services.authority.ContextTokenInvalidError:
        return agent_problem_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="CONTEXT_INVALID",
            title="Context invalid",
            detail="A valid mount context is required.",
            trace_id=resolved_trace_id,
        )
    except app.services.authority.AuthorityRequestDeniedError:
        return agent_problem_response(
            status_code=status.HTTP_403_FORBIDDEN,
            code="SCOPE_DENIED",
            title="Scope denied",
            detail="The current mount context is not authorized.",
            trace_id=resolved_trace_id,
        )
    except app.services.authority.ClientTurnConflictError:
        return agent_problem_response(
            status_code=status.HTTP_409_CONFLICT,
            code="CLIENT_TURN_CONFLICT",
            title="Client turn conflict",
            detail="The client turn identifier is already bound to another request.",
            trace_id=resolved_trace_id,
        )
    except SQLAlchemyError:
        return agent_problem_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="PLATFORM_UNAVAILABLE",
            title="Platform unavailable",
            detail="Mount-context resolution is temporarily unavailable.",
            retryable=True,
            trace_id=resolved_trace_id,
        )

    response.headers["X-Trace-ID"] = resolved_trace_id
    response.headers["X-Cosmaneura-Interaction-Ref"] = result.interaction_ref
    response.headers["X-Cosmaneura-Client-Turn-ID"] = str(client_turn_id)
    response.headers["X-Cosmaneura-Interaction-Reused"] = str(result.reused).lower()
    response.headers["Cache-Control"] = "no-store"
    return MountContextResponse.model_validate(result.context.public_body())
