"""Durable, provider-first ingestion for Sovereign observational audit events."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import orjson

import app.services.authority as authority
from app.configuration.authority import (
    AUTHORITY_KERNEL_SETTINGS,
    AuthorityKernelSettings,
)

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.authority import (
    AuthorityAuditEvent,
    AuthorityIdempotencyRecord,
    AuthorityPerson,
)
from app.models.authority_conversation import (
    CanonicalConversationFinalPhase,
    CanonicalConversationTurn,
    CanonicalConversationUserPhase,
)
from app.models.authority_usage import AgentModelUsageEvent
from sqlalchemy.exc import StatementError
from sqlmodel import Session, select

AUDIT_WRITE_OPERATION = "audit_events.write"
IDEMPOTENCY_RETENTION = timedelta(hours=24)
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{16,128}$")
OPAQUE_REF_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{8,160}$")
SAFE_LABEL_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{1,127}$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")

AUDIT_PAYLOAD_KEYS = frozenset(
    {
        "client_event_id",
        "observed_at",
        "event_type",
        "observation_status",
        "route",
        "intent",
        "content_sha256",
        "platform_refs",
    }
)
PLATFORM_REFERENCE_KINDS = frozenset(
    {
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
    }
)
TERMINAL_TURN_EVENTS = {
    "sovereign.turn.completed": "completed",
    "sovereign.turn.refused": "refused",
    "sovereign.turn.failed": "failed",
    "sovereign.turn.cancelled": "cancelled",
}
FINAL_OUTCOME_STATUSES = {
    "completed": "completed",
    "refused": "refused",
    "unavailable": "failed",
}


class AuditWriteError(Exception):
    pass


class AuditPayloadInvalidError(AuditWriteError):
    pass


class AuditReferenceInvalidError(AuditWriteError):
    pass


class AuditIdempotencyConflictError(AuditWriteError):
    pass


class AuditPersistenceStateError(AuditWriteError):
    pass


class AuditLineageClosedError(AuditWriteError):
    pass


@dataclass(frozen=True)
class AuditWriteResult:
    record_ref: str
    recorded_at: datetime
    replayed: bool


def _utc_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise AuditPayloadInvalidError from exc
    else:
        raise AuditPayloadInvalidError
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AuditPayloadInvalidError
    return parsed.astimezone(UTC)


def _canonical_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Revalidate the closed, no-content schema before hashing or persistence."""

    if set(payload) != AUDIT_PAYLOAD_KEYS:
        raise AuditPayloadInvalidError
    try:
        client_event_id = str(UUID(str(payload["client_event_id"])))
    except (ValueError, TypeError, AttributeError) as exc:
        raise AuditPayloadInvalidError from exc

    observed_at = _utc_datetime(payload["observed_at"])
    event_type = payload["event_type"]
    observation_status = payload["observation_status"]
    route = payload["route"]
    intent = payload["intent"]
    content_sha256 = payload["content_sha256"]
    platform_refs = payload["platform_refs"]
    if (
        not isinstance(event_type, str)
        or SAFE_LABEL_PATTERN.fullmatch(event_type) is None
        or observation_status not in {"completed", "refused", "failed", "cancelled"}
        or (
            route is not None
            and (
                not isinstance(route, str)
                or SAFE_LABEL_PATTERN.fullmatch(route) is None
            )
        )
        or (
            intent is not None
            and (
                not isinstance(intent, str)
                or SAFE_LABEL_PATTERN.fullmatch(intent) is None
            )
        )
        or (
            content_sha256 is not None
            and (
                not isinstance(content_sha256, str)
                or SHA256_PATTERN.fullmatch(content_sha256) is None
            )
        )
        or not isinstance(platform_refs, list)
        or len(platform_refs) > 50
    ):
        raise AuditPayloadInvalidError

    canonical_refs: list[dict[str, str]] = []
    seen_refs: set[tuple[str, str]] = set()
    for reference in platform_refs:
        if not isinstance(reference, dict) or set(reference) != {"kind", "ref"}:
            raise AuditPayloadInvalidError
        kind = reference.get("kind")
        ref = reference.get("ref")
        if (
            not isinstance(kind, str)
            or kind not in PLATFORM_REFERENCE_KINDS
            or not isinstance(ref, str)
            or OPAQUE_REF_PATTERN.fullmatch(ref) is None
            or (kind, ref) in seen_refs
        ):
            raise AuditPayloadInvalidError
        seen_refs.add((kind, ref))
        canonical_refs.append({"kind": kind, "ref": ref})

    return {
        "client_event_id": client_event_id,
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "event_type": event_type,
        "observation_status": observation_status,
        "route": route,
        "intent": intent,
        "content_sha256": content_sha256,
        "platform_refs": canonical_refs,
    }


def _request_digest(payload: dict[str, Any]) -> str:
    canonical_json = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
    return hashlib.sha256(canonical_json).hexdigest()


def _validate_platform_references(
    session: Session,
    *,
    authorized: authority.AuthorizedInteraction,
    references: list[dict[str, str]],
) -> None:
    """Resolve every supplied reference inside the exact interaction scope."""

    for reference in references:
        kind = reference["kind"]
        ref = reference["ref"]
        try:
            if kind == "person":
                person = session.get(AuthorityPerson, ref)
                valid = (
                    person is not None
                    and person.status == "active"
                    and person.id == authorized.context.person_id
                )
            elif kind == "conversation_turn":
                turn = session.get(CanonicalConversationTurn, ref)
                valid = (
                    turn is not None
                    and turn.interaction_id == authorized.lineage.id
                    and turn.workload_client_id == authorized.lineage.workload_client_id
                )
            elif kind == "model_usage":
                usage = session.get(AgentModelUsageEvent, ref)
                valid = (
                    usage is not None
                    and usage.interaction_id == authorized.lineage.id
                    and usage.workload_client_id
                    == authorized.lineage.workload_client_id
                )
            else:
                valid = False
        except (TypeError, ValueError, StatementError):
            valid = False
        if not valid:
            raise AuditReferenceInvalidError


def _validate_terminal_usage_closure(
    session: Session,
    *,
    authorized: authority.AuthorizedInteraction,
    payload: dict[str, Any],
) -> bool:
    """Prove a terminal turn names every contiguous model-call ledger entry."""

    expected_status = TERMINAL_TURN_EVENTS.get(cast(str, payload["event_type"]))
    if expected_status is None:
        return False
    if payload["observation_status"] != expected_status:
        raise AuditPayloadInvalidError

    conversation_refs = [
        reference["ref"]
        for reference in payload["platform_refs"]
        if reference["kind"] == "conversation_turn"
    ]
    if len(conversation_refs) != 1:
        raise AuditReferenceInvalidError
    try:
        turn = session.get(CanonicalConversationTurn, conversation_refs[0])
    except (TypeError, ValueError, StatementError):
        raise AuditReferenceInvalidError from None
    if (
        turn is None
        or turn.interaction_id != authorized.lineage.id
        or turn.workload_client_id != authorized.lineage.workload_client_id
    ):
        raise AuditReferenceInvalidError
    user_phase = session.exec(
        select(CanonicalConversationUserPhase).where(
            CanonicalConversationUserPhase.turn_id == turn.id
        )
    ).first()
    final_phase = session.exec(
        select(CanonicalConversationFinalPhase).where(
            CanonicalConversationFinalPhase.turn_id == turn.id
        )
    ).first()
    if user_phase is None or final_phase is None:
        raise AuditReferenceInvalidError
    if FINAL_OUTCOME_STATUSES.get(final_phase.outcome) != expected_status:
        raise AuditPayloadInvalidError
    expected_content_sha256 = hashlib.sha256(final_phase.content.encode()).hexdigest()
    if payload["content_sha256"] != expected_content_sha256:
        raise AuditPayloadInvalidError

    usage_events = session.exec(
        select(AgentModelUsageEvent)
        .where(AgentModelUsageEvent.interaction_id == authorized.lineage.id)
        .order_by(AgentModelUsageEvent.call_sequence)
    ).all()
    expected_sequences = list(range(1, len(usage_events) + 1))
    actual_sequences = [event.call_sequence for event in usage_events]
    supplied_refs = {
        reference["ref"]
        for reference in payload["platform_refs"]
        if reference["kind"] == "model_usage"
    }
    ledger_refs = {str(event.id) for event in usage_events}
    if actual_sequences != expected_sequences or supplied_refs != ledger_refs:
        raise AuditReferenceInvalidError
    return True


def _idempotency_records(
    session: Session,
    *,
    workload_client_id: str,
    interaction_id: TypeIDType,
    idempotency_key: str,
    client_event_id: str,
) -> tuple[AuthorityIdempotencyRecord | None, AuthorityIdempotencyRecord | None]:
    by_key = session.exec(
        select(AuthorityIdempotencyRecord).where(
            AuthorityIdempotencyRecord.workload_client_id == workload_client_id,
            AuthorityIdempotencyRecord.operation == AUDIT_WRITE_OPERATION,
            AuthorityIdempotencyRecord.interaction_id == interaction_id,
            AuthorityIdempotencyRecord.idempotency_key == idempotency_key,
        )
    ).first()
    by_event = session.exec(
        select(AuthorityIdempotencyRecord).where(
            AuthorityIdempotencyRecord.workload_client_id == workload_client_id,
            AuthorityIdempotencyRecord.operation == AUDIT_WRITE_OPERATION,
            AuthorityIdempotencyRecord.interaction_id == interaction_id,
            AuthorityIdempotencyRecord.logical_ref == client_event_id,
        )
    ).first()
    return by_key, by_event


def _replay_result(
    session: Session,
    *,
    authorized: authority.AuthorizedInteraction,
    workload_client_id: str,
    client_event_id: str,
    record: AuthorityIdempotencyRecord,
    request_digest: str,
) -> AuditWriteResult:
    if (
        record.status != "complete"
        or record.request_digest != request_digest
        or record.response_ref is None
    ):
        if record.request_digest != request_digest:
            raise AuditIdempotencyConflictError
        raise AuditPersistenceStateError
    event = session.get(AuthorityAuditEvent, record.response_ref)
    if (
        event is None
        or event.event_source != "sovereign_observational"
        or event.interaction_id != authorized.lineage.id
        or event.workload_client_id != workload_client_id
        or str(event.client_event_id) != client_event_id
        or event.tenant_id != authorized.mount_session.tenant_id
        or event.actor_id != authorized.mount_session.actor_id
        or event.person_id != authorized.context.person_id
        or event.request_digest != request_digest
    ):
        raise AuditPersistenceStateError
    recorded_at = event.recorded_at
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=UTC)
    return AuditWriteResult(
        record_ref=str(event.id),
        recorded_at=recorded_at,
        replayed=True,
    )


def write_observational_audit_event(
    *,
    workload_client_id: str,
    interaction_ref: str,
    idempotency_key: str,
    payload: dict[str, Any],
    mount_context_token: str | None = None,
    session: Session | None = None,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
) -> AuditWriteResult:
    """Commit observation, receipt, and lineage acknowledgement atomically."""

    if session is None:
        with get_session() as managed_session:
            return write_observational_audit_event(
                workload_client_id=workload_client_id,
                interaction_ref=interaction_ref,
                idempotency_key=idempotency_key,
                payload=payload,
                mount_context_token=mount_context_token,
                session=cast(Session, managed_session),
                settings=settings,
            )

    if IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key) is None:
        raise AuditPayloadInvalidError
    canonical_payload = _canonical_payload(payload)
    request_digest = _request_digest(canonical_payload)
    authorized = authority.authorize_observational_reporting(
        session=session,
        workload_client_id=workload_client_id,
        interaction_ref=interaction_ref,
        mount_context_token=mount_context_token,
        settings=settings,
    )
    _validate_platform_references(
        session,
        authorized=authorized,
        references=canonical_payload["platform_refs"],
    )

    client_event_id = cast(str, canonical_payload["client_event_id"])
    by_key, by_event = _idempotency_records(
        session,
        workload_client_id=workload_client_id,
        interaction_id=authorized.lineage.id,
        idempotency_key=idempotency_key,
        client_event_id=client_event_id,
    )
    if by_key is not None or by_event is not None:
        if by_key is None or by_event is None or by_key.id != by_event.id:
            raise AuditIdempotencyConflictError
        return _replay_result(
            session,
            authorized=authorized,
            workload_client_id=workload_client_id,
            client_event_id=client_event_id,
            record=by_key,
            request_digest=request_digest,
        )

    if authorized.lineage.audit_status in {"complete", "failed"}:
        raise AuditLineageClosedError
    terminal = _validate_terminal_usage_closure(
        session,
        authorized=authorized,
        payload=canonical_payload,
    )
    if terminal and authorized.lineage.usage_status == "failed":
        raise AuditLineageClosedError

    now = datetime.now(UTC)
    observed_at = _utc_datetime(canonical_payload["observed_at"])
    event = AuthorityAuditEvent(
        event_source="sovereign_observational",
        event_type=cast(str, canonical_payload["event_type"]),
        outcome=cast(str, canonical_payload["observation_status"]),
        occurred_at=observed_at,
        recorded_at=now,
        workload_client_id=workload_client_id,
        client_event_id=UUID(client_event_id),
        tenant_id=authorized.mount_session.tenant_id,
        actor_id=authorized.mount_session.actor_id,
        person_id=authorized.context.person_id,
        interaction_id=authorized.lineage.id,
        resource_type="sovereign_observation",
        resource_ref=client_event_id,
        request_digest=request_digest,
        route=canonical_payload["route"],
        intent=canonical_payload["intent"],
        content_sha256=canonical_payload["content_sha256"],
        platform_refs=canonical_payload["platform_refs"],
    )
    session.add(event)
    session.flush()
    session.add(
        AuthorityIdempotencyRecord(
            workload_client_id=workload_client_id,
            operation=AUDIT_WRITE_OPERATION,
            interaction_id=authorized.lineage.id,
            logical_ref=client_event_id,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            status="complete",
            response_ref=str(event.id),
            retain_until=now + IDEMPOTENCY_RETENTION,
        )
    )
    if terminal:
        authorized.lineage.audit_status = "complete"
        authorized.lineage.usage_status = "complete"
        authorized.lineage.completed_at = now
        session.add(authorized.lineage)
    session.commit()
    return AuditWriteResult(
        record_ref=str(event.id),
        recorded_at=now,
        replayed=False,
    )
