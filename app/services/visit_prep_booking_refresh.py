"""Refresh already-confirmed completed sources after a booking schedule change."""

from app.configuration import visit_prep
from app.services.visit_prep_packages import _database_now, _load_authorized_package
from app.services.visit_prep_synthesis_builds import stage_synthesis_request

from app.models.authority import AuthorityAuditEvent
from app.models.doctor import Doctor
from app.models.visit_prep import VisitPrep
from sqlmodel import Session


def stage_booking_synthesis_refresh(session: Session, *, prep: VisitPrep) -> bool:
    settings = visit_prep.VISIT_PREP_SETTINGS
    if (
        not settings.configuration_valid
        or not settings.enabled
        or not settings.auto_synthesis_enabled
        or prep.lifecycle_state != "completed"
    ):
        return False
    doctor = session.get(Doctor, prep.owner_doctor_id)
    if doctor is None or not doctor.clerk_id:
        raise ValueError("Visit Prep booking is unavailable.")
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
        current.current_answers_complete
        and (current.current_answers_reviewed or current.basis.event_sequence == 0)
    ):
        return False
    receipt = stage_synthesis_request(
        session,
        locked=locked,
        expected_clerk_subject=doctor.clerk_id,
        patient_handoff=True,
    )
    session.add(
        AuthorityAuditEvent(
            event_type="visit_prep.booking_refresh_queued",
            outcome="allowed",
            occurred_at=_database_now(session),
            resource_type="visit_prep",
            resource_ref=str(prep.id),
            request_digest=receipt.source_version,
            route="visit_prep_booking_changed",
            intent="confirmed_sources_schedule_refresh",
            platform_refs=[{"kind": "doctor", "ref": str(prep.owner_doctor_id)}],
        )
    )
    session.flush()
    return True
