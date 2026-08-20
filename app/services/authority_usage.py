"""Provider-first, content-free model-usage ingestion for Agent API turns."""

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
from app.models.authority import AuthorityIdempotencyRecord
from app.models.authority_conversation import (
    CanonicalConversationFinalPhase,
    CanonicalConversationTurn,
    CanonicalConversationUserPhase,
)
from app.models.authority_usage import AgentModelUsageEvent
from sqlmodel import Session, select

MODEL_USAGE_OPERATION = "model_usage_events.report"
IDEMPOTENCY_RETENTION = timedelta(hours=24)
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{16,128}$")
SAFE_LABEL_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{1,127}$")
CALL_KINDS = frozenset(
    {
        "classification",
        "routing",
        "reasoning",
        "drafting",
        "doctrinal_arbitration",
        "tool_support",
        "embedding",
        "other",
    }
)
MODEL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
USAGE_AVAILABILITY = frozenset({"complete", "partial", "unavailable"})
USAGE_SOURCES = frozenset(
    {"provider_response", "provider_headers", "platform_meter", "not_available"}
)
USAGE_PAYLOAD_KEYS = frozenset(
    {
        "client_event_id",
        "call_kind",
        "call_sequence",
        "workstream",
        "provider",
        "provider_request_id",
        "model",
        "deployment",
        "purpose",
        "started_at",
        "completed_at",
        "latency_ms",
        "status",
        "usage_availability",
        "usage_source",
        "token_usage",
    }
)
TOKEN_USAGE_KEYS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "reasoning_tokens",
    }
)


class ModelUsageWriteError(Exception):
    pass


class ModelUsagePayloadInvalidError(ModelUsageWriteError):
    pass


class ModelUsageIdempotencyConflictError(ModelUsageWriteError):
    pass


class ModelUsageLineageClosedError(ModelUsageWriteError):
    pass


class ModelUsagePhaseRequiredError(ModelUsageWriteError):
    pass


class ModelUsagePersistenceStateError(ModelUsageWriteError):
    pass


@dataclass(frozen=True)
class ModelUsageWriteResult:
    record_ref: str
    recorded_at: datetime
    replayed: bool


def _utc_datetime(value: object, *, nullable: bool) -> datetime | None:
    if value is None:
        if nullable:
            return None
        raise ModelUsagePayloadInvalidError
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ModelUsagePayloadInvalidError from exc
    else:
        raise ModelUsagePayloadInvalidError
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ModelUsagePayloadInvalidError
    return parsed.astimezone(UTC)


def _canonical_timestamp(value: object) -> str | None:
    parsed = _utc_datetime(value, nullable=True)
    if parsed is None:
        return None
    return parsed.isoformat().replace("+00:00", "Z")


def _safe_optional_identifier(
    value: object,
    *,
    minimum: int = 0,
) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not minimum <= len(value) <= 200
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ModelUsagePayloadInvalidError
    return value


def _nullable_nonnegative_integer(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ModelUsagePayloadInvalidError
    return value


def _safe_label(value: object) -> str:
    if not isinstance(value, str) or SAFE_LABEL_PATTERN.fullmatch(value) is None:
        raise ModelUsagePayloadInvalidError
    return value


def _canonical_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Revalidate the contract's closed, no-content usage schema."""

    if set(payload) != USAGE_PAYLOAD_KEYS:
        raise ModelUsagePayloadInvalidError
    try:
        client_event_id = str(UUID(str(payload["client_event_id"])))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ModelUsagePayloadInvalidError from exc

    call_kind = payload["call_kind"]
    call_sequence = payload["call_sequence"]
    model_status = payload["status"]
    usage_availability = payload["usage_availability"]
    usage_source = payload["usage_source"]
    token_usage = payload["token_usage"]
    if (
        call_kind not in CALL_KINDS
        or isinstance(call_sequence, bool)
        or not isinstance(call_sequence, int)
        or call_sequence < 1
        or model_status not in MODEL_STATUSES
        or usage_availability not in USAGE_AVAILABILITY
        or usage_source not in USAGE_SOURCES
        or not isinstance(token_usage, dict)
        or set(token_usage) != TOKEN_USAGE_KEYS
    ):
        raise ModelUsagePayloadInvalidError

    started_at = _utc_datetime(payload["started_at"], nullable=True)
    completed_at = _utc_datetime(payload["completed_at"], nullable=True)
    if started_at is not None and completed_at is not None and completed_at < started_at:
        raise ModelUsagePayloadInvalidError

    return {
        "client_event_id": client_event_id,
        "call_kind": call_kind,
        "call_sequence": call_sequence,
        "workstream": _safe_label(payload["workstream"]),
        "provider": _safe_label(payload["provider"]),
        "provider_request_id": _safe_optional_identifier(
            payload["provider_request_id"]
        ),
        "model": _safe_optional_identifier(payload["model"], minimum=1),
        "deployment": _safe_optional_identifier(payload["deployment"]),
        "purpose": _safe_label(payload["purpose"]),
        "started_at": _canonical_timestamp(payload["started_at"]),
        "completed_at": _canonical_timestamp(payload["completed_at"]),
        "latency_ms": _nullable_nonnegative_integer(payload["latency_ms"]),
        "status": model_status,
        "usage_availability": usage_availability,
        "usage_source": usage_source,
        "token_usage": {
            key: _nullable_nonnegative_integer(token_usage[key])
            for key in (
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
                "reasoning_tokens",
            )
        },
    }


def _request_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()


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
            AuthorityIdempotencyRecord.operation == MODEL_USAGE_OPERATION,
            AuthorityIdempotencyRecord.interaction_id == interaction_id,
            AuthorityIdempotencyRecord.idempotency_key == idempotency_key,
        )
    ).first()
    by_event = session.exec(
        select(AuthorityIdempotencyRecord).where(
            AuthorityIdempotencyRecord.workload_client_id == workload_client_id,
            AuthorityIdempotencyRecord.operation == MODEL_USAGE_OPERATION,
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
) -> ModelUsageWriteResult:
    if record.request_digest != request_digest:
        raise ModelUsageIdempotencyConflictError
    if record.status != "complete" or record.response_ref is None:
        raise ModelUsagePersistenceStateError
    try:
        event = session.get(AgentModelUsageEvent, record.response_ref)
    except (TypeError, ValueError):
        raise ModelUsagePersistenceStateError from None
    if (
        event is None
        or event.interaction_id != authorized.lineage.id
        or event.workload_client_id != workload_client_id
        or str(event.client_event_id) != client_event_id
        or event.request_digest != request_digest
        or event.tenant_id != authorized.mount_session.tenant_id
        or event.actor_id != authorized.mount_session.actor_id
        or event.person_id != authorized.context.person_id
    ):
        raise ModelUsagePersistenceStateError
    recorded_at = event.recorded_at
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=UTC)
    return ModelUsageWriteResult(
        record_ref=str(event.id),
        recorded_at=recorded_at,
        replayed=True,
    )


def _require_open_conversation_phase(
    session: Session,
    *,
    authorized: authority.AuthorizedInteraction,
) -> None:
    turn = session.exec(
        select(CanonicalConversationTurn).where(
            CanonicalConversationTurn.interaction_id == authorized.lineage.id
        )
    ).first()
    if turn is None:
        raise ModelUsagePhaseRequiredError
    user_phase = session.exec(
        select(CanonicalConversationUserPhase).where(
            CanonicalConversationUserPhase.turn_id == turn.id
        )
    ).first()
    if user_phase is None:
        raise ModelUsagePhaseRequiredError
    final_phase = session.exec(
        select(CanonicalConversationFinalPhase).where(
            CanonicalConversationFinalPhase.turn_id == turn.id
        )
    ).first()
    if final_phase is not None:
        raise ModelUsageLineageClosedError


def report_model_usage_event(
    *,
    workload_client_id: str,
    interaction_ref: str,
    idempotency_key: str,
    payload: dict[str, Any],
    mount_context_token: str | None = None,
    session: Session | None = None,
    settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS,
) -> ModelUsageWriteResult:
    """Append one model call and its 24-hour exact-replay receipt atomically."""

    if session is None:
        with get_session() as managed_session:
            return report_model_usage_event(
                workload_client_id=workload_client_id,
                interaction_ref=interaction_ref,
                idempotency_key=idempotency_key,
                payload=payload,
                mount_context_token=mount_context_token,
                session=cast(Session, managed_session),
                settings=settings,
            )
    if IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key) is None:
        raise ModelUsagePayloadInvalidError
    canonical = _canonical_payload(payload)
    request_digest = _request_digest(canonical)
    authorized = authority.authorize_observational_reporting(
        session=session,
        workload_client_id=workload_client_id,
        interaction_ref=interaction_ref,
        mount_context_token=mount_context_token,
        settings=settings,
    )
    client_event_id = cast(str, canonical["client_event_id"])
    by_key, by_event = _idempotency_records(
        session,
        workload_client_id=workload_client_id,
        interaction_id=authorized.lineage.id,
        idempotency_key=idempotency_key,
        client_event_id=client_event_id,
    )
    if by_key is not None or by_event is not None:
        if by_key is None or by_event is None or by_key.id != by_event.id:
            raise ModelUsageIdempotencyConflictError
        return _replay_result(
            session,
            authorized=authorized,
            workload_client_id=workload_client_id,
            client_event_id=client_event_id,
            record=by_key,
            request_digest=request_digest,
        )

    if (
        authorized.lineage.audit_status in {"complete", "failed"}
        or authorized.lineage.usage_status in {"complete", "failed"}
    ):
        raise ModelUsageLineageClosedError
    _require_open_conversation_phase(session, authorized=authorized)
    existing_sequence = session.exec(
        select(AgentModelUsageEvent).where(
            AgentModelUsageEvent.interaction_id == authorized.lineage.id,
            AgentModelUsageEvent.call_sequence == canonical["call_sequence"],
        )
    ).first()
    if existing_sequence is not None:
        raise ModelUsageIdempotencyConflictError

    now = datetime.now(UTC)
    token_usage = cast(dict[str, int | None], canonical["token_usage"])
    event = AgentModelUsageEvent(
        interaction_id=authorized.lineage.id,
        workload_client_id=workload_client_id,
        client_event_id=UUID(client_event_id),
        tenant_id=cast(TypeIDType, authorized.mount_session.tenant_id),
        actor_id=cast(TypeIDType, authorized.mount_session.actor_id),
        person_id=cast(TypeIDType, authorized.context.person_id),
        surface=authorized.context.surface,
        auth_tier=authorized.context.auth_tier,
        call_kind=cast(str, canonical["call_kind"]),
        call_sequence=cast(int, canonical["call_sequence"]),
        workstream=cast(str, canonical["workstream"]),
        provider=cast(str, canonical["provider"]),
        provider_request_id=cast(str | None, canonical["provider_request_id"]),
        model=cast(str | None, canonical["model"]),
        deployment=cast(str | None, canonical["deployment"]),
        purpose=cast(str, canonical["purpose"]),
        started_at=_utc_datetime(canonical["started_at"], nullable=True),
        completed_at=_utc_datetime(canonical["completed_at"], nullable=True),
        latency_ms=cast(int | None, canonical["latency_ms"]),
        status=cast(str, canonical["status"]),
        usage_availability=cast(str, canonical["usage_availability"]),
        usage_source=cast(str, canonical["usage_source"]),
        input_tokens=token_usage["input_tokens"],
        output_tokens=token_usage["output_tokens"],
        cached_input_tokens=token_usage["cached_input_tokens"],
        reasoning_tokens=token_usage["reasoning_tokens"],
        request_digest=request_digest,
        recorded_at=now,
    )
    session.add(event)
    session.flush()
    session.add(
        AuthorityIdempotencyRecord(
            workload_client_id=workload_client_id,
            operation=MODEL_USAGE_OPERATION,
            interaction_id=authorized.lineage.id,
            logical_ref=client_event_id,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            status="complete",
            response_ref=str(event.id),
            retain_until=now + IDEMPOTENCY_RETENTION,
        )
    )
    session.commit()
    return ModelUsageWriteResult(
        record_ref=str(event.id),
        recorded_at=now,
        replayed=False,
    )
