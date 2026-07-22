from datetime import datetime, timedelta

from app.jobs.session_prep import perform

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.session_prep import SessionPrep, SessionPrepStatus

from tests.factories import AppointmentFactory, DoctorFactory, PatientFactory


def _create_note(doctor, patient, *, appointment_date: datetime, note: dict[str, str]):
    appointment = AppointmentFactory.build(
        doctor_id=doctor.id,
        patient_id=patient.id,
        appointment_date=appointment_date,
    ).save()
    transcript = AIVisitTranscript(
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
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


def test_no_history_completes_with_first_session_brief():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    prep = SessionPrep(doctor_id=doctor.id, patient_id=patient.id).save()

    perform(prep.id)

    prep = SessionPrep.one(id=prep.id)
    assert prep.status == SessionPrepStatus.completed
    assert prep.talking_points == ["First session: no prior history in Cosmas"]
    assert prep.visits_in_context == 0
    assert prep.days_since_last_session is None


def test_generates_brief_from_prior_notes():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()

    _create_note(
        doctor,
        patient,
        appointment_date=datetime.now() - timedelta(days=30),
        note={"Plan": "start CBT homework"},
    )
    _create_note(
        doctor,
        patient,
        appointment_date=datetime.now() - timedelta(days=7),
        note={"Plan": "review sleep log next session"},
    )

    prep = SessionPrep(doctor_id=doctor.id, patient_id=patient.id).save()
    perform(prep.id)

    prep = SessionPrep.one(id=prep.id)
    assert prep.status == SessionPrepStatus.completed
    assert len(prep.talking_points) >= 1
    assert len(prep.talking_points) <= 5
    assert prep.open_threads == ["review sleep log next session"]
    assert prep.visits_in_context == 2
    assert prep.days_since_last_session == 7


def test_perform_is_idempotent_when_completed():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()

    prep = SessionPrep(
        doctor_id=doctor.id,
        patient_id=patient.id,
        status=SessionPrepStatus.completed,
        talking_points=["already generated"],
    ).save()

    perform(prep.id)

    prep = SessionPrep.one(id=prep.id)
    assert prep.talking_points == ["already generated"]
