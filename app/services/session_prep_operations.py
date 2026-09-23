"""Bounded receipt expiry and metadata-only preparation queue health."""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from activemodel.session_manager import get_session
from app.models.session_prep_runtime import SessionPrepBuild, SessionPrepReadReceipt
from sqlalchemy import func, or_
from sqlmodel import Session, select

RECEIPT_CLEANUP_BATCH = 100
QUEUE_LAG_THRESHOLD_SECONDS = 120
FAILED_BUILD_ALERT_WINDOW = timedelta(minutes=15)


def expire_read_receipts() -> int:
    """Delete at most one batch, using the same database clock as the guard.

    This runs even when new preparation is disabled. Authoritative metadata
    audit events remain; only the expired response-bearing retry receipt goes.
    """
    with get_session() as managed:
        session = cast(Session, managed)
        database_now = session.exec(select(func.now())).one()
        receipts = session.exec(
            select(SessionPrepReadReceipt)
            .where(SessionPrepReadReceipt.retain_until <= database_now)
            .order_by(cast(Any, SessionPrepReadReceipt.retain_until))
            .limit(RECEIPT_CLEANUP_BATCH)
            .with_for_update(skip_locked=True)
        ).all()
        for receipt in receipts:
            session.delete(receipt)
        session.commit()
        return len(receipts)


def runtime_health() -> dict[str, int | str]:
    """Return queue counts and lag, without record, provider, or source values."""
    with get_session() as managed:
        session = cast(Session, managed)
        counts = {"pending": 0, "running": 0, "ready": 0, "failed": 0}
        for state, count in session.exec(
            select(SessionPrepBuild.state, func.count()).group_by(
                SessionPrepBuild.state
            )
        ).all():
            if state in counts:
                counts[state] = count
        oldest = session.exec(
            select(func.min(SessionPrepBuild.requested_at)).where(
                cast(Any, SessionPrepBuild.state).in_(["pending", "running"])
            )
        ).one()
        lag = max(0, int((datetime.now(UTC) - oldest).total_seconds())) if oldest else 0
        actionable_failed = session.exec(
            select(func.count())
            .select_from(SessionPrepBuild)
            .where(
                SessionPrepBuild.state == "failed",
                SessionPrepBuild.retry_at
                >= datetime.now(UTC) - FAILED_BUILD_ALERT_WINDOW,
                or_(
                    cast(Any, SessionPrepBuild.failure_code).is_(None),
                    cast(Any, SessionPrepBuild.failure_code).not_in(
                        ["scope_unavailable", "no_eligible_sources"]
                    ),
                ),
            )
        ).one()
        return {
            **counts,
            "actionable_failed": actionable_failed,
            "oldest_pending_seconds": lag,
            "status": "degraded"
            if actionable_failed or lag > QUEUE_LAG_THRESHOLD_SECONDS
            else "ok",
        }
