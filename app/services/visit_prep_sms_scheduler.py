"""Bounded, resumable queue planning; no provider call or fabricated permission.

Core stores only an operational scan position. Per-preparation permission,
current lineage, STOP and central timing are freshly checked under existing
locks. No sender-source or patient-consent verifier is installed by default.
"""

from datetime import timedelta
from time import monotonic
from uuid import uuid4

from typeid import TypeID

from app.configuration import visit_prep
from app.services.companion_records import check_deadline, transaction_budget
from app.services.visit_prep_delivery_links import DeliveryLinkIntent
from app.services.visit_prep_linked_sms import enqueue_linked_sms
from app.services.visit_prep_packages import _database_now
from app.services.visit_prep_sms_outbox import (
    current_sms_timing,
    resolve_locked_sms_authority,
)
from app.services.visit_prep_sms_policy import (
    CoreSMSApprovalVerifier,
    read_locked_sms_policy_approval,
)
from app.services.visit_preps import _lock_current_lineage

from activemodel.session_manager import get_engine
from app.models.doctor import Doctor
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_sms_scan import VisitPrepSMSScan
from sqlalchemy import or_, text
from sqlmodel import Session, col, select

SCAN_NAME = "visit_prep_sms_v1"
BATCH_SIZE = 10
COUNT_FIELDS = ("scanned", "queued", "waiting", "omitted", "unavailable")


def _enabled(settings):
    return type(settings) is visit_prep.VisitPrepSettings and all(
        value is True
        for value in (
            settings.configuration_valid,
            settings.enabled,
            settings.public_api_enabled,
            settings.sms_send_enabled,
            settings.sms_schedule_enabled,
        )
    )


def _page(session, checkpoint, settings):
    query = select(col(VisitPrep.id)).where(
        col(VisitPrep.has_sms_contact).is_(True),
        col(VisitPrep.completed_at).is_(None),
        col(VisitPrep.revoked_at).is_(None),
        col(VisitPrep.misdirected_at).is_(None),
        or_(
            col(VisitPrep.owner_doctor_id).in_(
                [TypeID.from_string(value) for value in settings.enabled_doctor_ids]
            ),
            col(VisitPrep.practice_id).in_(
                [TypeID.from_string(value) for value in settings.enabled_practice_ids]
            ),
        ),
    )
    if checkpoint.after_id is not None:
        query = query.where(col(VisitPrep.id) > checkpoint.after_id)
    return session.exec(query.order_by(col(VisitPrep.id)).limit(BATCH_SIZE)).all()


def _plan(
    session, prep_id, settings, sender_source_verifier, consent_verifier, patient_origin
):
    # Unlocked scope reads are routing hints only; the actual reader locks and
    # validates this complete owner/preparation binding before granting anything.
    hint = session.exec(
        select(
            col(VisitPrep.owner_practice_member_id),
            col(VisitPrep.owner_doctor_id),
            col(VisitPrep.practice_id),
            col(Doctor.clerk_id),
        )
        .join(Doctor, col(Doctor.id) == col(VisitPrep.owner_doctor_id))
        .where(col(VisitPrep.id) == prep_id)
    ).one_or_none()
    if hint is None:
        raise ValueError("unavailable")
    scope = dict(
        owner_practice_member_id=hint[0],
        owner_doctor_id=hint[1],
        practice_id=hint[2],
        expected_clerk_subject=hint[3],
        settings=settings,
    )
    approval = read_locked_sms_policy_approval(session, **scope)
    verifier = CoreSMSApprovalVerifier(settings, sender_source_verifier)
    locked = resolve_locked_sms_authority(
        session,
        approval=approval,
        visit_prep_id=prep_id,
        settings=settings,
        approval_verifier=verifier,
        consent_verifier=consent_verifier,
    )
    waiting = False
    for step in ("booking_sms", "unopened_48h_sms", "unfinished_12h_sms"):
        action, _, _, _ = current_sms_timing(session, locked, settings, step)
        if action == "wait":
            waiting = True
        elif action == "candidate":
            schedule, credential = _lock_current_lineage(
                session, visit_prep=locked.prep
            )
            now = _database_now(session)
            if locked.consent.expires_at is None:
                raise ValueError("unavailable")
            expiry = min(
                now + timedelta(hours=24),
                schedule.scheduled_at,
                credential.expires_at,
                locked.consent.expires_at,
                approval.expires_at,
            )
            result = enqueue_linked_sms(
                _session=session,
                intent=DeliveryLinkIntent(prep_id, credential.id, str(uuid4()), expiry),
                step=step,
                patient_origin=patient_origin,
                approval=approval,
                settings=settings,
                approval_verifier=verifier,
                consent_verifier=consent_verifier,
            )
            return (
                "queued"
                if result.disposition == "queued"
                else "waiting"
                if result.disposition == "wait"
                else "omitted"
            )
    return "waiting" if waiting else "omitted"


def schedule_sms_batch(
    *, patient_origin, sender_source_verifier=None, consent_verifier=None
):
    """Plan at most ten preparations; return only committed aggregate metadata.

    Uses current runtime settings, not caller-supplied flags or identities. The
    scan cursor cannot confer permission. Savepoints isolate unavailable roots;
    outer failure rolls back all queue/link/cursor changes. Retried scans consult
    actual per-step outbox uniqueness and never reconstruct an old bearer.
    """
    settings = visit_prep.VISIT_PREP_SETTINGS
    if (
        not _enabled(settings)
        or not callable(sender_source_verifier)
        or not callable(consent_verifier)
    ):
        return {"status": "disabled"}
    counts = dict.fromkeys(COUNT_FIELDS, 0)
    started = monotonic()
    try:
        # Reuse Core's connection-scoped SQL budget. Every statement consumes
        # the same deadline, including work inside a source verifier/savepoint.
        # Connection acquisition and non-cooperative Python remain subject to
        # the separate Celery execution bound, not this SQL guarantee.
        deadline = started + 25
        with Session(get_engine()) as session:
            with transaction_budget(session, deadline):
                # Serialization is only for scan progress; no patient/source grant.
                if (
                    session.execute(
                        text("SELECT pg_try_advisory_xact_lock(1731915311, 1)")
                    ).scalar_one()
                    is not True
                ):
                    return {"status": "busy"}
                session.execute(
                    text(
                        "INSERT INTO visit_prep_sms_scan(name,version,checked_at) VALUES ('visit_prep_sms_v1',0,clock_timestamp()) ON CONFLICT(name) DO NOTHING"
                    )
                )
                checkpoint = session.exec(
                    select(VisitPrepSMSScan)
                    .where(VisitPrepSMSScan.name == SCAN_NAME)
                    .with_for_update()
                ).one()
                page = _page(session, checkpoint, settings)
            reached_end = len(page) < BATCH_SIZE
            last = checkpoint.after_id
            for prep_id in page:
                if monotonic() - started >= 20:
                    reached_end = False
                    break
                if visit_prep.VISIT_PREP_SETTINGS != settings or not _enabled(
                    visit_prep.VISIT_PREP_SETTINGS
                ):
                    raise ValueError("unavailable")
                outcome = "unavailable"
                try:
                    # Remove the statement listener BEFORE rolling back a
                    # failed savepoint. SET LOCAL inside an aborted PostgreSQL
                    # transaction would otherwise prevent ROLLBACK TO itself.
                    with (
                        session.begin_nested(),
                        transaction_budget(session, min(deadline, monotonic() + 3)),
                    ):
                        outcome = _plan(
                            session,
                            prep_id,
                            settings,
                            sender_source_verifier,
                            consent_verifier,
                            patient_origin,
                        )
                except Exception:
                    # A savepoint exit can fail after _plan returned. Never
                    # count its rolled-back queue as committed work.
                    outcome = "unavailable"
                counts["scanned"] += 1
                counts[outcome] += 1
                last = prep_id.uuid
            with transaction_budget(session, deadline):
                checkpoint.after_id = None if reached_end else last
                checkpoint.version += 1
                checkpoint.checked_at = _database_now(session)
                session.add(checkpoint)
                session.flush()
                if visit_prep.VISIT_PREP_SETTINGS != settings or not _enabled(
                    visit_prep.VISIT_PREP_SETTINGS
                ):
                    raise ValueError("unavailable")
                check_deadline(deadline)
                session.commit()
        return {"status": "planned", **counts}
    except Exception:
        return {"status": "storage_unavailable"}
