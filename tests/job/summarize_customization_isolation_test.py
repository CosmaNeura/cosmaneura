# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from unittest.mock import patch

from app.jobs import summarize

from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.note_type_customization import NoteTypeCustomization

from tests.factories import AppointmentFactory, DoctorFactory, PatientFactory


def _saved_transcript(doctor_id, appointment_id, patient_id) -> AIVisitTranscript:
    return AIVisitTranscript(
        doctor_id=doctor_id,
        appointment_id=appointment_id,
        patient_id=patient_id,
        duration=60,
        transcript_data=[
            TranscriptEntry(
                speaker="Patient", content="My back hurts.", startTime=0, endTime=1
            )
        ],
    ).save()


def test_perform_loads_each_schemas_own_customization():
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    appointment = AppointmentFactory.build(
        doctor_id=doctor.id, patient_id=patient.id
    ).save()
    transcript = _saved_transcript(doctor.id, appointment.id, patient.id)

    schema_a = DoctorNoteSchema(
        doctor_id=doctor.id, note_type="Iso A", fields={"CC": "x", "Plan": "y"}
    ).save()
    schema_b = DoctorNoteSchema(
        doctor_id=doctor.id, note_type="Iso B", fields={"CC": "x", "Plan": "y"}
    ).save()
    NoteTypeCustomization(
        doctor_note_schema_id=schema_a.id, require_plan_reason=False
    ).save()
    NoteTypeCustomization(
        doctor_note_schema_id=schema_b.id, require_plan_reason=True
    ).save()

    seen: dict = {}

    def fake_generate(transcript, example_note, note_schema, customization=None):
        seen[note_schema.id] = customization
        return {"CC": "stub", "Plan": "stub"}

    with (
        patch.object(summarize, "generate_clinical_note", side_effect=fake_generate),
        patch.object(summarize, "extract_action_items", return_value="none"),
        patch.object(
            summarize,
            "augment_clinical_note",
            side_effect=lambda original, transcript: original,
        ),
    ):
        summarize.perform(transcript.id, schema_a.id)
        summarize.perform(transcript.id, schema_b.id)

    assert seen[schema_a.id] is not None
    assert seen[schema_b.id] is not None
    assert seen[schema_a.id].doctor_note_schema_id == schema_a.id
    assert seen[schema_b.id].doctor_note_schema_id == schema_b.id
    assert seen[schema_a.id].require_plan_reason is False
    assert seen[schema_b.id].require_plan_reason is True
