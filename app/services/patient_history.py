"""
Collect a patient's prior records from this practice — the inputs for the
provider-facing "patient context" synthesis.

Only data the practice already holds is used: clinical notes from prior visits
and answers the patient typed into previous intake chats. Nothing is fetched
from external sources.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

from app.models.ai_visit_note import AIVisitNote
from app.models.appointment import Appointment
from app.models.intake.intake import Intake

MAX_PRIOR_NOTES = 5
"most recent prior visit notes to include"

MAX_PRIOR_INTAKES = 3
"most recent prior intakes whose answers are included"


def days_since(past_dt: datetime) -> int:
    past_date = past_dt.date()
    today = datetime.today().date()
    return (today - past_date).days


@dataclass
class PriorNote:
    days_ago: int
    note: dict[str, str]


@dataclass
class PriorIntakeAnswer:
    days_ago: int
    question: str
    answer: str


@dataclass
class PatientHistory:
    prior_notes: list[PriorNote] = field(default_factory=list)
    prior_intake_answers: list[PriorIntakeAnswer] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.prior_notes and not self.prior_intake_answers


def collect(intake: Intake) -> PatientHistory:
    """
    Gather this patient's prior visit notes and previous intake answers,
    scoped to the intake's doctor and excluding the current appointment/intake.
    """
    appointment = Appointment.one(id=intake.appointment_id)

    history = PatientHistory()

    prior_notes = (
        AIVisitNote.select()
        .join(Appointment, AIVisitNote.appointment_id == Appointment.id)
        .where(
            (AIVisitNote.doctor_id == intake.doctor_id)
            & (Appointment.patient_id == appointment.patient_id)
            & (AIVisitNote.appointment_id != intake.appointment_id)
        )
        .order_by(cast(Any, Appointment.appointment_date).desc())
        .limit(MAX_PRIOR_NOTES)
        .all()
    )

    for prior_note in cast(list[AIVisitNote], prior_notes):
        note_appointment = Appointment.one(id=prior_note.appointment_id)
        history.prior_notes.append(
            PriorNote(
                days_ago=days_since(note_appointment.appointment_date),
                note=prior_note.note,
            )
        )

    prior_intakes = (
        Intake.select()
        .join(Appointment, Intake.appointment_id == Appointment.id)
        .where(
            (Intake.doctor_id == intake.doctor_id)
            & (Appointment.patient_id == appointment.patient_id)
            & (Intake.id != intake.id)
        )
        .order_by(cast(Any, Appointment.appointment_date).desc())
        .limit(MAX_PRIOR_INTAKES)
        .all()
    )

    for prior_intake in cast(list[Intake], prior_intakes):
        intake_appointment = Appointment.one(id=prior_intake.appointment_id)

        for question in sorted(prior_intake.intake_questions, key=lambda q: q.order):
            if not question.answer:
                continue

            history.prior_intake_answers.append(
                PriorIntakeAnswer(
                    days_ago=days_since(intake_appointment.appointment_date),
                    question=question.question,
                    answer=question.answer,
                )
            )

    return history
