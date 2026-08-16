from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

import app.services.authority
import app.services.authority_conversation
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


class CanonicalConversationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_type: Literal["text/plain"]
    content: str = Field(min_length=1, max_length=20_000)


ActionCode = Annotated[
    str,
    Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$"),
]


class UserAcceptedTurnWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: Literal["user_accepted"]
    ingress_kind: Literal["chat_text", "provider_ui_action"]
    action_code: ActionCode | None
    message: CanonicalConversationMessage

    @model_validator(mode="after")
    def validate_action_code(self) -> UserAcceptedTurnWrite:
        if self.ingress_kind == "provider_ui_action" and self.action_code is None:
            raise ValueError("Provider UI actions require an action code.")
        if self.ingress_kind == "chat_text" and self.action_code is not None:
            raise ValueError("Chat text cannot carry an action code.")
        return self


SuppressionName = Literal[
    "disclaimer",
    "intent_badge",
    "doctrinal_badge",
    "citations",
    "structured_cards",
    "feature_actions",
    "guided_paths",
]
ALL_SUPPRESSIONS = frozenset(
    {
        "disclaimer",
        "intent_badge",
        "doctrinal_badge",
        "citations",
        "structured_cards",
        "feature_actions",
        "guided_paths",
    }
)


class HumanContactAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=120)
    action_id: str = Field(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._~-]{8,128}$",
    )
    fallback_text: str = Field(min_length=1, max_length=240)


def _unique_suppressions(values: list[SuppressionName]) -> list[SuppressionName]:
    if len(values) != len(set(values)):
        raise ValueError("Suppression values must be unique.")
    return values


class StandardPresentation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["standard"]
    suppress: list[SuppressionName]
    human_contact: HumanContactAction | None

    _validate_suppressions = field_validator("suppress")(_unique_suppressions)


class EmergencyPresentation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["emergency"]
    suppress: list[SuppressionName]
    human_contact: HumanContactAction | None

    _validate_suppressions = field_validator("suppress")(_unique_suppressions)


class SensitiveDisclosurePresentation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["sensitive_disclosure"]
    suppress: list[SuppressionName] = Field(min_length=7, max_length=7)
    human_contact: HumanContactAction

    @field_validator("suppress")
    @classmethod
    def validate_all_suppressions(
        cls,
        values: list[SuppressionName],
    ) -> list[SuppressionName]:
        if set(values) != ALL_SUPPRESSIONS or len(values) != len(set(values)):
            raise ValueError("Sensitive disclosures require every suppression.")
        return values


CanonicalPresentation = Annotated[
    StandardPresentation | EmergencyPresentation | SensitiveDisclosurePresentation,
    Field(discriminator="mode"),
]


class FinalResponseTurnWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: Literal["final_response"]
    outcome: Literal["completed", "refused", "unavailable"]
    response: CanonicalConversationMessage
    presentation: CanonicalPresentation


ConversationTurnWriteRequest = Annotated[
    UserAcceptedTurnWrite | FinalResponseTurnWrite,
    Field(discriminator="phase"),
]
CONVERSATION_TURN_WRITE_ADAPTER = TypeAdapter(ConversationTurnWriteRequest)


class ConversationTurnWriteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_turn_id: UUID
    turn_ref: str
    phase: Literal["user_accepted", "final_response"]
    state: Literal["accepted", "completed"]
    recorded_at: datetime


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


@agent_api_app.post(
    "/conversation-turns/write",
    response_model=ConversationTurnWriteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def write_conversation_turn(
    request: Request,
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
    settings: Annotated[
        AuthorityKernelSettings,
        Depends(get_agent_authority_settings),
    ],
    trace_id: Annotated[
        str | None,
        Header(alias="X-Trace-ID"),
    ] = None,
) -> ConversationTurnWriteResponse | JSONResponse:
    resolved_trace_id = _resolved_trace_id(trace_id)
    content_type = request.headers.get("content-type", "").partition(";")[0].strip()
    if content_type.lower() != "application/json":
        return agent_problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="VALIDATION_FAILED",
            title="Validation failed",
            detail="The conversation phase is invalid.",
            trace_id=resolved_trace_id,
        )
    try:
        raw_data = await request.json()
    except (UnicodeDecodeError, ValueError):
        return agent_problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="VALIDATION_FAILED",
            title="Validation failed",
            detail="The conversation phase is invalid.",
            trace_id=resolved_trace_id,
        )
    if not isinstance(raw_data, dict) or "phase" not in raw_data:
        return agent_problem_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="TURN_PHASE_REQUIRED",
            title="Turn phase required",
            detail="A canonical conversation phase is required.",
            trace_id=resolved_trace_id,
        )
    try:
        data = CONVERSATION_TURN_WRITE_ADAPTER.validate_python(raw_data)
    except ValidationError:
        return agent_problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="VALIDATION_FAILED",
            title="Validation failed",
            detail="The conversation phase is invalid.",
            trace_id=resolved_trace_id,
        )

    try:
        result = app.services.authority_conversation.write_conversation_turn(
            workload_client_id=principal.client_id,
            mount_context_token=mount_context_token,
            interaction_ref=interaction_ref,
            client_turn_id=client_turn_id,
            idempotency_key=idempotency_key,
            payload=data.model_dump(mode="json"),
            settings=settings,
        )
    except app.services.authority.AuthorityKernelDisabledError:
        return agent_problem_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="PLATFORM_UNAVAILABLE",
            title="Platform unavailable",
            detail="Canonical conversation persistence is not available.",
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
    except app.services.authority.InteractionInvalidError:
        return agent_problem_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="INTERACTION_INVALID",
            title="Interaction invalid",
            detail="A valid current interaction is required.",
            trace_id=resolved_trace_id,
        )
    except app.services.authority.ClientTurnConflictError:
        return agent_problem_response(
            status_code=status.HTTP_409_CONFLICT,
            code="CLIENT_TURN_CONFLICT",
            title="Client turn conflict",
            detail="The conversation phase conflicts with the current turn.",
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
    except app.services.authority_conversation.TurnPhaseRequiredError:
        return agent_problem_response(
            status_code=status.HTTP_409_CONFLICT,
            code="TURN_PHASE_REQUIRED",
            title="Turn phase required",
            detail="The required prior conversation phase is not durable.",
            trace_id=resolved_trace_id,
        )
    except app.services.authority_conversation.IdempotencyConflictError:
        return agent_problem_response(
            status_code=status.HTTP_409_CONFLICT,
            code="IDEMPOTENCY_CONFLICT",
            title="Idempotency conflict",
            detail="The idempotency key is bound to another request.",
            trace_id=resolved_trace_id,
        )
    except app.services.authority_conversation.ConversationInvariantError:
        return agent_problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="VALIDATION_FAILED",
            title="Validation failed",
            detail="The conversation phase is invalid.",
            trace_id=resolved_trace_id,
        )
    except (
        app.services.authority_conversation.ConversationStateError,
        SQLAlchemyError,
    ):
        return agent_problem_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="PLATFORM_UNAVAILABLE",
            title="Platform unavailable",
            detail="Canonical conversation persistence is temporarily unavailable.",
            retryable=True,
            trace_id=resolved_trace_id,
        )

    response.headers["X-Trace-ID"] = resolved_trace_id
    response.headers["X-Cosmaneura-Client-Turn-ID"] = str(client_turn_id)
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    response.headers["Cache-Control"] = "no-store"
    return ConversationTurnWriteResponse(
        client_turn_id=result.client_turn_id,
        turn_ref=result.turn_ref,
        phase=result.phase,
        state=result.state,
        recorded_at=result.recorded_at,
    )
