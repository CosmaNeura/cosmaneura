"""Durable execution fences for side-effecting Zoom webhook handlers.

The inbox prevents concurrent handling of one delivery while its lease is
healthy.  A worker can still die after mutating Redis, creating a Recall bot,
or starting a recording but before completing the inbox row.  These receipts
make that crash boundary explicit: work that never began a side effect may be
retried, while an expired in-flight effect is quarantined as uncertain instead
of being repeated blindly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from typeid import TypeID

from activemodel.session_manager import get_session
from app.models.external_webhook_operation import ExternalWebhookOperation
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

EXECUTION_LEASE = timedelta(minutes=10)
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_OPERATION_TYPES = {
    "meeting.started": "zoom_meeting_started",
    "meeting.participant_joined": "zoom_participant_joined",
    "meeting.participant_left": "zoom_participant_left",
    "meeting.ended": "zoom_meeting_ended",
}


@dataclass(frozen=True)
class ZoomEventClaim:
    operation_id: str
    fence: int
    disposition: Literal["execute", "busy", "succeeded", "uncertain"]


def _operation_id(value: str) -> TypeID:
    parsed = TypeID.from_string(value)
    if parsed.prefix != "webhook_operation":
        raise ValueError("invalid operation reference")
    return parsed


def _operation_type(event_type: str) -> str:
    try:
        return _OPERATION_TYPES[event_type]
    except KeyError as exc:
        raise ValueError("unsupported Zoom event type") from exc


def _validate_digest(provider_event_digest: str) -> None:
    if not _DIGEST_RE.fullmatch(provider_event_digest):
        raise ValueError("invalid provider event digest")


def _claim_existing(
    operation: ExternalWebhookOperation,
    *,
    now: datetime,
) -> ZoomEventClaim:
    if operation.state == "succeeded":
        return ZoomEventClaim(
            str(operation.id), operation.execution_attempt_count, "succeeded"
        )
    if operation.state in {"uncertain", "failed"}:
        return ZoomEventClaim(
            str(operation.id), operation.execution_attempt_count, "uncertain"
        )
    if (
        operation.execution_lease_expires_at is not None
        and operation.execution_lease_expires_at > now
    ):
        return ZoomEventClaim(
            str(operation.id), operation.execution_attempt_count, "busy"
        )
    if operation.state == "in_flight":
        operation.state = "uncertain"
        operation.execution_lease_expires_at = None
        operation.last_error_code = "provider_outcome_uncertain"
        return ZoomEventClaim(
            str(operation.id), operation.execution_attempt_count, "uncertain"
        )

    # A reserved handler never crossed its first side-effect boundary, so an
    # expired reservation can be safely reclaimed.
    operation.execution_attempt_count += 1
    operation.execution_lease_expires_at = now + EXECUTION_LEASE
    operation.last_error_code = None
    return ZoomEventClaim(
        str(operation.id), operation.execution_attempt_count, "execute"
    )


def claim_event(
    *,
    provider_event_digest: str,
    event_type: str,
    now: datetime | None = None,
) -> ZoomEventClaim:
    """Claim one Zoom delivery without exposing its provider identifier."""

    _validate_digest(provider_event_digest)
    operation_type = _operation_type(event_type)
    now = now or datetime.now(UTC)
    with get_session() as session:
        operation = session.exec(
            select(ExternalWebhookOperation)
            .where(
                ExternalWebhookOperation.provider == "zoom",
                ExternalWebhookOperation.operation_type == operation_type,
                ExternalWebhookOperation.provider_resource_digest
                == provider_event_digest,
            )
            .with_for_update()
        ).one_or_none()
        if operation is not None:
            claim = _claim_existing(operation, now=now)
            session.add(operation)
            session.commit()
            return claim

        operation = ExternalWebhookOperation(
            provider="zoom",
            operation_type=operation_type,
            provider_resource_digest=provider_event_digest,
            state="reserved",
            execution_attempt_count=1,
            execution_lease_expires_at=now + EXECUTION_LEASE,
        )
        session.add(operation)
        try:
            session.commit()
            session.refresh(operation)
            return ZoomEventClaim(str(operation.id), 1, "execute")
        except IntegrityError:
            session.rollback()
            operation = session.exec(
                select(ExternalWebhookOperation)
                .where(
                    ExternalWebhookOperation.provider == "zoom",
                    ExternalWebhookOperation.operation_type == operation_type,
                    ExternalWebhookOperation.provider_resource_digest
                    == provider_event_digest,
                )
                .with_for_update()
            ).one()
            claim = _claim_existing(operation, now=now)
            session.add(operation)
            session.commit()
            return claim


def begin_side_effect(
    claim: ZoomEventClaim,
    *,
    now: datetime | None = None,
) -> bool:
    """Cross the irreversible-effect boundary using a fenced update."""

    now = now or datetime.now(UTC)
    with get_session() as session:
        operation = session.exec(
            select(ExternalWebhookOperation)
            .where(
                ExternalWebhookOperation.id == _operation_id(claim.operation_id),
                ExternalWebhookOperation.provider == "zoom",
                ExternalWebhookOperation.state == "reserved",
                ExternalWebhookOperation.execution_attempt_count == claim.fence,
            )
            .with_for_update()
        ).one_or_none()
        if operation is None:
            return False
        operation.state = "in_flight"
        operation.execution_lease_expires_at = now + EXECUTION_LEASE
        session.add(operation)
        session.commit()
        return True


def complete_event(claim: ZoomEventClaim) -> bool:
    """Commit successful completion for the current execution fence."""

    with get_session() as session:
        operation = session.exec(
            select(ExternalWebhookOperation)
            .where(
                ExternalWebhookOperation.id == _operation_id(claim.operation_id),
                ExternalWebhookOperation.provider == "zoom",
                cast(Any, ExternalWebhookOperation.state).in_(
                    ["reserved", "in_flight"]
                ),
                ExternalWebhookOperation.execution_attempt_count == claim.fence,
            )
            .with_for_update()
        ).one_or_none()
        if operation is None:
            return False
        operation.state = "succeeded"
        operation.execution_lease_expires_at = None
        operation.last_error_code = None
        session.add(operation)
        session.commit()
        return True


def fail_event(claim: ZoomEventClaim, *, permanent: bool = False) -> bool:
    """Release a safe reservation or quarantine an ambiguous side effect.

    Returns true when a side effect may have occurred and the event must not be
    retried automatically.
    """

    with get_session() as session:
        operation = session.exec(
            select(ExternalWebhookOperation)
            .where(
                ExternalWebhookOperation.id == _operation_id(claim.operation_id),
                ExternalWebhookOperation.provider == "zoom",
                ExternalWebhookOperation.execution_attempt_count == claim.fence,
            )
            .with_for_update()
        ).one_or_none()
        if operation is None:
            return True
        if operation.state == "reserved":
            if permanent:
                operation.state = "failed"
            operation.execution_lease_expires_at = None
            operation.last_error_code = None
            session.add(operation)
            session.commit()
            return False
        if operation.state == "in_flight":
            operation.state = "uncertain"
            operation.execution_lease_expires_at = None
            operation.last_error_code = "provider_outcome_uncertain"
            session.add(operation)
            session.commit()
            return True
        return operation.state != "succeeded"
