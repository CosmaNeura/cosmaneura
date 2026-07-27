"""
Patient context: the provider-facing profile assembled from records the
practice already holds (prior visit notes + previous intake answers).

These run fully offline via the deterministic local fallback, which mirrors the
LLM's attribution format (source + age labels, "unconfirmed" markers).
"""

from datetime import datetime, timedelta

import app.jobs.intake.summarize as summarize
from app.services.patient_history import collect

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.intake.intake import Intake, IntakeSchemaQuestion
from app.models.patient import Patient

from tests.factories import (
    DoctorFactory,
    PatientFactory,
    SimpleIncompleteIntakeFactory,
)


def _appointment(doctor: Doctor, patient: Patient, days_offset: int) -> Appointment:
    return Appointment(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_date=datetime.now() + timedelta(days=days_offset),
        external_id=None,
        created_by_system="test",
    ).save()


def _prior_note(
    doctor: Doctor,
    patient: Patient,
    days_ago: int,
    note: dict[str, str] | None = None,
) -> AIVisitNote:
    appointment = _appointment(doctor, patient, days_offset=-days_ago)
    schema = DoctorNoteSchema(
        doctor_id=doctor.id, note_type="SOAP", fields={"CC": "x"}
    ).save()
    transcript = AIVisitTranscript(
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        duration=60,
        transcript_data=[],
    ).save()

    return AIVisitNote(
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        ai_transcript_id=transcript.id,
        doctor_note_schema_id=schema.id,
        note=note
        or {
            "Assessment": "Recurring migraines",
            "Plan": "Trial of sumatriptan",
        },
    ).save()


def _answered_questions(prefix: str) -> list[IntakeSchemaQuestion]:
    return [
        IntakeSchemaQuestion(
            order=index,
            question=f"{prefix} question {index}?",
            required_details="",
            answer=f"{prefix} answer {index}",
            answer_timestamp=1_000_000 + index,
        )
        for index in range(2)
    ]


def _intake(
    doctor: Doctor,
    appointment: Appointment,
    questions: list[IntakeSchemaQuestion],
) -> Intake:
    return SimpleIncompleteIntakeFactory.build(
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        intake_questions=questions,
    ).save()


def test_context_generated_from_prior_records():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()

    _prior_note(doctor, patient, days_ago=30)

    prior_intake_appointment = _appointment(doctor, patient, days_offset=-20)
    _intake(doctor, prior_intake_appointment, _answered_questions("prior"))

    current_appointment = _appointment(doctor, patient, days_offset=7)
    current = _intake(doctor, current_appointment, _answered_questions("current"))

    summarize.perform(current.id)

    current.refresh()
    assert current.summary
    assert current.patient_context

    # prior clinical note, attributed with source + age
    assert "Recurring migraines" in current.patient_context
    assert "(visit note, 30 days ago)" in current.patient_context

    # prior self-reported answers are attributed and flagged unconfirmed
    assert "prior answer 0" in current.patient_context
    assert "(patient-reported intake answer, 20 days ago)" in current.patient_context
    assert "unconfirmed" in current.patient_context

    # the current intake's own answers are not part of the history
    assert "current answer 0" not in current.patient_context


def test_no_history_leaves_context_empty():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()

    current_appointment = _appointment(doctor, patient, days_offset=7)
    current = _intake(doctor, current_appointment, _answered_questions("current"))

    summarize.perform(current.id)

    current.refresh()
    assert current.summary
    assert current.patient_context is None


def test_context_backfills_when_summary_already_exists():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()

    _prior_note(doctor, patient, days_ago=10)

    current_appointment = _appointment(doctor, patient, days_offset=7)
    current = _intake(doctor, current_appointment, _answered_questions("current"))
    current.summary = "- existing summary"
    current.save()

    summarize.perform(current.id)

    current.refresh()
    # summary untouched, context filled in
    assert current.summary == "- existing summary"
    assert current.patient_context
    assert "(visit note, 10 days ago)" in current.patient_context


def test_history_scoped_to_patient_and_doctor():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()

    # noise: same doctor, different patient
    other_patient = PatientFactory.build(doctor_id=doctor.id).save()
    _prior_note(doctor, other_patient, days_ago=15)

    # noise: same patient name/dob under a different doctor
    other_doctor = DoctorFactory.build().save()
    other_doctor_patient = PatientFactory.build(doctor_id=other_doctor.id).save()
    _prior_note(other_doctor, other_doctor_patient, days_ago=15)

    current_appointment = _appointment(doctor, patient, days_offset=7)
    current = _intake(doctor, current_appointment, _answered_questions("current"))

    history = collect(current)
    assert history.is_empty()


def test_collect_excludes_current_intake_and_unanswered():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()

    prior_appointment = _appointment(doctor, patient, days_offset=-20)
    _intake(doctor, prior_appointment, _answered_questions("prior"))

    # a prior intake the patient never filled out contributes nothing
    unanswered_appointment = _appointment(doctor, patient, days_offset=-40)
    unanswered = SimpleIncompleteIntakeFactory.build(
        doctor_id=doctor.id, appointment_id=unanswered_appointment.id
    ).save()
    assert unanswered.unanswered_questions()

    current_appointment = _appointment(doctor, patient, days_offset=7)
    current = _intake(doctor, current_appointment, _answered_questions("current"))

    history = collect(current)

    assert [entry.answer for entry in history.prior_intake_answers] == [
        "prior answer 0",
        "prior answer 1",
    ]
    assert not history.prior_notes
