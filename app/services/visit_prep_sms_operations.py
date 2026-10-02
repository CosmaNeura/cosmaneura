"""Content-free SMS recovery metadata; not authority, delivery or a liveness claim.

Reuse Core email health's one-statement clock/count pattern. No private envelope
is selected, no external source is called and no caller's transaction is committed.
"""

from datetime import timedelta
from typing import cast

from activemodel.session_manager import get_engine
from app.models.visit_prep import VisitPrep, VisitPrepCredentialGeneration
from app.models.visit_prep_sms_approval import VisitPrepSMSApprovalEvent
from app.models.visit_prep_sms_consent import VisitPrepSMSConsentEvent
from app.models.visit_prep_sms_delivery import VisitPrepSMSDelivery
from app.models.visit_prep_sms_stop import VisitPrepSMSStopEvent
from sqlalchemy import Table, and_, func, or_, select, true
from sqlmodel import Session

STATES = ("queued", "submitting", "accepted", "failed", "suppressed", "uncertain")
CONDITIONS = (
    "expired_queued",
    "obsolete_queued",
    "overdue_submitting",
    "recent_failed",
)
COUNT_FIELDS = (*STATES, *CONDITIONS, "oldest_queued_seconds")
# Observation window only; never a delivery retry or message timing rule.
_RECENT_FAILURE_WINDOW = timedelta(minutes=15)


def runtime_health() -> dict[str, int | str]:
    """Return only counts and fixed statuses from an owned read transaction.

    Obsolete and expired counts may overlap. Accepted is not delivered. An uncertain
    submission requires separate evidence reconciliation and is never eligible for
    automatic resend. Queue age without qualified timing/source is advisory. A good
    snapshot does not prove the scheduler, provider or cleanup consumer is alive.
    """
    try:
        with Session(get_engine()) as session:
            sms = cast(Table, getattr(VisitPrepSMSDelivery, "__table__"))
            prep = cast(Table, getattr(VisitPrep, "__table__"))
            credential = cast(
                Table, getattr(VisitPrepCredentialGeneration, "__table__")
            )
            consent = cast(Table, getattr(VisitPrepSMSConsentEvent, "__table__"))
            approval = cast(Table, getattr(VisitPrepSMSApprovalEvent, "__table__"))
            stop = cast(Table, getattr(VisitPrepSMSStopEvent, "__table__"))
            clock = (
                select(func.clock_timestamp().label("sampled_at"))
                .cte("sms_health_clock")
                .prefix_with("MATERIALIZED")
            )
            now, queued = clock.c.sampled_at, sms.c.state == "queued"
            latest_credential = (
                select(credential.c.id)
                .where(credential.c.visit_prep_id == sms.c.visit_prep_id)
                .order_by(credential.c.generation.desc())
                .limit(1)
                .correlate(sms)
                .scalar_subquery()
            )
            latest_consent = (
                select(consent.c.id)
                .where(consent.c.visit_prep_id == sms.c.visit_prep_id)
                .order_by(consent.c.event_sequence.desc())
                .limit(1)
                .correlate(sms)
                .scalar_subquery()
            )
            latest_approval = (
                select(approval.c.event_kind)
                .where(
                    approval.c.owner_practice_member_id
                    == sms.c.owner_practice_member_id
                )
                .order_by(approval.c.event_sequence.desc())
                .limit(1)
                .correlate(sms)
                .scalar_subquery()
            )
            stopped = (
                select(stop.c.id)
                .where(
                    stop.c.sender_digest == sms.c.sender_digest,
                    stop.c.recipient_digest == sms.c.recipient_digest,
                )
                .correlate(sms)
                .exists()
            )
            conditions = {
                "expired_queued": and_(queued, sms.c.expires_at <= now),
                "obsolete_queued": and_(
                    queued,
                    or_(
                        prep.c.revoked_at.is_not(None),
                        prep.c.misdirected_at.is_not(None),
                        prep.c.completed_at.is_not(None),
                        sms.c.credential_id != latest_credential,
                        credential.c.revoked_at.is_not(None),
                        credential.c.expires_at <= now,
                        sms.c.consent_event_id != latest_consent,
                        consent.c.event_kind == "withdrawn",
                        consent.c.expires_at <= now,
                        latest_approval == "withdrawn",
                        stopped,
                    ),
                ),
                "overdue_submitting": and_(
                    sms.c.state == "submitting",
                    or_(sms.c.lease_until <= now, sms.c.expires_at <= now),
                ),
                "recent_failed": and_(
                    sms.c.state == "failed",
                    sms.c.finalized_at >= now - _RECENT_FAILURE_WINDOW,
                ),
            }
            sample = (
                session.execute(
                    select(
                        now,
                        *(
                            func.count(sms.c.id)
                            .filter(sms.c.state == state)
                            .label(state)
                            for state in STATES
                        ),
                        *(
                            func.count(sms.c.id).filter(condition).label(name)
                            for name, condition in conditions.items()
                        ),
                        func.min(sms.c.requested_at)
                        .filter(queued)
                        .label("oldest_queued_at"),
                    )
                    .select_from(clock)
                    .outerjoin(sms, true())
                    .outerjoin(prep, prep.c.id == sms.c.visit_prep_id)
                    .outerjoin(credential, credential.c.id == sms.c.credential_id)
                    .outerjoin(consent, consent.c.id == sms.c.consent_event_id)
                    .group_by(now)
                )
                .one()
                ._mapping
            )
            sampled_at, oldest = sample["sampled_at"], sample["oldest_queued_at"]
            if sampled_at.tzinfo is None or sampled_at.utcoffset() is None:
                raise ValueError("unavailable")
            counts = {name: int(sample[name]) for name in (*STATES, *CONDITIONS)}
            return {
                **counts,
                "oldest_queued_seconds": max(
                    0, int((sampled_at - oldest).total_seconds())
                )
                if oldest is not None
                else 0,
                "queue_timing": "unqualified",
                "status": "degraded"
                if any(counts[name] for name in (*CONDITIONS, "uncertain"))
                else "ok",
            }
    except Exception:
        # Storage failure is not an empty queue; never expose exception/SQL values.
        return {"status": "storage_unavailable"}
