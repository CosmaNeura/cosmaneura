from datetime import UTC, datetime, timedelta

from app.services.patient_context import (
    NOTE_LOOKBACK,
    assemble_patient_context,
    latest_note_for_patient,
    recent_notes_for_patient,
)

from activemodel.session_manager import _session_context, get_session
from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.intake.intake import Intake
from sqlalchemy import text

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


def test_superseded_note_is_excluded_from_patient_prompt_context():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    current = _create_note(
        doctor,
        patient,
        appointment_date=datetime.now() - timedelta(days=2),
        note={"Plan": "current synthetic context"},
    )
    historical = AIVisitNote(
        doctor_id=current.doctor_id,
        appointment_id=current.appointment_id,
        ai_transcript_id=current.ai_transcript_id,
        doctor_note_schema_id=current.doctor_note_schema_id,
        note={"Plan": "superseded synthetic context"},
        superseded_at=datetime.now(UTC),
        supersession_reason="migration:a81c9d7e4f20",
    ).save()

    notes = recent_notes_for_patient(doctor.id, patient.id, limit=5)

    assert [note.id for note in notes] == [current.id]
    assert historical.note not in [note.note for note in notes]


def test_encounter_cutoff_uses_utc_in_a_non_utc_database_session():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    cutoff = datetime.now(UTC) - timedelta(days=2)
    prior = _create_note(
        doctor,
        patient,
        appointment_date=(cutoff - timedelta(minutes=5)).replace(tzinfo=None),
        note={"Plan": "Prior synthetic visit"},
    )
    _create_note(
        doctor,
        patient,
        appointment_date=(cutoff + timedelta(minutes=5)).replace(tzinfo=None),
        note={"Plan": "Later synthetic visit"},
    )
    with get_session() as session:
        context = _session_context.set(session)
        try:
            session.execute(text("SET LOCAL TIME ZONE 'America/New_York'"))
            result = recent_notes_for_patient(
                doctor.id, patient.id, limit=6, before=cutoff
            )
            assert [note.id for note in result] == [prior.id]
        finally:
            session.rollback()
            _session_context.reset(context)


def test_same_time_notes_have_a_stable_order_before_limiting():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    when = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2)
    notes = [
        _create_note(
            doctor, patient, appointment_date=when, note={"Plan": f"Synthetic {index}"}
        )
        for index in range(3)
    ]
    expected = sorted((str(note.id) for note in notes), reverse=True)[:2]
    assert [
        str(note.id)
        for note in recent_notes_for_patient(doctor.id, patient.id, limit=2)
    ] == expected
