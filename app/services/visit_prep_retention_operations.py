"""Value-free retention observations, never purge or incident authority.

This internal read is not installed in a job, route or monitor. It opens no
encrypted records and remains usable with release gates off or BAA withdrawn.
Stored deadlines are evidence to review, not permission to erase clinical or
consent records. Legal holds do not conceal overdue cleanup observations.
"""

from typing import cast

from activemodel.session_manager import global_session
from app.models.visit_prep import (
    VisitPrep,
    VisitPrepCredentialGeneration,
    VisitPrepLegalHold,
    VisitPrepScheduleGeneration,
)
from sqlalchemy import Table, and_, func, or_, select, true


def runtime_health() -> dict[str, int | str]:
    """Read one committed metadata snapshot without identities or mutations.

    Counts overlap intentionally: an overdue unused preparation can be expired,
    held and beyond the hard ceiling. Confirmation alone establishes clinical
    retention, including all-skipped completion. Quarantine uses only its
    separate incident disposition, never the ordinary clinical deadline.
    Rescheduling uses the greatest schedule generation; credential rotation
    uses the greatest credential generation, not a historical revoked token.
    Nothing here qualifies aggregate/consent cleanup or proves a monitor alive.
    """
    try:
        # Refuse an ambient session before observing or flushing caller writes.
        with global_session() as session:
            prep = cast(Table, getattr(VisitPrep, "__table__"))
            schedule = cast(Table, getattr(VisitPrepScheduleGeneration, "__table__"))
            credential = cast(
                Table, getattr(VisitPrepCredentialGeneration, "__table__")
            )
            hold = cast(Table, getattr(VisitPrepLegalHold, "__table__"))
            clock = (
                select(func.clock_timestamp().label("sampled_at"))
                .cte("retention_health_clock")
                .prefix_with("MATERIALIZED")
            )
            now = clock.c.sampled_at
            latest_schedule = (
                select(
                    schedule.c.visit_prep_id,
                    func.max(schedule.c.generation).label("generation"),
                )
                .group_by(schedule.c.visit_prep_id)
                .subquery("latest_retention_schedule")
            )
            latest_credential = (
                select(
                    credential.c.visit_prep_id,
                    func.max(credential.c.generation).label("generation"),
                )
                .group_by(credential.c.visit_prep_id)
                .subquery("latest_retention_credential")
            )
            held = (
                select(hold.c.id)
                .where(
                    hold.c.visit_prep_id == prep.c.id,
                    hold.c.released_at.is_(None),
                )
                .correlate(prep)
                .exists()
            )
            unused = and_(
                prep.c.lifecycle_state == "invited",
                prep.c.confirmed_at.is_(None),
                prep.c.started_at.is_(None),
                prep.c.completed_at.is_(None),
                prep.c.misdirected_at.is_(None),
            )
            cleanup_due = and_(
                unused,
                or_(
                    schedule.c.unused_purge_at <= now,
                    and_(
                        prep.c.revoked_at.is_not(None),
                        prep.c.revocation_reason.in_(
                            ("wrong_recipient", "appointment_cancelled")
                        ),
                    ),
                ),
            )
            clinical = and_(
                prep.c.confirmed_at.is_not(None), prep.c.misdirected_at.is_(None)
            )
            conditions = {
                "unused": unused,
                "unused_expired_unrevoked": and_(
                    unused,
                    prep.c.revoked_at.is_(None),
                    credential.c.revoked_at.is_(None),
                    credential.c.expires_at <= now,
                ),
                "unused_cleanup_due": cleanup_due,
                "unused_hard_ceiling_reached": and_(
                    unused, schedule.c.unused_hard_purge_at <= now
                ),
                "unused_cleanup_held": and_(cleanup_due, held),
                "confirmed_retained": and_(clinical, prep.c.retain_until > now),
                "confirmed_retention_elapsed": and_(
                    clinical, prep.c.retain_until <= now
                ),
                "quarantined_pending_review": and_(
                    prep.c.misdirected_at.is_not(None),
                    prep.c.misdirected_disposition == "pending_review",
                ),
                "quarantined_disposition_elapsed": and_(
                    prep.c.misdirected_at.is_not(None),
                    prep.c.misdirected_disposition.in_(
                        ("retain_until", "purge_approved")
                    ),
                    prep.c.misdirected_disposition_until <= now,
                ),
                "active_legal_holds": held,
                "incomplete_current_lineage": or_(
                    schedule.c.id.is_(None),
                    credential.c.id.is_(None),
                    credential.c.schedule_generation_id != schedule.c.id,
                ),
            }
            sample = (
                session.execute(
                    select(
                        now,
                        func.count(prep.c.id).label("preparations"),
                        *(
                            func.count(prep.c.id).filter(condition).label(name)
                            for name, condition in conditions.items()
                        ),
                    )
                    .select_from(clock)
                    .outerjoin(prep, true())
                    .outerjoin(
                        latest_schedule,
                        latest_schedule.c.visit_prep_id == prep.c.id,
                    )
                    .outerjoin(
                        schedule,
                        and_(
                            schedule.c.visit_prep_id == prep.c.id,
                            schedule.c.generation == latest_schedule.c.generation,
                        ),
                    )
                    .outerjoin(
                        latest_credential,
                        latest_credential.c.visit_prep_id == prep.c.id,
                    )
                    .outerjoin(
                        credential,
                        and_(
                            credential.c.visit_prep_id == prep.c.id,
                            credential.c.generation == latest_credential.c.generation,
                        ),
                    )
                    .group_by(now)
                )
                .one()
                ._mapping
            )
            at = sample["sampled_at"]
            if at.tzinfo is None or at.utcoffset() is None:
                raise ValueError("retention health clock is unavailable")
            counts = {name: int(sample[name]) for name in ("preparations", *conditions)}
            return {
                **counts,
                "retention_qualification": "not_qualified",
                "status": "degraded"
                if any(
                    counts[name]
                    for name in (
                        "unused_expired_unrevoked",
                        "unused_cleanup_due",
                        "unused_hard_ceiling_reached",
                        "confirmed_retention_elapsed",
                        "quarantined_pending_review",
                        "quarantined_disposition_elapsed",
                        "incomplete_current_lineage",
                    )
                )
                else "ok",
            }
    except Exception:
        # Storage failure is neither a healthy empty result nor permission to
        # emit SQL parameters, incident refs, contacts or retained content.
        return {"status": "storage_unavailable"}
