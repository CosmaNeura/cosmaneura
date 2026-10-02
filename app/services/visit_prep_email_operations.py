"""Read-only, value-free email recovery facts; not delivery authorization.

No job, endpoint or scheduler is installed here. This internal maintenance read
remains useful with release gates off or a practice's authority withdrawn. It
never opens an encrypted envelope, claims work, retries a send or infers delivery.
Queue age is advisory: without resolved cadence policy it cannot prove lateness.
"""

from datetime import timedelta
from typing import cast

from activemodel.session_manager import global_session
from app.models.visit_prep import (
    VisitPrep,
    VisitPrepCredentialGeneration,
    VisitPrepScheduleGeneration,
)
from app.models.visit_prep_delivery import VisitPrepEmailDelivery
from app.models.visit_prep_email_authority import (
    VisitPrepEmailApprovalEvent,
    VisitPrepEmailConsentEvent,
)
from sqlalchemy import Table, and_, func, or_, select, true

_STATES = ("queued", "submitting", "accepted", "failed", "suppressed", "uncertain")
# Telemetry window only, not a send/retry deadline or an activation threshold.
_RECENT_FAILURE_WINDOW = timedelta(minutes=15)


def runtime_health() -> dict[str, int | str]:
    """Sample aggregate metadata in one statement, without patient/owner IDs.

    Counts are a point-in-time database snapshot, not proof a consumer is alive.
    Expired and obsolete queued counts may overlap. Overdue submissions are
    potentially sent, not failed or available for retry. Historical failures
    remain counted but only recent failures affect this recovery status.
    Uncertain terminal outcomes remain attention-worthy until a separately
    qualified reconciliation path can record actual evidence.
    """
    try:
        # Refuse an ambient global session instead of observing its uncommitted
        # writes, flushing it, or changing the caller's transaction lifecycle.
        with global_session() as session:
            delivery = cast(Table, getattr(VisitPrepEmailDelivery, "__table__"))
            prep = cast(Table, getattr(VisitPrep, "__table__"))
            credential = cast(
                Table, getattr(VisitPrepCredentialGeneration, "__table__")
            )
            schedule = cast(Table, getattr(VisitPrepScheduleGeneration, "__table__"))
            consent = cast(Table, getattr(VisitPrepEmailConsentEvent, "__table__"))
            approval = cast(Table, getattr(VisitPrepEmailApprovalEvent, "__table__"))
            # PostgreSQL materializes one clock value for every field, including
            # an empty queue. A separate clock query would have an older snapshot
            # and could miss an expiry while the aggregate query waits.
            clock = (
                select(func.clock_timestamp().label("sampled_at"))
                .cte("email_health_clock")
                .prefix_with("MATERIALIZED")
            )
            now = clock.c.sampled_at
            queued = delivery.c.state == "queued"
            submitting = delivery.c.state == "submitting"
            latest_credential = (
                select(func.max(credential.c.generation))
                .where(credential.c.visit_prep_id == delivery.c.visit_prep_id)
                .correlate(delivery)
                .scalar_subquery()
            )
            latest_schedule = (
                select(func.max(schedule.c.generation))
                .where(schedule.c.visit_prep_id == delivery.c.visit_prep_id)
                .correlate(delivery)
                .scalar_subquery()
            )
            latest_consent = (
                select(consent.c.event_kind)
                .where(consent.c.visit_prep_id == delivery.c.visit_prep_id)
                .order_by(consent.c.event_sequence.desc())
                .limit(1)
                .correlate(delivery)
                .scalar_subquery()
            )
            latest_approval = (
                select(approval.c.event_kind)
                .where(
                    approval.c.owner_practice_member_id
                    == delivery.c.owner_practice_member_id
                )
                .order_by(approval.c.event_sequence.desc())
                .limit(1)
                .correlate(delivery)
                .scalar_subquery()
            )
            conditions = {
                "expired_queued": and_(queued, delivery.c.expires_at <= now),
                "obsolete_queued": and_(
                    queued,
                    or_(
                        prep.c.revoked_at.is_not(None),
                        prep.c.misdirected_at.is_not(None),
                        prep.c.completed_at.is_not(None),
                        delivery.c.credential_generation != latest_credential,
                        schedule.c.generation != latest_schedule,
                        credential.c.revoked_at.is_not(None),
                        credential.c.expires_at <= now,
                        schedule.c.scheduled_at <= now,
                        latest_consent == "withdrawn",
                        latest_approval == "withdrawn",
                    ),
                ),
                "overdue_submitting": and_(
                    submitting,
                    or_(delivery.c.lease_until <= now, delivery.c.expires_at <= now),
                ),
                "recent_failed": and_(
                    delivery.c.state == "failed",
                    delivery.c.finalized_at >= now - _RECENT_FAILURE_WINDOW,
                ),
            }
            sample = (
                session.execute(
                    select(
                        now,
                        *(
                            func.count(delivery.c.id)
                            .filter(delivery.c.state == state)
                            .label(state)
                            for state in _STATES
                        ),
                        *(
                            func.count(delivery.c.id).filter(condition).label(name)
                            for name, condition in conditions.items()
                        ),
                        func.min(delivery.c.requested_at)
                        .filter(queued)
                        .label("oldest_queued_at"),
                    )
                    .select_from(clock)
                    .outerjoin(delivery, true())
                    .outerjoin(prep, prep.c.id == delivery.c.visit_prep_id)
                    .outerjoin(
                        credential,
                        and_(
                            credential.c.visit_prep_id == delivery.c.visit_prep_id,
                            credential.c.generation == delivery.c.credential_generation,
                        ),
                    )
                    .outerjoin(
                        schedule, schedule.c.id == delivery.c.schedule_generation_id
                    )
                    .group_by(now)
                )
                .one()
                ._mapping
            )
            sampled_at, oldest = sample["sampled_at"], sample["oldest_queued_at"]
            if sampled_at.tzinfo is None or sampled_at.utcoffset() is None:
                raise ValueError("email health clock is unavailable")
            counts = {name: int(sample[name]) for name in (*_STATES, *conditions)}
            return {
                **counts,
                "oldest_queued_seconds": max(
                    0, int((sampled_at - oldest).total_seconds())
                )
                if oldest is not None
                else 0,
                "queue_timing": "unqualified",
                "status": "degraded"
                if any(counts[name] for name in (*conditions, "uncertain"))
                else "ok",
            }
    except Exception:
        # No exception detail, SQL parameters, source identities or invented
        # zero counts. Storage failure is not an empty/healthy queue.
        return {"status": "storage_unavailable"}
