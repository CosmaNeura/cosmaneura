"""Clinician action binding Session Prep eligibility to the displayed revision."""

from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import APIRouter, HTTPException, Path, Request, Response
from pydantic import BaseModel, Field

import app.configuration.session_prep as configuration
from app.routes.dependencies.view_as import deny_view_as_browser_activity
from app.services.session_prep_note_sources import (
    NoteSourceChanged,
    NoteSourceDenied,
    finalize_note_for_session_prep,
    note_content_sha256,
)

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.ai_visit_note import AIVisitNote
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.session_prep_note_finalization import SessionPrepNoteFinalization
from sqlmodel import Session, select

session_prep_review_api = APIRouter()


class SessionPrepNoteReview(BaseModel, extra="forbid"):
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewed_at: datetime | None = None


class ReviewNoteForSessionPrepRequest(BaseModel, extra="forbid"):
    expected_content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def note_review_statuses(
    doctor: Doctor, notes: list[AIVisitNote]
) -> dict[str, SessionPrepNoteReview] | None:
    """Return digests of exactly the content included in this note-detail read."""
    if (
        doctor.deleted_at is not None
        or not configuration.SESSION_PREP_PROVIDER_SETTINGS.allows(doctor.clerk_id)
    ):
        return None
    eligible: dict[str, SessionPrepNoteReview] = {}
    for note in notes:
        if note.doctor_id != doctor.id or note.superseded_at is not None:
            continue
        try:
            eligible[str(note.id)] = SessionPrepNoteReview(
                content_sha256=note_content_sha256(note.note)
            )
        except NoteSourceDenied:
            continue
    if not eligible:
        return eligible
    with get_session() as session:
        rows = session.exec(
            select(SessionPrepNoteFinalization)
            .join(
                Appointment,
                Appointment.id == SessionPrepNoteFinalization.appointment_id,
            )
            .join(Patient, Patient.id == Appointment.patient_id)
            .where(
                SessionPrepNoteFinalization.doctor_id == doctor.id,
                SessionPrepNoteFinalization.patient_id == Patient.id,
                Appointment.doctor_id == doctor.id,
                Patient.doctor_id == doctor.id,
                SessionPrepNoteFinalization.note_id.in_([note.id for note in notes]),  # type: ignore
            )
        ).all()
        for row in rows:
            status = eligible.get(str(row.note_id))
            if status is not None and status.content_sha256 == row.content_sha256:
                status.reviewed_at = row.finalized_at
    return eligible


@session_prep_review_api.post("/notes/revisions/{note_id}/session-prep-review")
def review_note_for_session_prep(
    request: Request,
    response: Response,
    note_id: Annotated[TypeIDType, Path()],
    body: ReviewNoteForSessionPrepRequest,
) -> SessionPrepNoteReview:
    # Included under the internal router's verified actor, current practice
    # access, view-as, and request-binding dependencies.
    deny_view_as_browser_activity(request)
    doctor: Doctor = request.state.doctor
    if not configuration.SESSION_PREP_PROVIDER_SETTINGS.allows(doctor.clerk_id):
        raise HTTPException(status_code=404, detail="Session Prep is unavailable.")
    response.headers["Cache-Control"] = "no-store"
    try:
        with get_session() as managed:
            session = cast(Session, managed)
            reviewed = finalize_note_for_session_prep(
                session=session,
                doctor_id=doctor.id,
                note_id=note_id,
                expected_content_sha256=body.expected_content_sha256,
                observed_at=datetime.now(UTC),
            )
            result = SessionPrepNoteReview(
                content_sha256=reviewed.content_sha256,
                reviewed_at=reviewed.finalized_at,
            )
            session.commit()
            return result
    except NoteSourceChanged:
        raise HTTPException(
            status_code=409, detail="The note changed. Reload and review it again."
        ) from None
    except NoteSourceDenied:
        raise HTTPException(status_code=404, detail="Note is unavailable.") from None
