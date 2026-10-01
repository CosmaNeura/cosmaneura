"""Content-free recovery signals from the queue and the single usage ledger.

An expired unmatched intent stays uncertain until an actual result is recorded.
No reconciliation writes guessed success/failure or token counts. Retried model
work remains bounded by the queue; immutable usage outlives mutable job cleanup.
"""

from typing import Any, cast

from app.services.visit_prep_packages import _database_now
from app.services.visit_prep_synthesis_builds import _profile_query
from app.services.visit_preps import _visit_prep_session

from app.models.authority_usage import AgentModelUsageEvent
from sqlalchemy import func
from sqlalchemy.orm import aliased
from sqlmodel import select


def runtime_health() -> dict[str, int | str]:
    """Read aggregate recovery facts; never disclose owner or preparation IDs."""
    with _visit_prep_session() as session:
        now = _database_now(session)
        builds = _profile_query().subquery()
        counts = {"pending": 0, "running": 0, "ready": 0, "failed": 0}
        for state, count in session.exec(
            select(builds.c.state, func.count()).group_by(builds.c.state)
        ).all():
            if state in counts:
                counts[state] = count
        oldest = session.exec(
            select(func.min(builds.c.requested_at)).where(
                builds.c.state.in_(["pending", "running"])
            )
        ).one()
        lag = max(0, int((now - oldest).total_seconds())) if oldest else 0
        result = aliased(AgentModelUsageEvent)
        unmatched = session.exec(
            select(func.count())
            .select_from(AgentModelUsageEvent)
            .outerjoin(
                result, cast(Any, result.call_intent_id) == AgentModelUsageEvent.id
            )
            .where(
                AgentModelUsageEvent.event_origin == "visit_prep_batch",
                AgentModelUsageEvent.batch_phase == "intent",
                cast(Any, AgentModelUsageEvent.build_lease_until) <= now,
                cast(Any, result.id).is_(None),
            )
        ).one()
        return {
            **counts,
            "oldest_pending_seconds": lag,
            "uncertain_usage_attempts": unmatched,
            "status": "degraded"
            if counts["failed"] or lag > 120 or unmatched
            else "ok",
        }
