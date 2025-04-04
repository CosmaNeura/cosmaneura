import re

from app.jobs.summarize import perform

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.patient import Patient


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
def test_summarize_includes_patient_action_items():
    patient = Patient.where(Patient.name == "Samwise Gamgee").one()
    assert patient is not None

    appt = Appointment.where(Appointment.patient_id == patient.id).one()
    assert appt is not None
    assert appt.appointment_name is None

    schema = DoctorNoteSchema.get(
        DoctorNoteSchema.doctor_id == appt.doctor_id
        and DoctorNoteSchema.note_type == "Test SOAP Note"
    )
    assert schema is not None

    transcript = (
        AIVisitTranscript.select()
        .where(AIVisitTranscript.appointment_id == appt.id)
        .one()
    )
    assert transcript is not None

    note = AIVisitNote.get(
        AIVisitNote.ai_transcript_id == transcript.id
        and AIVisitNote.doctor_note_schema_id == schema.id
    )
    assert note is None

    perform(transcript.id, schema.id)

    note = AIVisitNote.get(
        AIVisitNote.ai_transcript_id == transcript.id
        and AIVisitNote.doctor_note_schema_id == schema.id
    )
    assert note is not None

    fields = note.note
    plan = fields["Plan"]

    try:
        assert "optometrist" in plan
    except AssertionError:
        assert "vision" in plan

    appt = Appointment.where(Appointment.patient_id == patient.id).one()
    assert appt is not None
    assert appt.appointment_name is not None
    assert appt.appointment_name == fields["CC"]


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
        AIVisitTranscript.where(AIVisitTranscript.appointment_id == appt.id).one()
    )
    assert transcript is not None

    note = AIVisitNote.where(
        (AIVisitNote.ai_transcript_id == transcript.id)
        & (AIVisitNote.doctor_note_schema_id == schema.id)
    ).first()
    assert note is None

    perform(transcript.id, schema.id)

    appt = Appointment.where(Appointment.patient_id == patient.id).one()
    assert appt is not None
    assert appt.appointment_name is not None
    assert appt.appointment_name == "Follow-up -- Diabetes management"

    note = AIVisitNote.where(
        (AIVisitNote.ai_transcript_id == transcript.id)
        & (AIVisitNote.doctor_note_schema_id == schema.id)
    ).first()
    assert note is not None

    fields = note.note
    plan = fields["Plan"].lower()

    # This is always going to be brittle until we start using an eval library.
    assert "sudafed" in plan
    assert "sildenafil" in plan
    assert " mozal" in plan

    assert_matches(r"order fast(ed|ing) labs", plan)
    assert "glucose" in plan
    assert "insulin" in plan
    assert "cortisol" in plan
    assert "dhea" in plan
    assert "testosterone" in plan.lower()
    assert "estradiol" in plan.lower()
    assert "progesterone" in plan.lower()
    assert "lh" in plan
    assert "fsh" in plan

    assert_matches(r"request (records|labs)", plan)
    assert_matches(r"send a list of labs", plan)


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
