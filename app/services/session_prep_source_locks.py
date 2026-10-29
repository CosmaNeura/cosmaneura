"""Shared bounded Appointment -> Note fences for Session Prep publication.

Callers keep these NOWAIT locks through commit. Callers own savepoint/rollback
and retry handling; this helper performs no model or network operation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from app.services.session_prep_note_sources import (
    MAX_REVIEW_CANDIDATES,
    SOURCE_WINDOW,
    _prior_patient_scope,
    _utc,
)

from app.models.ai_visit_note import AIVisitNote
from app.models.appointment import Appointment
from app.models.patient import Patient
from app.models.provider_record_authority import ProviderRecordAuthority
from app.models.scribe_patient_identity import ScribePatientIdentity
from app.models.session_prep_note_finalization import SessionPrepNoteFinalization
from sqlmodel import Session, col, select


class NoteSourceScopeChanged(ValueError):
    """A fixed, content-free source fence refusal."""


def lock_note_source_versions(
    session: Session,
    scope: ProviderRecordAuthority,
    note_refs: list[str],
    observed_at: datetime,
) -> None:
    """Reuse the factual final-answer fence for brief reads/worker publication."""
    notes = (
        session.exec(
            select(AIVisitNote).where(
                AIVisitNote.id.in_(note_refs)  # type: ignore[attr-defined]
            )
        ).all()
        if note_refs
        else []
    )
    if len(notes) != len(note_refs):
        raise NoteSourceScopeChanged
    # Restore can make an unselected, superseded but reviewed note eligible.
    # Fence every bounded prior appointment with review evidence, not only the
    # sources that happened to be included in this artifact.
    appointment_ids = _reviewed_appointment_ids(session, scope, observed_at) | {
        scope.appointment_id,
        *(note.appointment_id for note in notes),
    }
    appointments = session.exec(
        select(Appointment)
        .where(
            Appointment.id.in_(appointment_ids)  # type: ignore[attr-defined]
        )
        .order_by(cast(Any, Appointment.id))
        .with_for_update(of=Appointment, nowait=True)
        .execution_options(populate_existing=True)
    ).all()
    if len(appointments) != len(appointment_ids) or any(
        a.doctor_id != scope.doctor_id for a in appointments
    ):
        raise NoteSourceScopeChanged
    # Capture retains original patient IDs after an explicit provider match.
    # Fence the association rows as well as the original appointments before
    # reapplying the exact reviewed-source canonical-patient predicate. NOWAIT
    # avoids waiting against Scribe's identity -> Patient writer lock order.
    session.exec(
        select(ScribePatientIdentity)
        .where(col(ScribePatientIdentity.appointment_id).in_(appointment_ids))
        .order_by(col(ScribePatientIdentity.appointment_id))
        .with_for_update(of=ScribePatientIdentity, nowait=True)
        .execution_options(populate_existing=True)
    ).all()
    target = next((a for a in appointments if a.id == scope.appointment_id), None)
    if target is None or target.patient_id != scope.patient_id:
        raise NoteSourceScopeChanged
    eligible_ids = _reviewed_appointment_ids(session, scope, observed_at)
    if (
        not eligible_ids <= appointment_ids
        or not (appointment_ids - {scope.appointment_id}) <= eligible_ids
    ):
        raise NoteSourceScopeChanged
    if note_refs:
        locked = session.exec(
            select(AIVisitNote)
            .where(
                AIVisitNote.id.in_(note_refs)  # type: ignore[attr-defined]
            )
            .order_by(cast(Any, AIVisitNote.id))
            .with_for_update(of=AIVisitNote, nowait=True)
            .execution_options(populate_existing=True)
        ).all()
        if len(locked) != len(note_refs) or any(
            note.doctor_id != scope.doctor_id
            or note.appointment_id not in appointment_ids
            for note in locked
        ):
            raise NoteSourceScopeChanged


def _reviewed_appointment_ids(
    session: Session, scope: ProviderRecordAuthority, observed_at: datetime
) -> set[Any]:
    target = session.get(Appointment, scope.appointment_id, populate_existing=True)
    if target is None:
        raise NoteSourceScopeChanged
    cutoff = min(_utc(target.appointment_date), observed_at).replace(tzinfo=None)
    ids = session.exec(
        select(cast(Any, Appointment.id))
        .join(
            SessionPrepNoteFinalization,
            cast(Any, SessionPrepNoteFinalization.appointment_id) == Appointment.id,
        )
        .join(Patient, col(Patient.id) == col(Appointment.patient_id))
        .where(
            Appointment.doctor_id == scope.doctor_id,
            Patient.doctor_id == scope.doctor_id,
            _prior_patient_scope(session, scope.doctor_id, target),
            Appointment.id != scope.appointment_id,
            Appointment.appointment_date >= cutoff - SOURCE_WINDOW,
            Appointment.appointment_date < cutoff,
            SessionPrepNoteFinalization.doctor_id == scope.doctor_id,
            SessionPrepNoteFinalization.patient_id == Appointment.patient_id,
            SessionPrepNoteFinalization.finalized_at <= observed_at,
        )
        .distinct()
        .order_by(cast(Any, Appointment.id))
        .limit(MAX_REVIEW_CANDIDATES + 1)
    ).all()
    if len(ids) > MAX_REVIEW_CANDIDATES:
        raise NoteSourceScopeChanged
    return set(ids)
