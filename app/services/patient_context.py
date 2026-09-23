"""
Shared assembly of a patient's session history for LLM prompt building.

This is the single place that answers "what do we know about this patient
from prior sessions with this doctor?". Intake question generation, intake
summaries, and session prep all build prompts from this data, and the
patient-facing (Sovereign) chatbot is expected to consume it once it
integrates with Cosmas. Do not re-implement these queries inside job files.

Everything returned here is PHI: log IDs, never content.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from pydantic import BaseModel as PydanticBaseModel

from activemodel.types import TypeIDType
from app.models.ai_visit_note import AIVisitNote
from app.models.appointment import Appointment
from app.models.cps_self_report import CpsSelfReport
from app.models.engagement_role import EngagementRole
from app.models.intake.intake import Intake
from app.models.patient import Patient

NOTE_LOOKBACK = timedelta(days=548)
"""
~18 months. Older notes are excluded from prompt context — they are more
likely to mislead the model than help it. Product decision 2026-07-22.
"""


def days_since(past_dt: datetime) -> int:
    past_date = past_dt.date()
    today = datetime.today().date()
    return (today - past_date).days


def recent_notes_for_patient(
    doctor_id: TypeIDType, patient_id: TypeIDType, *, limit: int,
    before: datetime | None = None,
    exclude_appointment_id: TypeIDType | None = None,
) -> list[AIVisitNote]:
    "clinical notes for a patient, most recent appointment first, capped at NOTE_LOOKBACK"

    cutoff = datetime.now(UTC).replace(tzinfo=None) - NOTE_LOOKBACK

    query = (
        AIVisitNote.select()
        .join(Appointment, AIVisitNote.appointment_id == Appointment.id)
        .join(Patient, Appointment.patient_id == Patient.id)
        .where(
            (AIVisitNote.doctor_id == doctor_id)
            & (Patient.id == patient_id)
            & (Appointment.appointment_date >= cutoff)
            & (cast(Any, AIVisitNote.superseded_at).is_(None))
        )
        # the visit that happened last, not the row that was written last
        .order_by(
            cast(Any, Appointment.appointment_date).desc(),
            cast(Any, AIVisitNote.id).desc(),
        )
        .limit(limit)
    )

    if before is not None:
        if before.tzinfo is not None:
            before = before.astimezone(UTC).replace(tzinfo=None)
        query = query.where(Appointment.appointment_date < before)
    if exclude_appointment_id is not None:
        query = query.where(Appointment.id != exclude_appointment_id)
    return cast(list[AIVisitNote], list(query.all()))


def latest_note_for_patient(
    doctor_id: TypeIDType, patient_id: TypeIDType
) -> AIVisitNote | None:
    notes = recent_notes_for_patient(doctor_id, patient_id, limit=1)
    return notes[0] if notes else None


class NoteContext(PydanticBaseModel):
    note: dict[str, str]
    appointment_date: datetime
    days_ago: int


class PatientContext(PydanticBaseModel):
    notes: list[NoteContext]
    "most recent appointment first"

    intake_summary: str | None
    "most recent AI intake summary, if any"

    engagement_role: EngagementRole | None
    "most recent communication-role read (the patient personality profile), if any"

    cps_self_report: CpsSelfReport | None
    "most recent patient-reported communication preference, if any"


def assemble_patient_context(
    doctor_id: TypeIDType, patient_id: TypeIDType, *, limit_notes: int = 5
) -> PatientContext:
    notes = []
    for visit_note in recent_notes_for_patient(
        doctor_id, patient_id, limit=limit_notes
    ):
        appointment = Appointment.one(id=visit_note.appointment_id)
        notes.append(
            NoteContext(
                note=visit_note.note,
                appointment_date=appointment.appointment_date,
                days_ago=days_since(appointment.appointment_date),
            )
        )

    intake = cast(
        Intake | None,
        (
            Intake.select()
            .join(Appointment, Intake.appointment_id == Appointment.id)
            .where(
                (Intake.doctor_id == doctor_id)
                & (Appointment.patient_id == patient_id)
                & (cast(Any, Intake.summary).is_not(None))
            )
            .order_by(cast(Any, Appointment.updated_at).desc())
            .limit(1)
            .first()
        ),
    )

    engagement_role = cast(
        EngagementRole | None,
        (
            EngagementRole.select()
            .join(Appointment, EngagementRole.appointment_id == Appointment.id)
            .where(
                (EngagementRole.doctor_id == doctor_id)
                & (Appointment.patient_id == patient_id)
            )
            .order_by(cast(Any, Appointment.updated_at).desc())
            .limit(1)
            .first()
        ),
    )

    cps_self_report = cast(
        CpsSelfReport | None,
        (
            CpsSelfReport.select()
            .join(Appointment, CpsSelfReport.appointment_id == Appointment.id)
            .where(
                (CpsSelfReport.doctor_id == doctor_id)
                & (Appointment.patient_id == patient_id)
            )
            .order_by(cast(Any, Appointment.updated_at).desc())
            .limit(1)
            .first()
        ),
    )

    return PatientContext(
        notes=notes,
        intake_summary=intake.summary if intake else None,
        engagement_role=engagement_role,
        cps_self_report=cps_self_report,
    )
