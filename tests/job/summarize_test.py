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
    return (
        AIVisitNote.select()
        .where(
            AIVisitNote.doctor_id == transcript.doctor_id
            and AIVisitNote.appointment_id == transcript.appointment_id
            and AIVisitNote.ai_transcript_id == transcript.id
            and AIVisitNote.doctor_note_schema_id == schema.id
        )
        .count()
    )


# Done at the request of Teresa Jensen.
def test_summarize_includes_all_actionable_steps():
    patient = Patient.select().where(Patient.name == "Ocie Dosie").one()
    assert patient is not None

    appt = Appointment.select().where(Appointment.patient_id == patient.id).one()
    assert appt is not None

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

    assert "Sudafed" in plan
    assert "Sildenafil" in plan
    assert "Dr. Mozal" in plan

    assert_matches(r"order fast(ed|ing) labs", plan)
    assert "glucose level" in plan
    assert "insulin level" in plan
    assert "cortisol" in plan
    assert "DHEA" in plan
    assert "testosterone" in plan
    assert "estradiol" in plan
    assert "progesterone" in plan
    assert "LH" in plan
    assert "FSH" in plan

    assert_matches(r"request (records|labs)", plan)
    assert_matches(r"send a list of labs", plan)


def test_summarize_is_idempotent():
    transcript = AIVisitTranscript.sample()
    assert transcript is not None

    schema = DoctorNoteSchema.get(
        DoctorNoteSchema.doctor_id == transcript.doctor_id
        and DoctorNoteSchema.note_type == "Test SOAP Note"
    )
    assert schema is not None

    init_note_count = count_visit_notes(transcript, schema)

    perform(transcript.id, schema.id)

    assert count_visit_notes(transcript, schema) == init_note_count + 1

    note = AIVisitNote.get(
        AIVisitNote.ai_transcript_id == transcript.id
        and AIVisitNote.doctor_note_schema_id == schema.id
    )

    perform(transcript.id, schema.id)

    assert count_visit_notes(transcript, schema) == init_note_count + 1

    assert (
        note.id
        == AIVisitNote.select()
        .where(
            AIVisitNote.ai_transcript_id == transcript.id
            and AIVisitNote.doctor_note_schema_id == schema.id
        )
        .order_by(AIVisitNote.updated_at.desc())
        .limit(1)
        .one()
        .id
    )
