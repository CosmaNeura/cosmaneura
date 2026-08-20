"""Value-free operator reconciliation for incomplete Agent API ingestion."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.authority import (
    AuthorityAuditEvent,
    AuthorityIdempotencyRecord,
    AuthorityPersonBinding,
    InteractionLineage,
    MountContextSession,
)
from app.models.authority_conversation import (
    CanonicalConversationFinalPhase,
    CanonicalConversationTurn,
    CanonicalConversationUserPhase,
)
from sqlmodel import Session, select

OPERATOR_FINAL_OPERATION = "conversation_turn.final_response"
OPERATOR_FINAL_CONTENT = "This response could not be completed. Please try again."
OPERATOR_FINAL_PAYLOAD = {
    "phase": "final_response",
    "outcome": "unavailable",
    "response": {
        "content_type": "text/plain",
        "content": OPERATOR_FINAL_CONTENT,
    },
    "presentation": {
        "mode": "standard",
        "suppress": [],
        "human_contact": None,
    },
}
OPERATOR_FINAL_REQUEST_DIGEST = hashlib.sha256(
    json.dumps(
        OPERATOR_FINAL_PAYLOAD,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
).hexdigest()
OPERATOR_FINAL_CONTENT_SHA256 = hashlib.sha256(
    OPERATOR_FINAL_CONTENT.encode()
).hexdigest()


class IngestionReconciliationError(Exception):
    pass


class IngestionReconciliationStateError(IngestionReconciliationError):
    pass


@dataclass(frozen=True)
class IncompleteInteraction:
    interaction_ref: str
    audit_status: str
    usage_status: str


@dataclass(frozen=True)
class IngestionStatusReport:
    older_than_minutes: int
    matching_interaction_count: int
    pending_audit_count: int
    failed_audit_count: int
    pending_usage_count: int
    failed_usage_count: int
    interactions: tuple[IncompleteInteraction, ...]


@dataclass(frozen=True)
class IngestionReconciliationResult:
    interaction_ref: str
    audit_status: str
    usage_status: str
    changed: bool


@dataclass(frozen=True)
class CanonicalTurnReconciliationResult:
    turn_ref: str
    interaction_ref: str
    state: Literal["eligible", "reconciled"]
    dry_run: bool
    changed: bool


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def incomplete_ingestion_status(
    *,
    older_than_minutes: int,
    limit: int,
    session: Session | None = None,
) -> IngestionStatusReport:
    """Return only aggregate state and opaque refs for stale incomplete turns."""

    if older_than_minutes < 1 or not 1 <= limit <= 1000:
        raise IngestionReconciliationError
    if session is None:
        with get_session() as managed_session:
            return incomplete_ingestion_status(
                older_than_minutes=older_than_minutes,
                limit=limit,
                session=cast(Session, managed_session),
            )
    cutoff = datetime.now(UTC) - timedelta(minutes=older_than_minutes)
    rows = session.exec(
        select(InteractionLineage)
        .where(
            cast(Any, InteractionLineage.created_at) <= cutoff,
            (InteractionLineage.audit_status != "complete")
            | (InteractionLineage.usage_status != "complete"),
        )
        .order_by(
            cast(Any, InteractionLineage.created_at),
            cast(Any, InteractionLineage.interaction_ref),
        )
    ).all()
    limited = rows[:limit]
    return IngestionStatusReport(
        older_than_minutes=older_than_minutes,
        matching_interaction_count=len(rows),
        pending_audit_count=sum(row.audit_status == "pending" for row in rows),
        failed_audit_count=sum(row.audit_status == "failed" for row in rows),
        pending_usage_count=sum(row.usage_status == "pending" for row in rows),
        failed_usage_count=sum(row.usage_status == "failed" for row in rows),
        interactions=tuple(
            IncompleteInteraction(
                interaction_ref=row.interaction_ref,
                audit_status=row.audit_status,
                usage_status=row.usage_status,
            )
            for row in limited
        ),
    )


def mark_stale_ingestion_failed(
    *,
    interaction_ref: str,
    component: Literal["audit", "usage", "both"],
    older_than_minutes: int,
    session: Session | None = None,
) -> IngestionReconciliationResult:
    """Idempotently fail a stale pending component and append an authority fact."""

    if older_than_minutes < 1 or component not in {"audit", "usage", "both"}:
        raise IngestionReconciliationError
    if session is None:
        with get_session() as managed_session:
            return mark_stale_ingestion_failed(
                interaction_ref=interaction_ref,
                component=component,
                older_than_minutes=older_than_minutes,
                session=cast(Session, managed_session),
            )
    lineage = session.exec(
        select(InteractionLineage)
        .where(InteractionLineage.interaction_ref == interaction_ref)
        .with_for_update()
    ).first()
    if lineage is None:
        raise IngestionReconciliationStateError
    cutoff = datetime.now(UTC) - timedelta(minutes=older_than_minutes)
    if lineage.created_at is None or _aware(lineage.created_at) > cutoff:
        raise IngestionReconciliationStateError

    components = ("audit", "usage") if component == "both" else (component,)
    for name in components:
        if getattr(lineage, f"{name}_status") == "complete":
            raise IngestionReconciliationStateError

    mount_session = session.get(MountContextSession, lineage.session_id)
    binding = (
        session.get(AuthorityPersonBinding, mount_session.person_binding_id)
        if mount_session is not None and mount_session.person_binding_id is not None
        else None
    )
    now = datetime.now(UTC)
    changed = False
    for name in components:
        attribute = f"{name}_status"
        if getattr(lineage, attribute) == "failed":
            continue
        setattr(lineage, attribute, "failed")
        changed = True
        session.add(
            AuthorityAuditEvent(
                event_source="platform_authoritative",
                event_type=f"interaction.{name}_ingestion.failed",
                outcome="failed",
                occurred_at=now,
                workload_client_id=lineage.workload_client_id,
                tenant_id=(
                    mount_session.tenant_id if mount_session is not None else None
                ),
                actor_id=(
                    mount_session.actor_id if mount_session is not None else None
                ),
                person_id=(binding.person_id if binding is not None else None),
                interaction_id=lineage.id,
                resource_type=f"interaction_{name}_ingestion",
                resource_ref=lineage.interaction_ref,
            )
        )
    if changed:
        session.add(lineage)
        session.commit()
    return IngestionReconciliationResult(
        interaction_ref=lineage.interaction_ref,
        audit_status=lineage.audit_status,
        usage_status=lineage.usage_status,
        changed=changed,
    )


def _operator_final_is_exact(final: CanonicalConversationFinalPhase) -> bool:
    return (
        final.outcome == "unavailable"
        and final.content_type == "text/plain"
        and final.content == OPERATOR_FINAL_CONTENT
        and final.presentation_mode == "standard"
        and not final.suppress_disclaimer
        and not final.suppress_intent_badge
        and not final.suppress_doctrinal_badge
        and not final.suppress_citations
        and not final.suppress_structured_cards
        and not final.suppress_feature_actions
        and not final.suppress_guided_paths
        and final.human_contact_label is None
        and final.human_contact_action_id is None
        and final.human_contact_fallback_text is None
        and final.request_digest == OPERATOR_FINAL_REQUEST_DIGEST
    )


def reconcile_stale_accepted_turn(
    *,
    turn_id: TypeIDType,
    older_than_minutes: int,
    apply: bool,
    session: Session | None = None,
) -> CanonicalTurnReconciliationResult:
    """Dry-run or reconcile one exact stale accepted turn with a fixed final."""

    if older_than_minutes < 1:
        raise IngestionReconciliationError
    if session is None:
        with get_session() as managed_session:
            return reconcile_stale_accepted_turn(
                turn_id=turn_id,
                older_than_minutes=older_than_minutes,
                apply=apply,
                session=cast(Session, managed_session),
            )

    turn = session.exec(
        select(CanonicalConversationTurn)
        .where(CanonicalConversationTurn.id == turn_id)
        .with_for_update()
    ).one_or_none()
    if turn is None:
        raise IngestionReconciliationStateError
    lineage = session.exec(
        select(InteractionLineage)
        .where(InteractionLineage.id == turn.interaction_id)
        .with_for_update()
    ).one_or_none()
    user_phase = session.exec(
        select(CanonicalConversationUserPhase).where(
            CanonicalConversationUserPhase.turn_id == turn.id
        )
    ).one_or_none()
    if (
        lineage is None
        or user_phase is None
        or lineage.workload_client_id != turn.workload_client_id
        or lineage.client_turn_id != turn.client_turn_id
        or lineage.audit_status != "pending"
        or lineage.usage_status != "pending"
    ):
        raise IngestionReconciliationStateError

    existing_final = session.exec(
        select(CanonicalConversationFinalPhase).where(
            CanonicalConversationFinalPhase.turn_id == turn.id
        )
    ).one_or_none()
    idempotency_key = f"operator-final-{turn.client_turn_id}"
    idempotency = session.exec(
        select(AuthorityIdempotencyRecord).where(
            AuthorityIdempotencyRecord.workload_client_id == turn.workload_client_id,
            AuthorityIdempotencyRecord.operation == OPERATOR_FINAL_OPERATION,
            AuthorityIdempotencyRecord.interaction_id == lineage.id,
            AuthorityIdempotencyRecord.logical_ref == str(turn.client_turn_id),
        )
    ).one_or_none()
    audit = session.exec(
        select(AuthorityAuditEvent).where(
            AuthorityAuditEvent.event_type
            == "canonical_conversation.final_response.operator_reconciled",
            AuthorityAuditEvent.interaction_id == lineage.id,
            AuthorityAuditEvent.resource_ref == str(turn.id),
        )
    ).one_or_none()
    if existing_final is not None:
        if (
            not _operator_final_is_exact(existing_final)
            or idempotency is None
            or idempotency.idempotency_key != idempotency_key
            or idempotency.request_digest != OPERATOR_FINAL_REQUEST_DIGEST
            or idempotency.status != "complete"
            or idempotency.response_ref != str(turn.id)
            or audit is None
            or audit.request_digest != OPERATOR_FINAL_REQUEST_DIGEST
            or audit.content_sha256 != OPERATOR_FINAL_CONTENT_SHA256
        ):
            raise IngestionReconciliationStateError
        return CanonicalTurnReconciliationResult(
            turn_ref=str(turn.id),
            interaction_ref=lineage.interaction_ref,
            state="reconciled",
            dry_run=not apply,
            changed=False,
        )
    if idempotency is not None or audit is not None:
        raise IngestionReconciliationStateError

    cutoff = datetime.now(UTC) - timedelta(minutes=older_than_minutes)
    if _aware(user_phase.recorded_at) > cutoff:
        raise IngestionReconciliationStateError
    if not apply:
        return CanonicalTurnReconciliationResult(
            turn_ref=str(turn.id),
            interaction_ref=lineage.interaction_ref,
            state="eligible",
            dry_run=True,
            changed=False,
        )

    recorded_at = datetime.now(UTC)
    session.add(
        CanonicalConversationFinalPhase(
            turn_id=turn.id,
            outcome="unavailable",
            content_type="text/plain",
            content=OPERATOR_FINAL_CONTENT,
            presentation_mode="standard",
            suppress_disclaimer=False,
            suppress_intent_badge=False,
            suppress_doctrinal_badge=False,
            suppress_citations=False,
            suppress_structured_cards=False,
            suppress_feature_actions=False,
            suppress_guided_paths=False,
            request_digest=OPERATOR_FINAL_REQUEST_DIGEST,
            recorded_at=recorded_at,
        )
    )
    session.add(
        AuthorityIdempotencyRecord(
            workload_client_id=turn.workload_client_id,
            operation=OPERATOR_FINAL_OPERATION,
            interaction_id=lineage.id,
            logical_ref=str(turn.client_turn_id),
            idempotency_key=idempotency_key,
            request_digest=OPERATOR_FINAL_REQUEST_DIGEST,
            status="complete",
            response_ref=str(turn.id),
            retain_until=recorded_at + timedelta(hours=24),
        )
    )
    session.add(
        AuthorityAuditEvent(
            event_source="platform_authoritative",
            event_type="canonical_conversation.final_response.operator_reconciled",
            outcome="unavailable",
            occurred_at=recorded_at,
            workload_client_id=turn.workload_client_id,
            tenant_id=turn.tenant_id,
            actor_id=turn.actor_id,
            person_id=turn.person_id,
            interaction_id=lineage.id,
            resource_type="canonical_conversation_turn",
            resource_ref=str(turn.id),
            request_digest=OPERATOR_FINAL_REQUEST_DIGEST,
            content_sha256=OPERATOR_FINAL_CONTENT_SHA256,
            platform_refs=[{"kind": "conversation_turn", "ref": str(turn.id)}],
        )
    )
    session.commit()
    return CanonicalTurnReconciliationResult(
        turn_ref=str(turn.id),
        interaction_ref=lineage.interaction_ref,
        state="reconciled",
        dry_run=False,
        changed=True,
    )
