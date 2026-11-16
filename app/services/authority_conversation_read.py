"""Governed encounter history and immutable, transitively grounded turn memory."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal, NamedTuple, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

import app.services.authority as authority
import app.services.authority_conversation as conversation
import app.services.provider_question_context as prep
from app.configuration.authority import (
    AUTHORITY_KERNEL_SETTINGS,
    AuthorityKernelSettings,
)
from app.services import reviewed_excerpt_citations as citations
from app.services.provider_database_deadline import provider_database_deadline
from app.services.session_prep import (
    OpaqueRef,
    SessionPrepIdempotencyConflict,
    SessionPrepScopeDenied,
)
from app.services.session_prep_runtime import SessionPrepSourceBusy

from app.models.authority import (
    AuthorityAuditEvent,
    AuthorityIdempotencyRecord,
    AuthorityPersonBinding,
    InteractionLineage,
    MountContextSession,
)
from app.models.authority_conversation import CanonicalConversationFinalPhase as Final
from app.models.authority_conversation import CanonicalConversationTurn as Turn
from app.models.authority_conversation import CanonicalConversationUserPhase as User
from app.models.provider_record_authority import ProviderRecordAuthority
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, select

OPERATION = "conversation.context.read"
TOOL_KEY = (OPERATION, "1.0.0")
MAX_MESSAGES_BYTES = 192 * 1024


class ConversationContextChanged(SessionPrepScopeDenied):
    """A retained memory snapshot no longer authorizes the response."""


class ConversationNotFound(SessionPrepScopeDenied):
    """A lookup has no visible chat turn in this current scope."""


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversationReadRequest(Closed):
    schema_version: Literal["1.0.0"]
    mode: Literal["recent", "turn"]
    client_turn_id: UUID | None

    @model_validator(mode="after")
    def selection(self):
        if (self.mode == "turn") != (self.client_turn_id is not None) or (
            self.client_turn_id is not None and self.client_turn_id.version != 4
        ):
            raise ValueError("Invalid turn selection")
        return self


class HistoryArguments(Closed):
    source_turn_ref: OpaqueRef
    detail: Literal["recent"]


class HistoryInvocation(Closed):
    tool_name: Literal["conversation.context.read"]
    tool_version: Literal["1.0.0"]
    subject_ref: None
    arguments: HistoryArguments


class MemoryMessage(Closed):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


class HistoryResult(Closed):
    schema_version: Literal["1.0.0"] = "1.0.0"
    messages: list[MemoryMessage] = Field(max_length=12)
    source_version_digest: str | None = Field(pattern=r"^sha256_[a-f0-9]{64}$")


class HistoryResponse(Closed):
    tool_call_ref: OpaqueRef
    status: Literal["completed"] = "completed"
    result: HistoryResult


class HumanContact(Closed):
    label: str = Field(min_length=1, max_length=120)
    action_id: str = Field(pattern=r"^[A-Za-z0-9._~-]{8,128}$")
    fallback_text: str = Field(min_length=1, max_length=240)


class Presentation(Closed):
    mode: Literal["standard", "emergency", "sensitive_disclosure"]
    suppress: list[
        Literal[
            "disclaimer",
            "intent_badge",
            "doctrinal_badge",
            "citations",
            "structured_cards",
            "feature_actions",
            "guided_paths",
        ]
    ] = Field(max_length=7)
    human_contact: HumanContact | None

    @model_validator(mode="after")
    def canonical(self):
        conversation._canonical_payload(
            {
                "phase": "final_response",
                "outcome": "completed",
                "response": {"content_type": "text/plain", "content": "validation"},
                "presentation": self.model_dump(mode="json"),
            }
        )
        return self


class DisplayMessage(MemoryMessage):
    turn_ref: OpaqueRef
    client_turn_id: UUID
    recorded_at: datetime
    presentation: Presentation | None


class Projection(Closed):
    thread_ref: OpaqueRef
    messages: list[DisplayMessage] = Field(max_length=12)
    next_cursor: None


class TurnStatus(Closed):
    turn_ref: OpaqueRef
    client_turn_id: UUID
    state: Literal["pending", "completed", "stale"]
    outcome: Literal["completed", "refused", "unavailable"] | None
    recorded_at: datetime


class ConversationReadResponse(Closed):
    schema_version: Literal["1.0.0"] = "1.0.0"
    history: Projection
    turns: list[TurnStatus] = Field(max_length=6)


class HistoryRead(NamedTuple):
    response: HistoryResponse
    replayed: bool


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _receipt(session: Session, turn: Turn, operation: str):
    return session.exec(
        select(AuthorityIdempotencyRecord).where(
            AuthorityIdempotencyRecord.workload_client_id == turn.workload_client_id,
            AuthorityIdempotencyRecord.interaction_id == turn.interaction_id,
            AuthorityIdempotencyRecord.operation == operation,
        )
    ).one_or_none()


def _live(receipt: AuthorityIdempotencyRecord, now: datetime) -> bool:
    return (
        receipt.status == "complete"
        and _utc(receipt.retain_until) > now
        and receipt.response_ref is not None
    )


def _scope(
    session: Session,
    context: authority.ResolvedMountContext,
    mount: MountContextSession,
):
    if (
        context.surface != "provider_assistant"
        or context.auth_tier != "provider"
        or "session-prep:read" not in context.capabilities
    ):
        raise SessionPrepScopeDenied
    scope = session.exec(
        select(ProviderRecordAuthority).where(
            ProviderRecordAuthority.binding_id == mount.person_binding_id
        )
    ).one_or_none()
    if scope is None or scope.context_snapshot_ref != context.session_prep_context_ref:
        raise SessionPrepScopeDenied
    return scope


def _query(workload: str, mount: MountContextSession, scope: ProviderRecordAuthority):
    # Encounter comes from the exact immutable binding, never the patient alone.
    return (
        select(Turn, User, Final)
        .join(User, cast(Any, User.turn_id) == Turn.id)
        .outerjoin(Final, cast(Any, Final.turn_id) == Turn.id)
        .join(
            InteractionLineage, cast(Any, Turn.interaction_id) == InteractionLineage.id
        )
        .join(
            MountContextSession,
            cast(Any, InteractionLineage.session_id) == MountContextSession.id,
        )
        .join(
            AuthorityPersonBinding,
            cast(Any, MountContextSession.person_binding_id)
            == AuthorityPersonBinding.id,
        )
        .join(
            ProviderRecordAuthority,
            cast(Any, ProviderRecordAuthority.binding_id) == AuthorityPersonBinding.id,
        )
        .where(
            Turn.workload_client_id == workload,
            InteractionLineage.workload_client_id == workload,
            Turn.client_turn_id == InteractionLineage.client_turn_id,
            Turn.actor_id == mount.actor_id,
            Turn.tenant_id == mount.tenant_id,
            Turn.person_id == AuthorityPersonBinding.person_id,
            MountContextSession.actor_id == mount.actor_id,
            MountContextSession.tenant_id == mount.tenant_id,
            MountContextSession.source_identity_id == mount.source_identity_id,
            MountContextSession.surface_ceiling == "provider_assistant",
            MountContextSession.auth_tier_ceiling == "provider",
            MountContextSession.status == "active",
            cast(Any, MountContextSession.revoked_at).is_(None),
            AuthorityPersonBinding.id == mount.person_binding_id,
            AuthorityPersonBinding.actor_id == mount.actor_id,
            AuthorityPersonBinding.tenant_id == mount.tenant_id,
            AuthorityPersonBinding.status == "active",
            cast(Any, AuthorityPersonBinding.revoked_at).is_(None),
            ProviderRecordAuthority.id == scope.id,
            User.ingress_kind == "chat_text",
            cast(Any, User.action_code).is_(None),
        )
    )


def _presentation(final: Final) -> dict[str, Any]:
    return {
        "mode": final.presentation_mode,
        "suppress": sorted(
            name
            for name in conversation.SUPPRESSION_NAMES
            if getattr(final, f"suppress_{name}")
        ),
        "human_contact": (
            {
                "label": final.human_contact_label,
                "action_id": final.human_contact_action_id,
                "fallback_text": final.human_contact_fallback_text,
            }
            if final.human_contact_label is not None
            else None
        ),
    }


def _marker(receipt: AuthorityIdempotencyRecord) -> tuple[datetime, str | None]:
    try:
        version, timestamp, dependency = cast(str, receipt.response_ref).split("|")
        if version != "h1":
            raise ValueError
        return datetime.fromisoformat(timestamp).astimezone(
            UTC
        ), None if dependency == "-" else dependency
    except (ValueError, AttributeError):
        raise ConversationContextChanged from None


def _source(
    session: Session,
    source_ref: str,
    workload: str,
    mount: MountContextSession,
    scope: ProviderRecordAuthority,
    now: datetime,
    settings: AuthorityKernelSettings,
):
    receipt = session.get(AuthorityIdempotencyRecord, source_ref)
    if (
        receipt is None
        or receipt.operation != prep.OPERATION
        or not _live(receipt, now)
    ):
        raise ConversationContextChanged
    row = session.exec(
        _query(workload, mount, scope).where(
            Turn.interaction_id == receipt.interaction_id
        )
    ).one_or_none()
    if (
        row is None
        or row[2] is None
        or receipt.logical_ref != str(row[0].id)
        or receipt.workload_client_id != workload
    ):
        raise ConversationContextChanged
    result, _ = prep._current_result(
        session, scope, now, settings, receipt.response_ref
    )
    if receipt.request_digest != prep._binding_digest(str(row[0].id), result):
        raise ConversationContextChanged
    return receipt, result


def _verified(
    session: Session,
    row,
    workload: str,
    mount: MountContextSession,
    scope: ProviderRecordAuthority,
    now: datetime,
    settings: AuthorityKernelSettings,
):
    turn, user, final = row
    user_payload = {
        "phase": "user_accepted",
        "ingress_kind": "chat_text",
        "action_code": None,
        "message": {"content_type": user.content_type, "content": user.content},
    }
    if user.request_digest != conversation._request_digest(
        conversation._canonical_payload(user_payload)
    ):
        raise ConversationContextChanged
    if final is None:
        return None
    payload: dict[str, Any] = {
        "phase": "final_response",
        "outcome": final.outcome,
        "response": {"content_type": final.content_type, "content": final.content},
        "presentation": _presentation(final),
    }
    bindings = final.reviewed_excerpt_bindings
    if bindings is not None:
        try:
            payload["reviewed_excerpts"] = citations.canonical_bindings(bindings)[
                "selection"
            ]
        except ValueError:
            raise ConversationContextChanged from None
    dependencies = []
    for operation, key in (
        (prep.OPERATION, "provider_context_ref"),
        (OPERATION, "history_context_ref"),
    ):
        receipt = _receipt(session, turn, operation)
        if receipt is None:
            continue
        if not _live(receipt, now) or receipt.logical_ref != str(turn.id):
            raise ConversationContextChanged
        payload[key] = str(receipt.id)
        source_ref = (
            str(receipt.id) if operation == prep.OPERATION else _marker(receipt)[1]
        )
        if source_ref is not None:
            dependency, result = _source(
                session, source_ref, workload, mount, scope, now, settings
            )
            if result.state != "ready" and final.outcome != "unavailable":
                raise ConversationContextChanged
            dependencies.append((dependency, result))
    # Missing direct/inherited receipts cannot masquerade as legacy independence:
    # their omitted ref changes the immutable final hash.
    if final.request_digest != conversation._request_digest(
        conversation._canonical_payload(payload), bindings
    ):
        raise ConversationContextChanged
    return (
        min(dependencies, key=lambda item: _utc(item[0].retain_until))
        if dependencies
        else None
    )


def _fence(
    session: Session, scope: ProviderRecordAuthority, markers: set[str], now: datetime
):
    try:
        for marker in sorted(markers):
            prep._lock_final_sources(session, scope, marker, now)
    except prep.ProviderContextChanged:
        raise ConversationContextChanged from None
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) != "55P03":
            raise
        session.rollback()
        raise ConversationContextChanged from None


def _select(
    session: Session,
    workload: str,
    mount: MountContextSession,
    scope: ProviderRecordAuthority,
    now: datetime,
    settings: AuthorityKernelSettings,
    *,
    cutoff: datetime | None = None,
    turn_id: UUID | None = None,
    exclude: str | None = None,
):
    query = _query(workload, mount, scope)
    if turn_id is not None:
        query = query.where(Turn.client_turn_id == turn_id)
    if cutoff is not None:
        query = query.where(
            Final.recorded_at <= cutoff,
            Final.outcome == "completed",
            Final.presentation_mode == "standard",
        )
    if exclude is not None:
        query = query.where(Turn.id != exclude)
    rows = list(
        session.exec(
            query.order_by(
                cast(Any, User.recorded_at).desc(), cast(Any, Turn.id).desc()
            ).limit(6)
        ).all()
    )
    # Lock sources before validating any returned clinical answer. These source
    # locks and the current Doctor/token locks are held through serialization.
    markers = set()
    for row in rows:
        try:
            dependency = _verified(session, row, workload, mount, scope, now, settings)
            if dependency:
                markers.add(cast(str, dependency[0].response_ref))
        except SessionPrepSourceBusy:
            raise ConversationContextChanged from None
        except (
            ConversationContextChanged,
            prep.ProviderContextChanged,
            conversation.ConversationInvariantError,
        ):
            pass  # Rechecked and classified as stale below; no stale source lock.
    if markers:
        _fence(session, scope, markers, now)
    selected = []
    for row in reversed(rows):
        try:
            dependency = _verified(session, row, workload, mount, scope, now, settings)
            if dependency and dependency[0].response_ref not in markers:
                raise ConversationContextChanged
            state = "completed" if row[2] is not None else "pending"
        except SessionPrepSourceBusy:
            raise ConversationContextChanged from None
        except (
            ConversationContextChanged,
            prep.ProviderContextChanged,
            conversation.ConversationInvariantError,
        ):
            dependency, state = None, "stale"
        if cutoff is None or state == "completed":
            selected.append((row, state, dependency))
    return selected


def _digest(turn_ref: str, result: HistoryResult, members: list[dict[str, str]]) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "request": {
                    "tool_name": OPERATION,
                    "tool_version": "1.0.0",
                    "subject_ref": None,
                    "arguments": {"source_turn_ref": turn_ref, "detail": "recent"},
                },
                "result": result.model_dump(mode="json"),
                "members": members,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def _memory(
    session: Session,
    authorized: authority.AuthorizedInteraction,
    scope: ProviderRecordAuthority,
    cutoff: datetime,
    now: datetime,
    settings: AuthorityKernelSettings,
    turn_ref: str,
):
    selected = _select(
        session,
        authorized.lineage.workload_client_id,
        authorized.mount_session,
        scope,
        now,
        settings,
        cutoff=cutoff,
        exclude=turn_ref,
    )
    while selected:
        content = [
            {"role": role, "content": phase.content}
            for row, _, _ in selected
            for role, phase in (("user", row[1]), ("assistant", row[2]))
        ]
        if (
            len(json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode())
            <= MAX_MESSAGES_BYTES
        ):
            break
        selected.pop(0)
    dependencies = [dependency for _, _, dependency in selected if dependency]
    source = (
        min(dependencies, key=lambda item: _utc(item[0].retain_until))
        if dependencies
        else None
    )
    result = HistoryResult(
        messages=[
            MemoryMessage(
                role=cast(Literal["user", "assistant"], role), content=phase.content
            )
            for row, _, _ in selected
            for role, phase in (("user", row[1]), ("assistant", row[2]))
        ],
        source_version_digest=source[1].source_version_digest if source else None,
    )
    members = [
        {
            "turn_ref": str(row[0].id),
            "user_digest": row[1].request_digest,
            "final_digest": row[2].request_digest,
        }
        for row, _, _ in selected
    ]
    return result, source[0] if source else None, members


def read_history_context(
    *,
    session: Session,
    workload_client_id: str,
    mount_context_token: str,
    interaction_ref: str,
    client_turn_id: UUID,
    idempotency_key: str,
    invocation: HistoryInvocation,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
) -> HistoryRead:
    authorized = authority.authorize_interaction(
        session=session,
        workload_client_id=workload_client_id,
        mount_context_token=mount_context_token,
        interaction_ref=interaction_ref,
        client_turn_id=client_turn_id,
        settings=settings,
    )
    turn_ref = invocation.arguments.source_turn_ref
    scope = prep._turn_and_scope(session, authorized, turn_ref)
    turn = session.get(Turn, turn_ref)
    assert turn is not None  # Verified by _turn_and_scope in this transaction.
    receipt = _receipt(session, turn, OPERATION)
    now = datetime.now(UTC)
    if receipt is not None:
        if receipt.idempotency_key != idempotency_key:
            raise SessionPrepIdempotencyConflict
        if not _live(receipt, now) or receipt.logical_ref != turn_ref:
            raise ConversationContextChanged
        cutoff, _ = _marker(receipt)
    else:
        if (
            session.exec(select(Final).where(Final.turn_id == turn.id)).one_or_none()
            is not None
        ):
            raise ConversationContextChanged
        cutoff = now
    result, dependency, members = _memory(
        session, authorized, scope, cutoff, now, settings, turn_ref
    )
    marker = f"h1|{cutoff.isoformat()}|{dependency.id if dependency else '-'}"
    digest = _digest(turn_ref, result, members)
    replayed = receipt is not None
    if receipt is not None and (
        receipt.request_digest != digest or receipt.response_ref != marker
    ):
        raise ConversationContextChanged
    if receipt is None:
        receipt = AuthorityIdempotencyRecord(
            workload_client_id=workload_client_id,
            operation=OPERATION,
            interaction_id=authorized.lineage.id,
            logical_ref=turn_ref,
            idempotency_key=idempotency_key,
            request_digest=digest,
            status="complete",
            response_ref=marker,
            retain_until=min(now + prep.RETENTION, _utc(dependency.retain_until))
            if dependency
            else now + prep.RETENTION,
        )
        session.add(receipt)
        session.flush()
        session.add(
            AuthorityAuditEvent(
                event_type=OPERATION,
                outcome="accepted",
                occurred_at=now,
                tenant_id=authorized.mount_session.tenant_id,
                actor_id=authorized.mount_session.actor_id,
                person_id=authorized.context.person_id,
                interaction_id=authorized.lineage.id,
                resource_type="conversation_context",
                resource_ref=str(receipt.id),
                request_digest=digest,
            )
        )
    response = HistoryResponse(tool_call_ref=str(receipt.id), result=result)
    session.commit()
    return HistoryRead(response, replayed)


def validate_final_history(
    session: Session,
    *,
    authorized: authority.AuthorizedInteraction,
    payload: dict[str, Any],
    settings: AuthorityKernelSettings,
):
    turn = session.exec(
        select(Turn).where(Turn.interaction_id == authorized.lineage.id)
    ).one_or_none()
    receipt = _receipt(session, turn, OPERATION) if turn else None
    if receipt is None:
        if payload.get("history_context_ref") is not None:
            raise ConversationContextChanged
        return
    now = datetime.now(UTC)
    if not _live(receipt, now) or payload.get("history_context_ref") != str(receipt.id):
        raise ConversationContextChanged
    scope = prep._turn_and_scope(session, authorized, receipt.logical_ref)
    cutoff, _ = _marker(receipt)
    result, dependency, members = _memory(
        session, authorized, scope, cutoff, now, settings, receipt.logical_ref
    )
    if (
        receipt.request_digest != _digest(receipt.logical_ref, result, members)
        or receipt.response_ref
        != f"h1|{cutoff.isoformat()}|{dependency.id if dependency else '-'}"
    ):
        raise ConversationContextChanged
    return {
        "context_ref": str(receipt.id),
        "response_ref": receipt.response_ref,
        "request_digest": receipt.request_digest,
        "cutoff": cutoff.isoformat(),
        "dependency_ref": str(dependency.id) if dependency else None,
        "source_version_digest": result.source_version_digest,
        "members": members,
    }


@provider_database_deadline
def read_conversation(
    *,
    session: Session,
    workload_client_id: str,
    mount_context_token: str,
    request: ConversationReadRequest,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
) -> dict[str, Any]:
    context, mount = authority.authorize_mount_read(
        session=session, mount_context_token=mount_context_token, settings=settings
    )
    scope = _scope(session, context, mount)
    selected = _select(
        session,
        workload_client_id,
        mount,
        scope,
        datetime.now(UTC),
        settings,
        turn_id=request.client_turn_id,
    )
    if request.mode == "turn" and not selected:
        raise ConversationNotFound
    messages, statuses = [], []
    for (turn, user, final), state, _ in selected:
        common = {"turn_ref": str(turn.id), "client_turn_id": str(turn.client_turn_id)}
        messages.append(
            {
                **common,
                "role": "user",
                "content": user.content,
                "recorded_at": _utc(user.recorded_at).isoformat(),
                "presentation": None,
            }
        )
        if state == "completed":
            messages.append(
                {
                    **common,
                    "role": "assistant",
                    "content": final.content,
                    "recorded_at": _utc(final.recorded_at).isoformat(),
                    "presentation": _presentation(final),
                }
            )
        statuses.append(
            {
                **common,
                "state": state,
                "outcome": final.outcome if state == "completed" else None,
                "recorded_at": _utc(
                    final.recorded_at if state == "completed" else user.recorded_at
                ).isoformat(),
            }
        )
    while (
        len(json.dumps(messages, ensure_ascii=False, separators=(",", ":")).encode())
        > MAX_MESSAGES_BYTES
    ):
        removed = statuses.pop(0)["turn_ref"]
        messages = [message for message in messages if message["turn_ref"] != removed]
    result = {
        "schema_version": "1.0.0",
        "history": {
            "thread_ref": str(scope.binding_id),
            "messages": messages,
            "next_cursor": None,
        },
        "turns": statuses,
    }
    validated = ConversationReadResponse.model_validate(result).model_dump(mode="json")
    session.commit()
    return validated
