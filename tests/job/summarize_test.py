import re
import json

from app.jobs.summarize import perform, generate_clinical_note

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.doctor_note_example import DoctorNoteExample
from app.models.patient import Patient

from tests.eval import eval_n_of_m


def assert_matches(regex, string):
    if not re.search(regex, string, re.IGNORECASE):
        raise AssertionError(f"Regex '{regex}' does not match the string '{string}'")

def count_visit_notes(transcript: AIVisitTranscript, schema: DoctorNoteSchema) -> int:
    return AIVisitNote.where(
        (AIVisitNote.doctor_id == transcript.doctor_id)
        & (AIVisitNote.appointment_id == transcript.appointment_id)
        & (AIVisitNote.ai_transcript_id == transcript.id)
        & (AIVisitNote.doctor_note_schema_id == schema.id)
    ).count()

def test_summarize_copies_schema_field_orer():
    patient = Patient.where(Patient.name == "Samwise Gamgee").one()
    assert patient is not None

    appt = Appointment.where(Appointment.patient_id == patient.id).one()
    assert appt is not None
    assert appt.appointment_name is None

    schema = DoctorNoteSchema.get(
        (DoctorNoteSchema.doctor_id == appt.doctor_id)
        & (DoctorNoteSchema.note_type == "Test SOAP Note")
    )
    assert schema is not None

    transcript = (
        AIVisitTranscript.where(AIVisitTranscript.appointment_id == appt.id).one()
    )
    assert transcript is not None

    perform(transcript.id, schema.id)

    note = AIVisitNote.get(
        (AIVisitNote.ai_transcript_id == transcript.id)
        & (AIVisitNote.doctor_note_schema_id == schema.id)
    )
    assert note is not None

    note_fields = list(note.note.keys())
    schema_fields = list(schema.fields.keys())

    assert note_fields == schema_fields

# Done at the request of Caroline at Fiat Health.
def test_note_includes_patient_action_items():
    patient = Patient.where(Patient.name == "Samwise Gamgee").one()
    assert patient is not None

    appt = Appointment.where(Appointment.patient_id == patient.id).one()
    assert appt.appointment_name is None

    schema = DoctorNoteSchema.where(
        (DoctorNoteSchema.doctor_id == appt.doctor_id)
        & (DoctorNoteSchema.note_type == "Test SOAP Note")
    ).one()

    transcript = (
        AIVisitTranscript.select()
        .where(AIVisitTranscript.appointment_id == appt.id)
        .limit(1)
        .one()
    )

    example_note = (
        DoctorNoteExample.select()
        .where(DoctorNoteExample.doctor_note_schema_id == schema.id)
        .order_by(DoctorNoteExample.updated_at.desc())
        .limit(1)
        .one()
    )

    create_clinical_note = lambda: (
        json.dumps(generate_clinical_note(
            transcript=transcript,
            example_note=example_note,
            note_schema=schema
        ))
    )

    cirtieria = "The 'Plan' section of the note should mention the patient seeing an optometrist or vision specialist."

    eval_n_of_m(call_llm=create_clinical_note, criteria=cirtieria, n=1, m=2)

# Done at the request of Teresa Jensen.
def test_summarize_includes_all_actionable_steps_for_doctor():
    patient = Patient.where(Patient.name == "Ocie Dosie").one()
    assert patient is not None

    appt = Appointment.where(Appointment.patient_id == patient.id).one()
    assert appt is not None
    assert appt.appointment_name is None

    schema = DoctorNoteSchema.where(
        (DoctorNoteSchema.doctor_id == appt.doctor_id)
        & (DoctorNoteSchema.note_type == "Test SOAP Note")
    ).first()
    assert schema is not None

    transcript = (
        AIVisitTranscript.select()
        .where(AIVisitTranscript.appointment_id == appt.id)
        .limit(1)
        .one()
    )

    example_note = (
        DoctorNoteExample.select()
        .where(DoctorNoteExample.doctor_note_schema_id == schema.id)
        .order_by(DoctorNoteExample.updated_at.desc())
        .limit(1)
        .one()
    )

    create_clinical_note = lambda: (
        json.dumps(generate_clinical_note(
            transcript=transcript,
            example_note=example_note,
            note_schema=schema
        ))
    )

    cirtieria = """
    The 'Plan' section of the note should mention:
      - labs will be ordered for fasting glucose, insulin, cortisol, DHEA, testosterone, estradiol, progesterone, LH, and FSH
      - the doctor will ask Dr. Mozal about the patient's dosage of Sildenafil and Sudafed
      - the doctor will request records and labs
      - the doctor will send a list of labs to the patient
    """

    eval_n_of_m(call_llm=create_clinical_note, criteria=cirtieria, n=1, m=2)


def test_summarize_is_idempotent():
    # Using duration to find the transcript isn't great but it keeps things consistent.
    transcript = AIVisitTranscript.where(AIVisitTranscript.duration == 182).one()
    assert transcript is not None

    schema = DoctorNoteSchema.where(
        (DoctorNoteSchema.doctor_id == transcript.doctor_id)
        & (DoctorNoteSchema.note_type == "Test SOAP Note")
    ).first()
    assert schema is not None

    init_note_count = count_visit_notes(transcript, schema)

    perform(transcript.id, schema.id)

    assert count_visit_notes(transcript, schema) == init_note_count + 1

    note = AIVisitNote.where(
        (AIVisitNote.ai_transcript_id == transcript.id)
        & (AIVisitNote.doctor_note_schema_id == schema.id)
    ).limit(1).one()

    perform(transcript.id, schema.id)

    assert count_visit_notes(transcript, schema) == init_note_count + 1

    last_id = AIVisitNote.where(
            (AIVisitNote.ai_transcript_id == transcript.id)
            & (AIVisitNote.doctor_note_schema_id == schema.id)
        ).order_by(AIVisitNote.updated_at.desc()).limit(1).one().id

    assert note.id == last_id
