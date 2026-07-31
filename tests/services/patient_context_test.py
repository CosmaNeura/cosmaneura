from datetime import datetime, timedelta

from app.services.patient_context import (
    NOTE_LOOKBACK,
    assemble_patient_context,
    latest_note_for_patient,
    recent_notes_for_patient,
)

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.intake.intake import Intake

from tests.factories import AppointmentFactory, DoctorFactory, PatientFactory


def _create_note(
    doctor, patient, *, appointment_date: datetime, note: dict[str, str]
) -> AIVisitNote:
    appointment = AppointmentFactory.build(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_date=appointment_date,
    ).save()
    transcript = AIVisitTranscript(
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        duration=60,
        transcript_data=[],
    ).save()
    schema = DoctorNoteSchema.get(doctor_id=doctor.id, note_type="Test SOAP")
    if schema is None:
        schema = DoctorNoteSchema(
            doctor_id=doctor.id, note_type="Test SOAP", fields={"Plan": "plan"}
        ).save()
    return AIVisitNote(
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        ai_transcript_id=transcript.id,
        doctor_note_schema_id=schema.id,
        note=note,
    ).save()


def test_latest_note_returns_most_recent_within_lookback():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()

    _create_note(
        doctor,
        patient,
        appointment_date=datetime.now() - timedelta(days=60),
        note={"Plan": "older visit"},
    )
    _create_note(
        doctor,
        patient,
        appointment_date=datetime.now() - timedelta(days=7),
        note={"Plan": "newest visit"},
    )

    result = latest_note_for_patient(doctor.id, patient.id)
    assert result is not None
    assert result.note == {"Plan": "newest visit"}


def test_notes_older_than_lookback_are_excluded():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()

    _create_note(
        doctor,
        patient,
        appointment_date=datetime.now() - NOTE_LOOKBACK - timedelta(days=30),
        note={"Plan": "ancient visit"},
    )

    assert latest_note_for_patient(doctor.id, patient.id) is None
    assert recent_notes_for_patient(doctor.id, patient.id, limit=5) == []


def test_notes_scoped_to_doctor_and_patient():
    doctor = DoctorFactory.build().save()
    other_doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    other_patient = PatientFactory.build(doctor_id=doctor.id).save()

    _create_note(
        other_doctor,
        patient,
        appointment_date=datetime.now() - timedelta(days=1),
        note={"Plan": "other doctor's note"},
    )
    _create_note(
        doctor,
        other_patient,
        appointment_date=datetime.now() - timedelta(days=1),
        note={"Plan": "other patient's note"},
    )

    assert latest_note_for_patient(doctor.id, patient.id) is None


def test_assemble_patient_context_with_no_history():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()

    context = assemble_patient_context(doctor.id, patient.id)

    assert context.notes == []
    assert context.intake_summary is None
    assert context.engagement_role is None
    assert context.cps_self_report is None


def test_assemble_patient_context_returns_notes_and_intake_summary():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()

    _create_note(
        doctor,
        patient,
        appointment_date=datetime.now() - timedelta(days=30),
        note={"Plan": "earlier"},
    )
    _create_note(
        doctor,
        patient,
        appointment_date=datetime.now() - timedelta(days=3),
        note={"Plan": "latest"},
    )

    intake_appointment = AppointmentFactory.build(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_date=datetime.now() + timedelta(days=1),
    ).save()
    Intake(
        doctor_id=doctor.id,
        appointment_id=intake_appointment.id,
        external_intake_data={},
        intake_questions=[],
        summary="Concise intake summary.",
    ).save()

    context = assemble_patient_context(doctor.id, patient.id, limit_notes=2)

    assert [n.note["Plan"] for n in context.notes] == ["latest", "earlier"]
    assert all(n.days_ago >= 0 for n in context.notes)
    assert context.intake_summary == "Concise intake summary."


def test_assemble_limits_note_count():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()

    for days_ago in [40, 30, 20, 10]:
        _create_note(
            doctor,
            patient,
            appointment_date=datetime.now() - timedelta(days=days_ago),
            note={"Plan": f"{days_ago} days ago"},
        )

    context = assemble_patient_context(doctor.id, patient.id, limit_notes=2)
    assert len(context.notes) == 2
    assert context.notes[0].note["Plan"] == "10 days ago"
