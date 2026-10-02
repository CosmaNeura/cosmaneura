"""Atomic, default-off patient submission → existing Core synthesis queue.

Patient authority permits only handing off their completed/confirmed sources.
Core schedules work for the preparation's current canonical owner, not a
fabricated patient/provider sign-in. No AI, delivery or chart write happens here.
"""

from app.services import visit_prep_access
from app.services.visit_prep_interview_contract import VisitPrepInterviewUnavailable
from app.services.visit_prep_interviews import _lock_authorized_interview
from app.services.visit_prep_packages import _database_now, _load_authorized_package
from app.services.visit_prep_patient_continuation import ConfirmedVisitPrepAccess
from app.services.visit_prep_synthesis_builds import stage_synthesis_request
from app.services.visit_preps import VisitPrepConflict, VisitPrepNotFound

from app.models.authority import AuthorityAuditEvent
from app.models.doctor import Doctor
from sqlmodel import Session


def _enabled() -> bool:
    settings = visit_prep_access.VISIT_PREP_SETTINGS
    return (
        settings.configuration_valid
        and settings.enabled
        and settings.auto_synthesis_enabled
    )


def stage_patient_handoff(
    session: Session, *, access_token: str | ConfirmedVisitPrepAccess
) -> None:
    """Caller commits submission/review, queued work and audit together.

    The invitation is reacquired before and after staging. The worker still
    independently rechecks current owner/cohort, sources and Core AI safeguards.
    Reads, unfinished answers and unconfirmed corrections never schedule work.
    """
    if not _enabled():
        return
    authorized = _lock_authorized_interview(session, access_token=access_token)
    prep = authorized.visit_prep
    doctor = session.get(Doctor, prep.owner_doctor_id)
    if doctor is None or not doctor.clerk_id:
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    try:
        # This is canonical owner policy/lineage revalidation, not provider
        # authentication: no subject or ownership selector comes from a patient.
        locked = _load_authorized_package(
            session,
            owner_practice_member_id=prep.owner_practice_member_id,
            owner_doctor_id=prep.owner_doctor_id,
            practice_id=prep.practice_id,
            expected_clerk_subject=doctor.clerk_id,
            visit_prep_id=prep.id,
        )
        current = locked.package.contribution
        if not (
            current.original_interview_completed
            and current.current_answers_complete
            and (current.current_answers_reviewed or current.basis.event_sequence == 0)
        ):
            return
        receipt = stage_synthesis_request(
            session,
            locked=locked,
            expected_clerk_subject=doctor.clerk_id,
            patient_handoff=True,
        )
        session.add(
            AuthorityAuditEvent(
                event_type="visit_prep.patient_handoff_queued",
                outcome="allowed",
                occurred_at=_database_now(session),
                resource_type="visit_prep",
                resource_ref=str(prep.id),
                request_digest=receipt.source_version,
                route="visit_prep_patient_handoff",
                intent="patient_submission_handoff",
                platform_refs=[
                    {"kind": "doctor", "ref": str(prep.owner_doctor_id)},
                    {"kind": "practice", "ref": str(prep.practice_id)},
                    {
                        "kind": "practice_member",
                        "ref": str(prep.owner_practice_member_id),
                    },
                ],
            )
        )
        session.flush()
        _lock_authorized_interview(session, access_token=access_token)
        if not _enabled():
            raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")
    except (VisitPrepNotFound, VisitPrepConflict):
        raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable") from None
