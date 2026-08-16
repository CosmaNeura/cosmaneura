from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID

import app.services.authority as authority
from app.configuration.authority import (
    AUTHORITY_KERNEL_SETTINGS,
    AuthorityKernelSettings,
)

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.authority import AuthorityAuditEvent, AuthorityIdempotencyRecord
from app.models.authority_conversation import (
    CanonicalConversationFinalPhase,
    CanonicalConversationTurn,
    CanonicalConversationUserPhase,
)
from sqlmodel import Session, select

USER_ACCEPTED_OPERATION = "conversation_turn.user_accepted"
FINAL_RESPONSE_OPERATION = "conversation_turn.final_response"
IDEMPOTENCY_RETENTION = timedelta(hours=24)

SUPPRESSION_NAMES = frozenset(
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
ACTION_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{1,127}$")
CONTACT_ACTION_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{8,128}$")
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{16,128}$")


class ConversationKernelError(Exception):
    pass


class ConversationInvariantError(ConversationKernelError):
    pass


class ConversationStateError(ConversationKernelError):
    pass


class TurnPhaseRequiredError(ConversationKernelError):
    pass


class IdempotencyConflictError(ConversationKernelError):
    pass


@dataclass(frozen=True)
class ConversationWriteResult:
    client_turn_id: UUID
    turn_ref: str
    phase: Literal["user_accepted", "final_response"]
    state: Literal["accepted", "completed"]
    recorded_at: datetime
    replayed: bool


def _has_exact_keys(value: dict[str, Any], expected: set[str]) -> bool:
    return set(value) == expected


def _canonical_message(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or not _has_exact_keys(
        value,
        {"content_type", "content"},
    ):
        raise ConversationInvariantError
    content = value.get("content")
    if (
        value.get("content_type") != "text/plain"
        or not isinstance(content, str)
        or not 1 <= len(content) <= 20_000
    ):
        raise ConversationInvariantError
    return {"content_type": "text/plain", "content": content}


def _canonical_contact(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not _has_exact_keys(
        value,
        {"label", "action_id", "fallback_text"},
    ):
        raise ConversationInvariantError
    label = value.get("label")
    action_id = value.get("action_id")
    fallback_text = value.get("fallback_text")
    if (
        not isinstance(label, str)
        or not 1 <= len(label) <= 120
        or not isinstance(action_id, str)
        or CONTACT_ACTION_PATTERN.fullmatch(action_id) is None
        or not isinstance(fallback_text, str)
        or not 1 <= len(fallback_text) <= 240
    ):
        raise ConversationInvariantError
    return {
        "label": label,
        "action_id": action_id,
        "fallback_text": fallback_text,
    }


def _canonical_payload(payload: dict[str, Any]) -> dict[str, Any]:
    phase = payload.get("phase")
    if phase == "user_accepted":
        if not _has_exact_keys(
            payload,
            {"phase", "ingress_kind", "action_code", "message"},
        ):
            raise ConversationInvariantError
        ingress_kind = payload.get("ingress_kind")
        action_code = payload.get("action_code")
        if ingress_kind == "chat_text":
            if action_code is not None:
                raise ConversationInvariantError
        elif ingress_kind == "provider_ui_action":
            if (
                not isinstance(action_code, str)
                or ACTION_CODE_PATTERN.fullmatch(action_code) is None
            ):
                raise ConversationInvariantError
        else:
            raise ConversationInvariantError
        return {
            "phase": phase,
            "ingress_kind": ingress_kind,
            "action_code": action_code,
            "message": _canonical_message(payload.get("message")),
        }
    if phase == "final_response":
        if not _has_exact_keys(
            payload,
            {"phase", "outcome", "response", "presentation"},
        ):
            raise ConversationInvariantError
        outcome = payload.get("outcome")
        if outcome not in {"completed", "refused", "unavailable"}:
            raise ConversationInvariantError
        presentation = payload.get("presentation")
        if not isinstance(presentation, dict) or not _has_exact_keys(
            presentation,
            {"mode", "suppress", "human_contact"},
        ):
            raise ConversationInvariantError
        mode = presentation.get("mode")
        if mode not in {"standard", "emergency", "sensitive_disclosure"}:
            raise ConversationInvariantError
        suppress = presentation.get("suppress")
        if not isinstance(suppress, list) or any(
            not isinstance(value, str) for value in suppress
        ):
            raise ConversationInvariantError
        suppress_set = set(suppress)
        if len(suppress_set) != len(suppress) or not suppress_set <= SUPPRESSION_NAMES:
            raise ConversationInvariantError
        contact = _canonical_contact(presentation.get("human_contact"))
        if mode == "sensitive_disclosure" and (
            suppress_set != SUPPRESSION_NAMES or contact is None
        ):
            raise ConversationInvariantError
        return {
            "phase": phase,
            "outcome": outcome,
            "response": _canonical_message(payload.get("response")),
            "presentation": {
                "mode": mode,
                "suppress": sorted(suppress_set),
                "human_contact": contact,
            },
        }
    raise ConversationInvariantError


def _request_digest(payload: dict[str, Any]) -> str:
    canonical_json = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical_json.encode()).hexdigest()


def _operation_for_phase(phase: str) -> str:
    if phase == "user_accepted":
        return USER_ACCEPTED_OPERATION
    if phase == "final_response":
        return FINAL_RESPONSE_OPERATION
    raise ConversationInvariantError


def _turn_for_interaction(
    session: Session,
    interaction_id: TypeIDType,
) -> CanonicalConversationTurn | None:
    return session.exec(
        select(CanonicalConversationTurn).where(
            CanonicalConversationTurn.interaction_id == interaction_id
        )
    ).first()


def _idempotency_records(
    session: Session,
    *,
    workload_client_id: str,
    operation: str,
    interaction_id: TypeIDType,
    idempotency_key: str,
) -> tuple[AuthorityIdempotencyRecord | None, AuthorityIdempotencyRecord | None]:
    key_record = session.exec(
        select(AuthorityIdempotencyRecord).where(
            AuthorityIdempotencyRecord.workload_client_id == workload_client_id,
            AuthorityIdempotencyRecord.operation == operation,
            AuthorityIdempotencyRecord.interaction_id == interaction_id,
            AuthorityIdempotencyRecord.idempotency_key == idempotency_key,
        )
    ).first()
    logical_record = session.exec(
        select(AuthorityIdempotencyRecord).where(
            AuthorityIdempotencyRecord.workload_client_id == workload_client_id,
            AuthorityIdempotencyRecord.operation == operation,
            AuthorityIdempotencyRecord.interaction_id == interaction_id,
        )
    ).first()
    return key_record, logical_record


def _cross_phase_key_exists(
    session: Session,
    *,
    workload_client_id: str,
    interaction_id: TypeIDType,
    idempotency_key: str,
) -> bool:
    return (
        session.exec(
            select(AuthorityIdempotencyRecord).where(
                AuthorityIdempotencyRecord.workload_client_id == workload_client_id,
                AuthorityIdempotencyRecord.interaction_id == interaction_id,
                AuthorityIdempotencyRecord.idempotency_key == idempotency_key,
            )
        ).first()
        is not None
    )


def _receipt_for_existing(
    session: Session,
    *,
    turn: CanonicalConversationTurn | None,
    phase: str,
    record: AuthorityIdempotencyRecord,
) -> ConversationWriteResult:
    if (
        turn is None
        or record.status != "complete"
        or record.response_ref != str(turn.id)
    ):
        raise ConversationStateError
    if phase == "user_accepted":
        stored_phase = session.exec(
            select(CanonicalConversationUserPhase).where(
                CanonicalConversationUserPhase.turn_id == turn.id
            )
        ).first()
        state = "accepted"
    else:
        stored_phase = session.exec(
            select(CanonicalConversationFinalPhase).where(
                CanonicalConversationFinalPhase.turn_id == turn.id
            )
        ).first()
        state = "completed"
    if stored_phase is None or stored_phase.request_digest != record.request_digest:
        raise ConversationStateError
    recorded_at = stored_phase.recorded_at
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=UTC)
    return ConversationWriteResult(
        client_turn_id=turn.client_turn_id,
        turn_ref=str(turn.id),
        phase=cast(Literal["user_accepted", "final_response"], phase),
        state=state,
        recorded_at=recorded_at,
        replayed=True,
    )


def _audit_event(
    *,
    authorized: authority.AuthorizedInteraction,
    phase: str,
    turn_ref: str,
    request_digest: str,
) -> AuthorityAuditEvent:
    return AuthorityAuditEvent(
        event_type=f"canonical_conversation.{phase}",
        outcome="accepted",
        occurred_at=datetime.now(UTC),
        tenant_id=authorized.mount_session.tenant_id,
        actor_id=authorized.mount_session.actor_id,
        person_id=authorized.context.person_id,
        interaction_id=authorized.lineage.id,
        resource_type="canonical_conversation_turn",
        resource_ref=turn_ref,
        request_digest=request_digest,
    )


def _write_user_phase(
    session: Session,
    *,
    authorized: authority.AuthorizedInteraction,
    workload_client_id: str,
    client_turn_id: UUID,
    payload: dict[str, Any],
    request_digest: str,
    idempotency_key: str,
) -> ConversationWriteResult:
    if _turn_for_interaction(session, authorized.lineage.id) is not None:
        raise authority.ClientTurnConflictError
    ingress_kind = payload["ingress_kind"]
    if ingress_kind == "provider_ui_action" and (
        authorized.context.surface != "provider_assistant"
        or authorized.context.auth_tier != "provider"
    ):
        raise authority.AuthorityRequestDeniedError

    recorded_at = datetime.now(UTC)
    turn = CanonicalConversationTurn(
        interaction_id=authorized.lineage.id,
        workload_client_id=workload_client_id,
        client_turn_id=client_turn_id,
        tenant_id=authorized.mount_session.tenant_id,
        actor_id=authorized.mount_session.actor_id,
        person_id=authorized.context.person_id,
        created_at=recorded_at,
    )
    session.add(turn)
    session.flush()
    message = payload["message"]
    session.add(
        CanonicalConversationUserPhase(
            turn_id=turn.id,
            ingress_kind=ingress_kind,
            action_code=payload["action_code"],
            content_type=message["content_type"],
            content=message["content"],
            request_digest=request_digest,
            recorded_at=recorded_at,
        )
    )
    session.add(
        AuthorityIdempotencyRecord(
            workload_client_id=workload_client_id,
            operation=USER_ACCEPTED_OPERATION,
            interaction_id=authorized.lineage.id,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            status="complete",
            response_ref=str(turn.id),
            retain_until=recorded_at + IDEMPOTENCY_RETENTION,
        )
    )
    session.add(
        _audit_event(
            authorized=authorized,
            phase="user_accepted",
            turn_ref=str(turn.id),
            request_digest=request_digest,
        )
    )
    return ConversationWriteResult(
        client_turn_id=client_turn_id,
        turn_ref=str(turn.id),
        phase="user_accepted",
        state="accepted",
        recorded_at=recorded_at,
        replayed=False,
    )


def _write_final_phase(
    session: Session,
    *,
    authorized: authority.AuthorizedInteraction,
    workload_client_id: str,
    client_turn_id: UUID,
    payload: dict[str, Any],
    request_digest: str,
    idempotency_key: str,
) -> ConversationWriteResult:
    turn = _turn_for_interaction(session, authorized.lineage.id)
    if turn is None:
        raise TurnPhaseRequiredError
    user_phase = session.exec(
        select(CanonicalConversationUserPhase).where(
            CanonicalConversationUserPhase.turn_id == turn.id
        )
    ).first()
    if user_phase is None:
        raise ConversationStateError
    if (
        session.exec(
            select(CanonicalConversationFinalPhase).where(
                CanonicalConversationFinalPhase.turn_id == turn.id
            )
        ).first()
        is not None
    ):
        raise authority.ClientTurnConflictError

    recorded_at = datetime.now(UTC)
    message = payload["response"]
    presentation = payload["presentation"]
    suppress = set(presentation["suppress"])
    contact = presentation["human_contact"]
    session.add(
        CanonicalConversationFinalPhase(
            turn_id=turn.id,
            outcome=payload["outcome"],
            content_type=message["content_type"],
            content=message["content"],
            presentation_mode=presentation["mode"],
            suppress_disclaimer="disclaimer" in suppress,
            suppress_intent_badge="intent_badge" in suppress,
            suppress_doctrinal_badge="doctrinal_badge" in suppress,
            suppress_citations="citations" in suppress,
            suppress_structured_cards="structured_cards" in suppress,
            suppress_feature_actions="feature_actions" in suppress,
            suppress_guided_paths="guided_paths" in suppress,
            human_contact_label=(contact["label"] if contact else None),
            human_contact_action_id=(contact["action_id"] if contact else None),
            human_contact_fallback_text=(contact["fallback_text"] if contact else None),
            request_digest=request_digest,
            recorded_at=recorded_at,
        )
    )
    session.add(
        AuthorityIdempotencyRecord(
            workload_client_id=workload_client_id,
            operation=FINAL_RESPONSE_OPERATION,
            interaction_id=authorized.lineage.id,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            status="complete",
            response_ref=str(turn.id),
            retain_until=recorded_at + IDEMPOTENCY_RETENTION,
        )
    )
    session.add(
        _audit_event(
            authorized=authorized,
            phase="final_response",
            turn_ref=str(turn.id),
            request_digest=request_digest,
        )
    )
    return ConversationWriteResult(
        client_turn_id=client_turn_id,
        turn_ref=str(turn.id),
        phase="final_response",
        state="completed",
        recorded_at=recorded_at,
        replayed=False,
    )


def write_conversation_turn(
    *,
    workload_client_id: str,
    mount_context_token: str,
    interaction_ref: str,
    client_turn_id: UUID,
    idempotency_key: str,
    payload: dict[str, Any],
    session: Session | None = None,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
) -> ConversationWriteResult:
    """Persist exactly one canonical phase and its audit in one transaction."""

    if session is None:
        with get_session() as managed_session:
            return write_conversation_turn(
                workload_client_id=workload_client_id,
                mount_context_token=mount_context_token,
                interaction_ref=interaction_ref,
                client_turn_id=client_turn_id,
                idempotency_key=idempotency_key,
                payload=payload,
                session=cast(Session, managed_session),
                settings=settings,
            )

    authorized = authority.authorize_interaction(
        session=session,
        workload_client_id=workload_client_id,
        mount_context_token=mount_context_token,
        interaction_ref=interaction_ref,
        client_turn_id=client_turn_id,
        settings=settings,
    )
    if IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key) is None:
        raise ConversationInvariantError
    canonical_payload = _canonical_payload(payload)
    phase = cast(str, canonical_payload["phase"])
    operation = _operation_for_phase(phase)
    request_digest = _request_digest(canonical_payload)
    key_record, logical_record = _idempotency_records(
        session,
        workload_client_id=workload_client_id,
        operation=operation,
        interaction_id=authorized.lineage.id,
        idempotency_key=idempotency_key,
    )
    turn = _turn_for_interaction(session, authorized.lineage.id)
    if key_record is not None:
        if key_record.request_digest != request_digest:
            raise IdempotencyConflictError
        if logical_record is None or logical_record.id != key_record.id:
            raise ConversationStateError
        return _receipt_for_existing(
            session,
            turn=turn,
            phase=phase,
            record=key_record,
        )
    if logical_record is not None:
        raise authority.ClientTurnConflictError
    if _cross_phase_key_exists(
        session,
        workload_client_id=workload_client_id,
        interaction_id=authorized.lineage.id,
        idempotency_key=idempotency_key,
    ):
        raise IdempotencyConflictError

    if phase == "user_accepted":
        result = _write_user_phase(
            session,
            authorized=authorized,
            workload_client_id=workload_client_id,
            client_turn_id=client_turn_id,
            payload=canonical_payload,
            request_digest=request_digest,
            idempotency_key=idempotency_key,
        )
    else:
        result = _write_final_phase(
            session,
            authorized=authorized,
            workload_client_id=workload_client_id,
            client_turn_id=client_turn_id,
            payload=canonical_payload,
            request_digest=request_digest,
            idempotency_key=idempotency_key,
        )
    session.commit()
    return result
