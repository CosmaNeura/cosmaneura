# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportArgumentType=false, reportReturnType=false

import json
import os
import re

import pytest

from app.jobs.summarize import generate_clinical_note, perform

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.appointment import Appointment
from app.models.doctor_note_example import DoctorNoteExample
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.patient import Patient

from tests.eval import eval_llm, eval_n_of_m

_RUN_LIVE_LLM_TESTS = os.getenv("RUN_LIVE_LLM_TESTS") == "true"
_HAS_AZURE_OPENAI_CREDS = bool(
    os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("AZURE_OPENAI_ENDPOINT")
)
pytestmark = pytest.mark.skipif(
    not (_RUN_LIVE_LLM_TESTS and _HAS_AZURE_OPENAI_CREDS),
    reason=(
        "requires live Azure OpenAI access; set RUN_LIVE_LLM_TESTS=true and Azure OpenAI env vars"
    ),
)


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

    transcript = AIVisitTranscript.where(
        AIVisitTranscript.appointment_id == appt.id
    ).one()
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

    def create_clinical_note() -> str:
        return json.dumps(
            generate_clinical_note(
                transcript=transcript,
                example_note=example_note,
                note_schema=schema,
            )
        )

    cirtieria = "The 'Plan' section of the note should mention the patient monitoring for changes in vision."

    eval_n_of_m(call_llm=create_clinical_note, criteria=cirtieria, n=1, m=2)


def test_summarize_updates_appointment_name_based_on_intake():
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

    perform(transcript.id, schema.id)

    appt = Appointment.where(Appointment.patient_id == patient.id).one()
    assert appt is not None
    assert appt.appointment_name is not None
    assert eval_llm(
        appt.appointment_name,
        "Should mention it is a follow up for diabetes management",
    )


# Done at the request of Teresa Jensen.
def test_note_includes_all_actionable_steps_for_doctor():
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

    def create_clinical_note() -> str:
        return json.dumps(
            generate_clinical_note(
                transcript=transcript,
                example_note=example_note,
                note_schema=schema,
            )
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
    patient = Patient.where(Patient.name == "Ghengis Khan").one()
    appt = Appointment.where(Appointment.patient_id == patient.id).first()
    transcript = AIVisitTranscript.where(
        AIVisitTranscript.appointment_id == appt.id
    ).one()

    assert appt is not None
    assert appt.appointment_name is None

    schema = DoctorNoteSchema.where(
        (DoctorNoteSchema.doctor_id == transcript.doctor_id)
        & (DoctorNoteSchema.note_type == "Test SOAP Note")
    ).first()
    assert schema is not None

    init_note_count = count_visit_notes(transcript, schema)

    perform(transcript.id, schema.id)

    assert count_visit_notes(transcript, schema) == init_note_count + 1

    note = (
        AIVisitNote.where(
            (AIVisitNote.ai_transcript_id == transcript.id)
            & (AIVisitNote.doctor_note_schema_id == schema.id)
        )
        .limit(1)
        .one()
    )

    perform(transcript.id, schema.id)

    assert count_visit_notes(transcript, schema) == init_note_count + 1

    last_id = (
        AIVisitNote.where(
            (AIVisitNote.ai_transcript_id == transcript.id)
            & (AIVisitNote.doctor_note_schema_id == schema.id)
        )
        .order_by(AIVisitNote.updated_at.desc())
        .limit(1)
        .one()
        .id
    )

    assert note.id == last_id

    # Appointment name has been updated.
    appt = Appointment.get(transcript.appointment_id)
    assert appt is not None
    assert appt.appointment_name is not None
    assert eval_llm(appt.appointment_name, "Should mention issues with breathing")


# Issue #33 item: notes should use the patient's actual pronouns
# instead of defaulting to "they/them". The patient's pronouns are
# inferred from the transcript itself (the doctor or patient uses them
# explicitly). This test seeds a transcript where "she/her" is used
# consistently and asserts the generated note matches.
def test_note_uses_patient_pronouns_from_transcript():
    patient = Patient.where(Patient.name == "Samwise Gamgee").one()
    assert patient is not None

    appt = Appointment.where(Appointment.patient_id == patient.id).one()
    assert appt is not None

    schema = DoctorNoteSchema.where(
        (DoctorNoteSchema.doctor_id == appt.doctor_id)
        & (DoctorNoteSchema.note_type == "Test SOAP Note")
    ).one()
    assert schema is not None

    example_note = (
        DoctorNoteExample.select()
        .where(DoctorNoteExample.doctor_note_schema_id == schema.id)
        .order_by(DoctorNoteExample.updated_at.desc())
        .limit(1)
        .one()
    )

    # In-memory transcript — no .save() needed because
    # generate_clinical_note only reads transcript_data + doctor_id.
    # The doctor and patient both use "she/her" repeatedly; name is
    # never used in pronoun-bearing sentences so the LLM has only the
    # transcript's explicit pronoun signal to go on.
    transcript = AIVisitTranscript(
        doctor_id=appt.doctor_id,
        appointment_id=appt.id,
        patient_id=patient.id,
        duration=120,
        transcript_data=[
            TranscriptEntry(
                startTime=0,
                endTime=4000,
                speaker="Doctor",
                content="How is she feeling about the new medication?",
            ),
            TranscriptEntry(
                startTime=4000,
                endTime=12000,
                speaker="Patient",
                content="I've been doing better since I started it. I feel less anxious overall.",
            ),
            TranscriptEntry(
                startTime=12000,
                endTime=16000,
                speaker="Doctor",
                content="And the headaches she was getting?",
            ),
            TranscriptEntry(
                startTime=16000,
                endTime=22000,
                speaker="Patient",
                content="They're mostly gone. I get one maybe once a week now instead of every day.",
            ),
            TranscriptEntry(
                startTime=22000,
                endTime=26000,
                speaker="Doctor",
                content="Has she had any side effects from it?",
            ),
            TranscriptEntry(
                startTime=26000,
                endTime=32000,
                speaker="Patient",
                content="A little dry mouth at first but that went away after the first week.",
            ),
        ],
    )

    def create_clinical_note() -> str:
        return json.dumps(
            generate_clinical_note(
                transcript=transcript,
                example_note=example_note,
                note_schema=schema,
            )
        )

    criteria = (
        "This clinical note must use 'she' or 'her' pronouns at least once to "
        "refer to the patient (matching the pronouns used in the transcript "
        "by the doctor). The note must NOT refer to the patient as 'they' or "
        "'them', and must NOT avoid pronouns entirely by always saying 'the "
        "patient' -- a natural clinical note about this transcript should "
        "use she/her at least once."
    )

    eval_n_of_m(call_llm=create_clinical_note, criteria=criteria, n=1, m=2)


# Issue #33 item: the Subjective section should be sourced only from
# what the patient said (lines beginning with "Patient:"), not from
# the doctor's interpretation. This test seeds a transcript where the
# doctor offers a specific diagnostic interpretation ("costochondritis")
# that the patient never says, and asserts that label does not appear
# in Subjective.
def test_subjective_excludes_doctor_interpretation():
    patient = Patient.where(Patient.name == "Ocie Dosie").one()
    assert patient is not None

    appt = Appointment.where(Appointment.patient_id == patient.id).one()
    assert appt is not None

    schema = DoctorNoteSchema.where(
        (DoctorNoteSchema.doctor_id == appt.doctor_id)
        & (DoctorNoteSchema.note_type == "Test SOAP Note")
    ).one()
    assert schema is not None

    example_note = (
        DoctorNoteExample.select()
        .where(DoctorNoteExample.doctor_note_schema_id == schema.id)
        .order_by(DoctorNoteExample.updated_at.desc())
        .limit(1)
        .one()
    )

    transcript = AIVisitTranscript(
        doctor_id=appt.doctor_id,
        appointment_id=appt.id,
        patient_id=patient.id,
        duration=120,
        transcript_data=[
            TranscriptEntry(
                startTime=0,
                endTime=3000,
                speaker="Doctor",
                content="What brings you in today?",
            ),
            TranscriptEntry(
                startTime=3000,
                endTime=10000,
                speaker="Patient",
                content="I've been having a sharp pain when I breathe in. It's right here on the left side of my chest, has been for about a week.",
            ),
            TranscriptEntry(
                startTime=10000,
                endTime=15000,
                speaker="Doctor",
                content="Any redness or swelling at the site? Does pressing on the spot make it worse?",
            ),
            TranscriptEntry(
                startTime=15000,
                endTime=21000,
                speaker="Patient",
                content="Yeah, pressing on it makes it a lot worse. No redness or swelling that I can see.",
            ),
            TranscriptEntry(
                startTime=21000,
                endTime=28000,
                speaker="Doctor",
                content="Sounds like classic costochondritis -- pain that worsens with palpation and with deep inspiration. That's an inflammation of the cartilage where the ribs meet the sternum.",
            ),
            TranscriptEntry(
                startTime=28000,
                endTime=32000,
                speaker="Patient",
                content="Okay. Anything I should be doing differently in the meantime?",
            ),
        ],
    )

    def create_clinical_note() -> str:
        return json.dumps(
            generate_clinical_note(
                transcript=transcript,
                example_note=example_note,
                note_schema=schema,
            )
        )

    criteria = (
        "The 'Subjective' section of this clinical note should describe "
        "the patient's own reported symptoms (sharp pain when breathing in, "
        "pain on the left side of the chest, pain worse when pressing on "
        "the spot). The 'Subjective' section must NOT contain the word "
        "'costochondritis' or describe the condition by name -- that is "
        "the doctor's diagnostic interpretation and belongs in the "
        "'Assessment' section. It is acceptable, and expected, for "
        "'costochondritis' to appear elsewhere in the note (e.g. Assessment)."
    )

    eval_n_of_m(call_llm=create_clinical_note, criteria=criteria, n=1, m=2)
