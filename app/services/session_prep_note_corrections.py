"""Retained clinician corrections of owned, web-uploaded source notes.

Capture appointments keep their existing Scribe editor. A correction publishes
a new current note and never carries the earlier version's Session Prep review.
The caller owns the transaction; no model, recording or template is changed.
"""

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import Any, cast

from app.services.session_prep_note_sources import (
    NoteSourceChanged,
    NoteSourceDenied,
    canonical_note_content,
    note_content_sha256,
)
from app.services.session_prep_provider_access import (
    resolve_session_prep_provider_access,
)

from activemodel.types import TypeIDType
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.patient import Patient
from app.models.practice import PracticeMember, lock_practice_seats
from app.models.scribe_capture_binding import ScribeCaptureBinding
from app.models.visit_audio_file import VisitAudioFile
from sqlalchemy import update
from sqlmodel import Session, col, select


def correct_source_note(
    *,
    session: Session,
    doctor: Doctor,
    note_id: TypeIDType,
    expected_content_sha256: str,
    fields: dict[str, str],
) -> AIVisitNote:
    """Compare and append under the existing provider and appointment locks."""
    canonical_note_content(fields)
    if any(len(heading) > 200 for heading in fields):
        raise NoteSourceDenied("Note cannot be corrected")
    decision = resolve_session_prep_provider_access(doctor, session=session)
    if not decision.allowed or decision.practice is None:
        raise NoteSourceDenied("Note is unavailable")
    # Membership mutations use this same Practice -> Doctor acquisition order.
    practice = lock_practice_seats(session, decision.practice.id)
    current_doctor = session.exec(
        select(Doctor)
        .where(Doctor.id == doctor.id, Doctor.clerk_id == doctor.clerk_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    ).one_or_none()
    member = session.exec(
        select(PracticeMember)
        .where(PracticeMember.doctor_id == doctor.id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if (
        current_doctor is None
        or practice is None
        or member is None
        or member.practice_id != practice.id
        or not resolve_session_prep_provider_access(
            current_doctor, session=session
        ).allowed
    ):
        raise NoteSourceDenied("Note is unavailable")
    candidate = session.get(AIVisitNote, note_id)
    if candidate is None or candidate.doctor_id != doctor.id:
        raise NoteSourceDenied("Note is unavailable")
    appointment = session.exec(
        select(Appointment)
        .where(Appointment.id == candidate.appointment_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if appointment is None or appointment.doctor_id != doctor.id:
        raise NoteSourceDenied("Note is unavailable")
    # Capture binding creation uses this appointment lock too. Even a revoked
    # or foreign/inconsistent retained binding closes this web editing path.
    if (
        session.exec(
            select(col(ScribeCaptureBinding.id))
            .where(ScribeCaptureBinding.appointment_id == appointment.id)
            .limit(1)
        ).first()
        is not None
    ):
        raise NoteSourceDenied("Note is unavailable")
    patient = session.exec(
        select(Patient)
        .where(Patient.id == appointment.patient_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    ).one_or_none()
    upload = session.exec(
        select(VisitAudioFile)
        .where(
            VisitAudioFile.appointment_id == appointment.id,
            VisitAudioFile.doctor_id == doctor.id,
            VisitAudioFile.patient_id == appointment.patient_id,
        )
        .with_for_update(read=True)
        .limit(1)
    ).first()
    if patient is None or patient.doctor_id != doctor.id or upload is None:
        raise NoteSourceDenied("Note is unavailable")
    note = session.exec(
        select(AIVisitNote)
        .where(AIVisitNote.id == note_id)
        .with_for_update(of=AIVisitNote)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if note is None or note.doctor_id != doctor.id:
        raise NoteSourceDenied("Note is unavailable")
    if note.superseded_at is not None or not hmac.compare_digest(
        note_content_sha256(note.note), expected_content_sha256
    ):
        raise NoteSourceChanged("The note changed")
    transcript = session.exec(
        select(AIVisitTranscript)
        .where(AIVisitTranscript.id == note.ai_transcript_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    ).one_or_none()
    schema = session.exec(
        select(DoctorNoteSchema)
        .where(DoctorNoteSchema.id == note.doctor_note_schema_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if (
        note.appointment_id != appointment.id
        or schema is None
        or schema.doctor_id != doctor.id
        or schema.deleted_at is not None
        or transcript is None
        or transcript.deleted_at is not None
        or transcript.doctor_id != doctor.id
        or transcript.patient_id != patient.id
        or transcript.appointment_id != appointment.id
    ):
        raise NoteSourceDenied("Note is unavailable")
    if canonical_note_content(note.note) == canonical_note_content(fields):
        return note
    # Release the partial unique current slot before inserting its replacement.
    # Both become visible together at commit. Retain the earlier body/review.
    session.execute(
        update(cast(Any, AIVisitNote))
        .where(cast(Any, AIVisitNote.id) == note.id)
        .values(
            superseded_at=datetime.now(UTC),
            supersession_reason="clinician_web_source_correction",
            updated_at=note.updated_at,
        )
    )
    session.flush()
    replacement = AIVisitNote(
        doctor_id=note.doctor_id,
        appointment_id=note.appointment_id,
        ai_transcript_id=note.ai_transcript_id,
        doctor_note_schema_id=note.doctor_note_schema_id,
        restore_frontier_transcript_id=note.restore_frontier_transcript_id,
        note=dict(fields),
    )
    session.add(replacement)
    session.flush()
    return replacement
