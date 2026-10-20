from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal, cast
from uuid import UUID

from anyio import CancelScope
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
from starlette.concurrency import run_in_threadpool

import app.configuration.session_prep as session_prep_configuration
import app.services.authority
import app.services.authority_audit
import app.services.authority_conversation
import app.services.authority_conversation_read as history
import app.services.authority_usage
import app.services.provider_question_context as provider_question
import app.services.session_prep
from app import log
from app.configuration.authority import (
    AUTHORITY_KERNEL_SETTINGS,
    AuthorityKernelSettings,
)
from app.configuration.session_prep import (
    SESSION_PREP_SYNTHETIC_SETTINGS,
    SessionPrepSyntheticSettings,
)

from activemodel.session_manager import aglobal_session, get_session
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from .authority_errors import agent_problem_response, normalize_trace_id
from .dependencies.entra_service import (
    EntraServicePrincipal,
    authenticate_agent_api_request,
    authenticate_sovereign_tool_request,
)
from .scribe_capture import capture_api_app

agent_api_app = APIRouter(
    prefix="/service/v1/agent",
    tags=["agent"],
    dependencies=[Depends(aglobal_session)],
)
agent_api_app.include_router(capture_api_app)


async def _run_agent_database[T, **P](
    function: Callable[P, T], *args: P.args, **kwargs: P.kwargs
) -> T:
    # Retain the worker task: cancelling the route must not let aglobal_session
    # close its ContextVar session while synchronous database work still uses it.
    worker = asyncio.create_task(run_in_threadpool(function, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # AnyIO shields level cancellation; asyncio.shield keeps direct (even
        # repeated) Task.cancel() calls from cancelling the retained worker.
        with CancelScope(shield=True):
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not worker.cancelled():
                worker.exception()  # Retrieve failure; preserve original cancellation.
        raise


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
    history_context_ref: app.services.session_prep.OpaqueRef | None = None

    model_config = ConfigDict(extra="forbid")

    phase: Literal["final_response"]
    outcome: Literal["completed", "refused", "unavailable"]
    response: CanonicalConversationMessage
    presentation: CanonicalPresentation
    provider_context_ref: str | None = Field(
        default=None, min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._~-]{8,160}$"
    )


ConversationTurnWriteRequest = Annotated[
    UserAcceptedTurnWrite | FinalResponseTurnWrite,
    Field(discriminator="phase"),
]
CONVERSATION_TURN_WRITE_ADAPTER = TypeAdapter(ConversationTurnWriteRequest)


class ConversationTurnWriteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_turn_id: UUID
    turn_ref: str = Field(
        min_length=8,
        max_length=160,
        pattern=r"^[A-Za-z0-9._~-]{8,160}$",
    )
    phase: Literal["user_accepted", "final_response"]
    state: Literal["accepted", "completed"]
    recorded_at: datetime


PlatformReferenceKind = Literal[
    "person",
    "conversation_turn",
    "user_request",
    "source_evidence",
    "session_snapshot",
    "journey_digest",
    "consent_offer",
    "consent_event",
    "trigger",
    "tool_call",
    "access_intent",
    "access_outcome",
    "approval",
    "model_usage",
    "other",
]


class PlatformReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: PlatformReferenceKind
    ref: str = Field(
        min_length=8,
        max_length=160,
        pattern=r"^[A-Za-z0-9._~-]{8,160}$",
    )


class AuditWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_event_id: UUID
    observed_at: datetime
    event_type: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$")
    observation_status: Literal["completed", "refused", "failed", "cancelled"]
    route: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9._-]{1,127}$",
    )
    intent: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9._-]{1,127}$",
    )
    content_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    platform_refs: list[PlatformReference] = Field(max_length=50)

    @field_validator("platform_refs")
    @classmethod
    def references_are_unique(
        cls,
        references: list[PlatformReference],
    ) -> list[PlatformReference]:
        keys = {(reference.kind, reference.ref) for reference in references}
        if len(keys) != len(references):
            raise ValueError("Platform references must be unique.")
        return references


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int | None = Field(ge=0)
    output_tokens: int | None = Field(ge=0)
    cached_input_tokens: int | None = Field(ge=0)
    reasoning_tokens: int | None = Field(ge=0)


class ModelUsageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_event_id: UUID
    call_kind: Literal[
        "classification",
        "routing",
        "reasoning",
        "drafting",
        "doctrinal_arbitration",
        "tool_support",
        "embedding",
        "other",
    ]
    call_sequence: int = Field(ge=1)
    workstream: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$")
    provider: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$")
    provider_request_id: str | None = Field(max_length=200)
    model: str | None = Field(min_length=1, max_length=200)
    deployment: str | None = Field(max_length=200)
    purpose: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$")
    started_at: datetime | None
    completed_at: datetime | None
    latency_ms: int | None = Field(ge=0)
    status: Literal["succeeded", "failed", "cancelled"]
    usage_availability: Literal["complete", "partial", "unavailable"]
    usage_source: Literal[
        "provider_response",
        "provider_headers",
        "platform_meter",
        "not_available",
    ]
    token_usage: TokenUsage

    @field_validator("started_at", "completed_at")
    @classmethod
    def timestamps_require_timezone(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("A timezone is required.")
        return value

    @model_validator(mode="after")
    def completion_does_not_precede_start(self) -> ModelUsageRequest:
        if (
            self.started_at is not None
            and self.completed_at is not None
            and self.completed_at < self.started_at
        ):
            raise ValueError("Completion cannot precede start.")
        return self


class WriteReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_ref: str = Field(
        min_length=8,
        max_length=160,
        pattern=r"^[A-Za-z0-9._~-]{8,160}$",
    )
    recorded_at: datetime


OpaqueRef = Annotated[
    str,
    Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._~-]{8,160}$"),
]


class ToolInvocationEnvelope(BaseModel):
    """Closed generic envelope used only to select a ratified tool schema."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$")
    tool_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?$")
    subject_ref: OpaqueRef | None
    ehr_read_access: dict[str, object] | None = None
    arguments: dict[str, object]


class SessionPrepReadArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_turn_ref: OpaqueRef
    context_snapshot_ref: OpaqueRef
    purpose: Literal["session_prep"]
    detail: Literal["brief", "supporting_context"]


class SessionPrepContextReadInvocation(BaseModel):
    """Frozen request schema for the reserved Session Prep read operation."""

    model_config = ConfigDict(extra="forbid")

    tool_name: Literal["session-prep.context.read"]
    tool_version: Literal["1.0.0"]
    subject_ref: None
    arguments: SessionPrepReadArguments


_SESSION_PREP_TOOL_KEY = ("session-prep.context.read", "1.0.0")
_MAX_TOOL_INVOCATION_BODY_BYTES = 8 * 1024


class _DuplicateJSONKey(ValueError):
    pass


def _closed_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKey
        result[key] = value
    return result


async def _validated_tool_body(
    request: Request,
    *,
    trace_id: str,
) -> ToolInvocationEnvelope | JSONResponse:
    """Read one bounded JSON object after workload authentication."""

    content_types = request.headers.getlist("content-type")
    content_type = (
        content_types[0].split(";", 1)[0].strip().lower()
        if len(content_types) == 1
        else ""
    )
    if (
        content_type != "application/json"
        or request.query_params
        or request.headers.get("content-encoding", "identity").lower()
        not in {"", "identity"}
    ):
        return agent_problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="VALIDATION_FAILED",
            title="Validation failed",
            detail="The tool invocation is invalid.",
            trace_id=trace_id,
        )

    declared_lengths = request.headers.getlist("content-length")
    if len(declared_lengths) > 1:
        return agent_problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="VALIDATION_FAILED",
            title="Validation failed",
            detail="The tool invocation is invalid.",
            trace_id=trace_id,
        )
    if declared_lengths:
        try:
            declared_length = int(declared_lengths[0])
        except ValueError:
            declared_length = -1
        if not 0 <= declared_length <= _MAX_TOOL_INVOCATION_BODY_BYTES:
            return agent_problem_response(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="VALIDATION_FAILED",
                title="Validation failed",
                detail="The tool invocation is invalid.",
                trace_id=trace_id,
            )

    body = bytearray()
    async for chunk in request.stream():
        if len(chunk) > _MAX_TOOL_INVOCATION_BODY_BYTES - len(body):
            return agent_problem_response(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                code="VALIDATION_FAILED",
                title="Validation failed",
                detail="The tool invocation is invalid.",
                trace_id=trace_id,
            )
        body.extend(chunk)
    try:
        raw = json.loads(bytes(body), object_pairs_hook=_closed_json_object)
        return ToolInvocationEnvelope.model_validate(raw)
    except (TypeError, ValueError, UnicodeDecodeError, RecursionError):
        return agent_problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="VALIDATION_FAILED",
            title="Validation failed",
            detail="The tool invocation is invalid.",
            trace_id=trace_id,
        )


async def _validated_audit_body(
    request: Request,
    *,
    trace_id: str,
) -> AuditWriteRequest | JSONResponse:
    """Parse only after workload auth and never echo rejected request values."""

    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
    if content_type.lower() != "application/json":
        return agent_problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="VALIDATION_FAILED",
            title="Validation failed",
            detail="The observational audit event is invalid.",
            trace_id=trace_id,
        )
    try:
        return AuditWriteRequest.model_validate(await request.json())
    except (TypeError, ValueError):
        return agent_problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="VALIDATION_FAILED",
            title="Validation failed",
            detail="The observational audit event is invalid.",
            trace_id=trace_id,
        )


async def _validated_usage_body(
    request: Request,
    *,
    trace_id: str,
) -> ModelUsageRequest | JSONResponse:
    """Parse only after workload auth and never echo rejected request values."""

    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
    if content_type.lower() != "application/json":
        return agent_problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="VALIDATION_FAILED",
            title="Validation failed",
            detail="The model usage event is invalid.",
            trace_id=trace_id,
        )
    try:
        return ModelUsageRequest.model_validate(await request.json())
    except (TypeError, ValueError):
        return agent_problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="VALIDATION_FAILED",
            title="Validation failed",
            detail="The model usage event is invalid.",
            trace_id=trace_id,
        )


def get_agent_authority_settings() -> AuthorityKernelSettings:
    return AUTHORITY_KERNEL_SETTINGS


def get_session_prep_synthetic_settings() -> SessionPrepSyntheticSettings:
    return SESSION_PREP_SYNTHETIC_SETTINGS


@agent_api_app.post(
    "/tools/invoke",
    response_model=app.services.session_prep.SessionPrepToolResponse
    | provider_question.ProviderQuestionResponse
    | history.HistoryResponse,
    status_code=status.HTTP_200_OK,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": TypeAdapter(
                        SessionPrepContextReadInvocation
                        | provider_question.ProviderQuestionInvocation
                        | history.HistoryInvocation
                    ).json_schema(),
                }
            },
        }
    },
)
async def invoke_tool(
    request: Request,
    response: Response,
    principal: Annotated[
        EntraServicePrincipal,
        Depends(authenticate_sovereign_tool_request),
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
) -> (
    app.services.session_prep.SessionPrepToolResponse
    | provider_question.ProviderQuestionResponse
    | history.HistoryResponse
    | JSONResponse
):
    """Read a current provider brief or the isolated synthetic compatibility path."""

    return await _invoke_tool(
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
    )


async def _invoke_tool(
    *,
    request: Request,
    response: Response,
    principal: EntraServicePrincipal,
    mount_context_token: str,
    interaction_ref: str,
    client_turn_id: UUID,
    idempotency_key: str,
    authority_settings: AuthorityKernelSettings,
    session_prep_settings: SessionPrepSyntheticSettings,
    trace_id: str | None,
    companion_brief: bool = False,
) -> (
    app.services.session_prep.SessionPrepToolResponse
    | provider_question.ProviderQuestionResponse
    | history.HistoryResponse
    | JSONResponse
):
    """Execute shared receipt semantics after the route's own workload gate.

    The companion constraint is trusted route configuration, never request data.
    Apply it before any authority/data access and never enter the demo fallback.
    """

    resolved_trace_id = normalize_trace_id(trace_id)
    singleton_headers = (
        "x-cosmaneura-mount-context",
        "x-cosmaneura-interaction-ref",
        "x-cosmaneura-client-turn-id",
        "idempotency-key",
    )
    if (
        any(len(request.headers.getlist(name)) != 1 for name in singleton_headers)
        or len(request.headers.getlist("x-trace-id")) > 1
    ):
        return agent_problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="VALIDATION_FAILED",
            title="Validation failed",
            detail="The tool invocation is invalid.",
            trace_id=resolved_trace_id,
        )

    data = await _validated_tool_body(request, trace_id=resolved_trace_id)
    if isinstance(data, JSONResponse):
        return data
    tool_key = (data.tool_name, data.tool_version)
    if (companion_brief and tool_key != _SESSION_PREP_TOOL_KEY) or tool_key not in {
        _SESSION_PREP_TOOL_KEY,
        provider_question.TOOL_KEY,
        history.TOOL_KEY,
    }:
        return agent_problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code="RESOURCE_NOT_FOUND",
            title="Resource not found",
            detail="The requested tool is not available.",
            trace_id=resolved_trace_id,
        )
    try:
        invocation = (
            history.HistoryInvocation.model_validate(
                data.model_dump(mode="json", exclude_unset=True)
            )
            if tool_key == history.TOOL_KEY
            else provider_question.ProviderQuestionInvocation.model_validate(
                data.model_dump(mode="json", exclude_unset=True)
            )
            if tool_key == provider_question.TOOL_KEY
            else SessionPrepContextReadInvocation.model_validate(
                data.model_dump(mode="json", exclude_unset=True)
            )
        )
        if companion_brief and (
            not isinstance(invocation, SessionPrepContextReadInvocation)
            or invocation.arguments.detail != "brief"
        ):
            raise ValueError("companion_requires_brief")
    except (ValidationError, ValueError, RecursionError):
        return agent_problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="VALIDATION_FAILED",
            title="Validation failed",
            detail="The tool invocation is invalid.",
            trace_id=resolved_trace_id,
        )

    if (
        companion_brief or not session_prep_settings.is_complete()
    ) and not session_prep_configuration.SESSION_PREP_PROVIDER_SETTINGS.enabled:
        return agent_problem_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="PLATFORM_UNAVAILABLE",
            title="Platform unavailable",
            detail="The requested platform tool is not available.",
            trace_id=resolved_trace_id,
        )

    def invoke_in_session() -> (
        app.services.session_prep.SessionPrepInvocation
        | provider_question.ProviderQuestionRead
        | history.HistoryRead
    ):
        # Keep authorization, row locks, and commit in one sequential worker
        # call. The threadpool carries aglobal_session's ContextVar, and the
        # await boundary drains this worker before request session teardown.
        with get_session() as managed:
            session = cast(Session, managed)
            if isinstance(invocation, history.HistoryInvocation):
                return history.read_history_context(
                    session=session,
                    workload_client_id=principal.client_id,
                    mount_context_token=mount_context_token,
                    interaction_ref=interaction_ref,
                    client_turn_id=client_turn_id,
                    idempotency_key=idempotency_key,
                    invocation=invocation,
                    settings=authority_settings,
                )
            if isinstance(invocation, provider_question.ProviderQuestionInvocation):
                return provider_question.read_provider_question_context(
                    session=session,
                    workload_client_id=principal.client_id,
                    mount_context_token=mount_context_token,
                    interaction_ref=interaction_ref,
                    client_turn_id=client_turn_id,
                    idempotency_key=idempotency_key,
                    invocation=invocation,
                    settings=authority_settings,
                )
            authorized = app.services.session_prep.authorize_session_prep_read(
                workload_client_id=principal.client_id,
                mount_context_token=mount_context_token,
                interaction_ref=interaction_ref,
                client_turn_id=client_turn_id,
                source_turn_ref=invocation.arguments.source_turn_ref,
                settings=authority_settings,
                session=session,
            )
            if (
                session_prep_configuration.SESSION_PREP_PROVIDER_SETTINGS.enabled
                and authorized.authorized.context.session_prep_context_ref is not None
            ):
                from app.services.session_prep_runtime import (
                    invoke_provider_session_prep,
                )

                result = invoke_provider_session_prep(
                    session=session,
                    authorization=authorized,
                    workload_client_id=principal.client_id,
                    idempotency_key=idempotency_key,
                    source_turn_ref=invocation.arguments.source_turn_ref,
                    context_snapshot_ref=invocation.arguments.context_snapshot_ref,
                    detail=invocation.arguments.detail,
                    observed_at=datetime.now(UTC),
                    settings=authority_settings,
                )
                session.commit()
            elif not companion_brief and session_prep_settings.is_complete():
                result = app.services.session_prep.SYNTHETIC_SESSION_PREP_SERVICE.invoke(
                    workload_client_id=principal.client_id,
                    interaction_ref=interaction_ref,
                    client_turn_id=client_turn_id,
                    idempotency_key=idempotency_key,
                    source_turn_ref=invocation.arguments.source_turn_ref,
                    context_snapshot_ref=invocation.arguments.context_snapshot_ref,
                    detail=invocation.arguments.detail,
                    allowed_context_snapshot_ref=session_prep_settings.context_snapshot_ref,
                    turn_closed=authorized.turn_closed,
                )
            else:
                raise app.services.session_prep.SessionPrepScopeDenied
        return result

    try:
        result = await _run_agent_database(invoke_in_session)
    except history.ConversationContextChanged:
        return agent_problem_response(
            status_code=409,
            code="CONVERSATION_CONTEXT_CHANGED",
            title="Conversation context changed",
            detail="The recent conversation no longer authorizes this response.",
            trace_id=resolved_trace_id,
        )
    except app.services.authority.AuthorityKernelDisabledError:
        return agent_problem_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="PLATFORM_UNAVAILABLE",
            title="Platform unavailable",
            detail="The requested platform tool is not available.",
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
            detail="The tool request conflicts with the current turn.",
            trace_id=resolved_trace_id,
        )
    except (
        app.services.authority.AuthorityRequestDeniedError,
        app.services.session_prep.SessionPrepScopeDenied,
    ):
        return agent_problem_response(
            status_code=status.HTTP_403_FORBIDDEN,
            code="SCOPE_DENIED",
            title="Scope denied",
            detail="The current mount context is not authorized.",
            trace_id=resolved_trace_id,
        )
    except app.services.session_prep.SessionPrepIdempotencyConflict:
        return agent_problem_response(
            status_code=status.HTTP_409_CONFLICT,
            code="IDEMPOTENCY_CONFLICT",
            title="Idempotency conflict",
            detail="The idempotency key is bound to another request.",
            trace_id=resolved_trace_id,
        )
    except (app.services.session_prep.SessionPrepInvariantError, SQLAlchemyError):
        return agent_problem_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="PLATFORM_UNAVAILABLE",
            title="Platform unavailable",
            detail="The requested platform tool is temporarily unavailable.",
            retryable=True,
            trace_id=resolved_trace_id,
        )

    response.headers["X-Trace-ID"] = resolved_trace_id
    response.headers["X-Cosmaneura-Client-Turn-ID"] = str(client_turn_id)
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    response.headers["Cache-Control"] = "no-store"
    return result.response


@agent_api_app.post(
    "/mount-context/bootstrap",
    response_model=MountContextResponse,
    status_code=status.HTTP_200_OK,
)
async def bootstrap_mount_context(
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
    settings: Annotated[
        AuthorityKernelSettings,
        Depends(get_agent_authority_settings),
    ],
    trace_id: Annotated[
        str | None,
        Header(alias="X-Trace-ID"),
    ] = None,
) -> MountContextResponse | JSONResponse:
    """Resolve pre-turn UI context without creating durable lineage."""

    # Authentication dependencies resolve before the endpoint reads the body.
    # Keep this operation bodyless so it cannot become a hidden action channel.
    submitted_trace_ids = request.headers.getlist("x-trace-id")
    resolved_trace_id = normalize_trace_id(
        trace_id if len(submitted_trace_ids) == 1 else None
    )
    forbidden_turn_headers = (
        "X-Cosmaneura-Client-Turn-ID",
        "X-Cosmaneura-Interaction-Ref",
        "Idempotency-Key",
    )
    if await request.body() or any(
        header in request.headers for header in forbidden_turn_headers
    ):
        return agent_problem_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="VALIDATION_FAILED",
            title="Validation failed",
            detail="The mount-context bootstrap request is invalid.",
            trace_id=resolved_trace_id,
        )

    try:
        context = await _run_agent_database(
            app.services.authority.bootstrap_mount_context,
            mount_context_token=mount_context_token,
            settings=settings,
        )
        response_body = MountContextResponse.model_validate(context.public_body())
    except app.services.authority.AuthorityKernelDisabledError:
        return agent_problem_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="PLATFORM_UNAVAILABLE",
            title="Platform unavailable",
            detail="Mount-context bootstrap is not available.",
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
    except SQLAlchemyError:
        return agent_problem_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="PLATFORM_UNAVAILABLE",
            title="Platform unavailable",
            detail="Mount-context bootstrap is temporarily unavailable.",
            retryable=True,
            trace_id=resolved_trace_id,
        )
    except Exception as error:
        log.error(
            "agent mount bootstrap failed",
            action="agent_api.mount_bootstrap_failed",
            error_class=type(error).__name__,
        )
        return agent_problem_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="INTERNAL_ERROR",
            title="Internal error",
            detail="The platform agent API request could not be completed.",
            trace_id=resolved_trace_id,
        )

    response.headers["X-Trace-ID"] = resolved_trace_id
    response.headers["Cache-Control"] = "no-store"
    return response_body


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
    resolved_trace_id = normalize_trace_id(trace_id)
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
    if result.context.session_prep_context_ref is not None:
        response.headers["X-Cosmaneura-Session-Prep-Context"] = (
            result.context.session_prep_context_ref
        )
    response.headers["Cache-Control"] = "no-store"
    return MountContextResponse.model_validate(result.context.public_body())


@agent_api_app.post(
    "/conversation-turns/write",
    response_model=ConversationTurnWriteResponse,
    status_code=status.HTTP_201_CREATED,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": CONVERSATION_TURN_WRITE_ADAPTER.json_schema(),
                }
            },
        }
    },
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
    resolved_trace_id = normalize_trace_id(trace_id)
    content_type = request.headers.get("content-type", "").partition(";")[0].strip()
    if content_type.lower() != "application/json":
        return agent_problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="VALIDATION_FAILED",
            title="Validation failed",
            detail="The conversation phase is invalid.",
            trace_id=resolved_trace_id,
        )
    try:
        raw_data = await request.json()
    except (UnicodeDecodeError, ValueError):
        return agent_problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="VALIDATION_FAILED",
            title="Validation failed",
            detail="The conversation phase is invalid.",
            trace_id=resolved_trace_id,
        )

    try:
        result = await _run_agent_database(
            app.services.authority_conversation.write_conversation_turn,
            workload_client_id=principal.client_id,
            mount_context_token=mount_context_token,
            interaction_ref=interaction_ref,
            client_turn_id=client_turn_id,
            idempotency_key=idempotency_key,
            payload=data.model_dump(mode="json"),
            settings=settings,
        )
    except history.ConversationContextChanged:
        return agent_problem_response(
            status_code=409,
            code="CONVERSATION_CONTEXT_CHANGED",
            title="Conversation context changed",
            detail="The recent conversation no longer authorizes this response.",
            trace_id=resolved_trace_id,
        )
    except provider_question.ProviderContextChanged:
        return agent_problem_response(
            status_code=status.HTTP_409_CONFLICT,
            code="PROVIDER_CONTEXT_CHANGED",
            title="Provider context changed",
            detail="The preparation context no longer authorizes this response.",
            trace_id=resolved_trace_id,
        )
    except app.services.session_prep.SessionPrepScopeDenied:
        return agent_problem_response(
            status_code=status.HTTP_403_FORBIDDEN,
            code="SCOPE_DENIED",
            title="Scope denied",
            detail="The current mount context is not authorized.",
            trace_id=resolved_trace_id,
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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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


@agent_api_app.post(
    "/audit-events/write",
    response_model=WriteReceipt,
    status_code=status.HTTP_201_CREATED,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": AuditWriteRequest.model_json_schema(),
                }
            },
        }
    },
)
async def write_audit_event(
    request: Request,
    response: Response,
    principal: Annotated[
        EntraServicePrincipal,
        Depends(authenticate_agent_api_request),
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
    mount_context_token: Annotated[
        str | None,
        Header(
            alias="X-Cosmaneura-Mount-Context",
            min_length=16,
            max_length=8192,
        ),
    ] = None,
    trace_id: Annotated[
        str | None,
        Header(alias="X-Trace-ID"),
    ] = None,
) -> WriteReceipt | JSONResponse:
    resolved_trace_id = normalize_trace_id(trace_id)
    data = await _validated_audit_body(request, trace_id=resolved_trace_id)
    if isinstance(data, JSONResponse):
        return data
    try:
        result = await _run_agent_database(
            app.services.authority_audit.write_observational_audit_event,
            workload_client_id=principal.client_id,
            interaction_ref=interaction_ref,
            idempotency_key=idempotency_key,
            payload=data.model_dump(mode="json"),
            mount_context_token=mount_context_token,
            settings=settings,
        )
    except app.services.authority.AuthorityKernelDisabledError:
        return agent_problem_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="PLATFORM_UNAVAILABLE",
            title="Platform unavailable",
            detail="Observational audit ingestion is not available.",
            trace_id=resolved_trace_id,
        )
    except app.services.authority.ContextTokenInvalidError:
        return agent_problem_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="CONTEXT_INVALID",
            title="Context invalid",
            detail="A supplied mount context is invalid.",
            trace_id=resolved_trace_id,
        )
    except app.services.authority.InteractionInvalidError:
        return agent_problem_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="INTERACTION_INVALID",
            title="Interaction invalid",
            detail="A valid interaction lineage is required.",
            trace_id=resolved_trace_id,
        )
    except app.services.authority.AuthorityRequestDeniedError:
        return agent_problem_response(
            status_code=status.HTTP_403_FORBIDDEN,
            code="SCOPE_DENIED",
            title="Scope denied",
            detail="The interaction is not authorized for audit reporting.",
            trace_id=resolved_trace_id,
        )
    except (
        app.services.authority.ClientTurnConflictError,
        app.services.authority_audit.AuditIdempotencyConflictError,
        app.services.authority_audit.AuditLineageClosedError,
    ):
        return agent_problem_response(
            status_code=status.HTTP_409_CONFLICT,
            code="IDEMPOTENCY_CONFLICT",
            title="Idempotency conflict",
            detail="The audit receipt is bound to another request.",
            trace_id=resolved_trace_id,
        )
    except (
        app.services.authority_audit.AuditPayloadInvalidError,
        app.services.authority_audit.AuditReferenceInvalidError,
    ):
        return agent_problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="VALIDATION_FAILED",
            title="Validation failed",
            detail="The observational audit event is invalid.",
            trace_id=resolved_trace_id,
        )
    except (
        app.services.authority_audit.AuditPersistenceStateError,
        SQLAlchemyError,
    ):
        return agent_problem_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="PLATFORM_UNAVAILABLE",
            title="Platform unavailable",
            detail="Observational audit ingestion is temporarily unavailable.",
            retryable=True,
            trace_id=resolved_trace_id,
        )

    response.headers["X-Trace-ID"] = resolved_trace_id
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    response.headers["Cache-Control"] = "no-store"
    return WriteReceipt(
        record_ref=result.record_ref,
        recorded_at=result.recorded_at,
    )


@agent_api_app.post(
    "/model-usage-events/report",
    response_model=WriteReceipt,
    status_code=status.HTTP_201_CREATED,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": ModelUsageRequest.model_json_schema(),
                }
            },
        }
    },
)
async def report_model_usage_event(
    request: Request,
    response: Response,
    principal: Annotated[
        EntraServicePrincipal,
        Depends(authenticate_agent_api_request),
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
    mount_context_token: Annotated[
        str | None,
        Header(
            alias="X-Cosmaneura-Mount-Context",
            min_length=16,
            max_length=8192,
        ),
    ] = None,
    trace_id: Annotated[
        str | None,
        Header(alias="X-Trace-ID"),
    ] = None,
) -> WriteReceipt | JSONResponse:
    resolved_trace_id = normalize_trace_id(trace_id)
    data = await _validated_usage_body(request, trace_id=resolved_trace_id)
    if isinstance(data, JSONResponse):
        return data
    try:
        result = await _run_agent_database(
            app.services.authority_usage.report_model_usage_event,
            workload_client_id=principal.client_id,
            interaction_ref=interaction_ref,
            idempotency_key=idempotency_key,
            payload=data.model_dump(mode="json"),
            mount_context_token=mount_context_token,
            settings=settings,
        )
    except app.services.authority.AuthorityKernelDisabledError:
        return agent_problem_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="PLATFORM_UNAVAILABLE",
            title="Platform unavailable",
            detail="Model usage ingestion is not available.",
            trace_id=resolved_trace_id,
        )
    except app.services.authority.ContextTokenInvalidError:
        return agent_problem_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="CONTEXT_INVALID",
            title="Context invalid",
            detail="A supplied mount context is invalid.",
            trace_id=resolved_trace_id,
        )
    except app.services.authority.InteractionInvalidError:
        return agent_problem_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="INTERACTION_INVALID",
            title="Interaction invalid",
            detail="A valid interaction lineage is required.",
            trace_id=resolved_trace_id,
        )
    except app.services.authority.AuthorityRequestDeniedError:
        return agent_problem_response(
            status_code=status.HTTP_403_FORBIDDEN,
            code="SCOPE_DENIED",
            title="Scope denied",
            detail="The interaction is not authorized for usage reporting.",
            trace_id=resolved_trace_id,
        )
    except (
        app.services.authority.ClientTurnConflictError,
        app.services.authority_usage.ModelUsageIdempotencyConflictError,
        app.services.authority_usage.ModelUsageLineageClosedError,
    ):
        return agent_problem_response(
            status_code=status.HTTP_409_CONFLICT,
            code="IDEMPOTENCY_CONFLICT",
            title="Idempotency conflict",
            detail="The usage receipt conflicts with the current interaction.",
            trace_id=resolved_trace_id,
        )
    except app.services.authority_usage.ModelUsagePhaseRequiredError:
        return agent_problem_response(
            status_code=status.HTTP_409_CONFLICT,
            code="TURN_PHASE_REQUIRED",
            title="Turn phase required",
            detail="The accepted conversation phase is not durable.",
            trace_id=resolved_trace_id,
        )
    except app.services.authority_usage.ModelUsagePayloadInvalidError:
        return agent_problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="VALIDATION_FAILED",
            title="Validation failed",
            detail="The model usage event is invalid.",
            trace_id=resolved_trace_id,
        )
    except (
        app.services.authority_usage.ModelUsagePersistenceStateError,
        SQLAlchemyError,
    ):
        return agent_problem_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="PLATFORM_UNAVAILABLE",
            title="Platform unavailable",
            detail="Model usage ingestion is temporarily unavailable.",
            retryable=True,
            trace_id=resolved_trace_id,
        )

    response.headers["X-Trace-ID"] = resolved_trace_id
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    response.headers["Cache-Control"] = "no-store"
    return WriteReceipt(
        record_ref=result.record_ref,
        recorded_at=result.recorded_at,
    )


@agent_api_app.post(
    "/conversation-turns/read", response_model=history.ConversationReadResponse
)
async def read_conversation_turns(
    request: Request,
    principal: Annotated[
        EntraServicePrincipal, Depends(authenticate_agent_api_request)
    ],
    mount_context_token: Annotated[
        str, Header(alias="X-Cosmaneura-Mount-Context", min_length=16, max_length=8192)
    ],
    settings: Annotated[AuthorityKernelSettings, Depends(get_agent_authority_settings)],
    trace_id: Annotated[str | None, Header(alias="X-Trace-ID")] = None,
):
    resolved_trace_id = normalize_trace_id(trace_id)

    def problem(code: str, status_code: int):
        return agent_problem_response(
            status_code=status_code,
            code=code,
            title="Conversation read unavailable",
            detail="The current context could not read the requested conversation.",
            trace_id=resolved_trace_id,
        )

    if (
        len(request.headers.getlist("x-cosmaneura-mount-context")) != 1
        or len(request.headers.getlist("x-trace-id")) > 1
        or len(request.headers.getlist("content-type")) != 1
        or len(request.headers.getlist("content-length")) > 1
        or request.query_params
        or request.headers.get("content-encoding", "identity").lower()
        not in {"", "identity"}
        or request.headers.get("content-type", "").partition(";")[0].strip().lower()
        != "application/json"
    ):
        return problem("VALIDATION_FAILED", 422)
    try:
        raw = bytearray()
        async for chunk in request.stream():
            if len(chunk) > 8192 - len(raw):
                return problem("VALIDATION_FAILED", 422)
            raw.extend(chunk)
        body = history.ConversationReadRequest.model_validate(
            json.loads(raw, object_pairs_hook=_closed_json_object)
        )
    except (ValueError, UnicodeDecodeError, RecursionError):
        return problem("VALIDATION_FAILED", 422)

    def read_in_session():
        with get_session() as managed:
            return history.read_conversation(
                session=cast(Session, managed),
                workload_client_id=principal.client_id,
                mount_context_token=mount_context_token,
                request=body,
                settings=settings,
            )

    try:
        result = await _run_agent_database(read_in_session)
    except history.ConversationNotFound:
        return problem("RESOURCE_NOT_FOUND", 404)
    except history.ConversationContextChanged:
        return problem("CONVERSATION_CONTEXT_CHANGED", 409)
    except app.services.authority.ContextTokenExpiredError:
        return problem("CONTEXT_EXPIRED", 401)
    except app.services.authority.ContextTokenInvalidError:
        return problem("CONTEXT_INVALID", 401)
    except (
        app.services.authority.AuthorityRequestDeniedError,
        app.services.session_prep.SessionPrepScopeDenied,
    ):
        return problem("SCOPE_DENIED", 403)
    except (
        app.services.authority.AuthorityKernelDisabledError,
        app.services.session_prep.SessionPrepInvariantError,
        SQLAlchemyError,
    ):
        return problem("PLATFORM_UNAVAILABLE", 503)
    return JSONResponse(
        result, headers={"Cache-Control": "no-store", "X-Trace-ID": resolved_trace_id}
    )
