"""
Terminology threading through the LLM prompt builders.

The default (doctor/patient) rendering must stay byte-identical to the
pre-terminology prompts — that invariant is pinned by
tests/job/summarize_prompt_test.py's ORIGINAL_DEFAULT_BLOCK. These tests
cover the non-default sets.
"""

from unittest.mock import patch

from app.jobs import summarize
from app.jobs.intake.generate_questions import (
    extraction_prompt,
    generate_questions_prompt,
)
from app.jobs.intake.summarize import generate_prompt as intake_summary_prompt
from app.jobs.summarize import (
    _instruction_block,
    augment_clinical_note,
    build_action_item_prompt,
    build_structured_prompt,
)
from app.services.terminology import TERMS, terminology_terms

from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.doctor import Doctor, Terminology
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.note_type_customization import default_customization

from tests.factories import (
    AppointmentFactory,
    DoctorFactory,
    PatientFactory,
    SimpleCompleteIntakeFactory,
)

THERAPIST = TERMS[Terminology.therapist_client]


def _therapist_doctor() -> Doctor:
    doctor = DoctorFactory.build().save()
    doctor.terminology = Terminology.therapist_client
    doctor.save()
    return doctor


def _transcript(doctor) -> AIVisitTranscript:
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    appointment = AppointmentFactory.build(
        doctor_id=doctor.id, patient_id=patient.id
    ).save()
    return AIVisitTranscript(
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        duration=120,
        transcript_data=[
            TranscriptEntry(
                speaker="Patient", content="My back hurts.", startTime=0, endTime=1
            )
        ],
    )


def test_terminology_terms_resolution():
    doctor = _therapist_doctor()
    assert terminology_terms(doctor) == THERAPIST
    assert terminology_terms(None).patient == "patient"


def test_therapist_instruction_block_uses_client_terms():
    block = _instruction_block(default_customization(), THERAPIST)

    assert "the client is coming in" in block
    assert (
        'refer to the client as "the client"' not in block
    )  # default customization infers pronouns
    assert "client-actionable items" in block
    # transcript speaker labels are data, never remapped
    assert '"Patient:"' in block
    # no stray default role words outside the literal label references
    assert "the patient is coming in" not in block
    assert "doctor-actionable" not in block


def test_therapist_structured_prompt_and_label_note():
    doctor = _therapist_doctor()
    transcript = _transcript(doctor)
    schema = DoctorNoteSchema(
        doctor_id=doctor.id, note_type="Therapy Note", fields={"Plan": "the plan"}
    ).save()

    with patch.object(summarize, "extract_action_items", return_value="- none"):
        prompt = build_structured_prompt(transcript, None, schema)

    assert "you just had with a client" in prompt
    assert 'lines labeled "Patient:" are the client' in prompt


def test_default_structured_prompt_has_no_label_note():
    doctor = DoctorFactory.build().save()
    transcript = _transcript(doctor)
    schema = DoctorNoteSchema(
        doctor_id=doctor.id, note_type="SOAP", fields={"Plan": "the plan"}
    ).save()

    with patch.object(summarize, "extract_action_items", return_value="- none"):
        prompt = build_structured_prompt(transcript, None, schema)

    assert "you just had with a patient" in prompt
    assert "lines labeled" not in prompt


def test_therapist_action_item_prompt():
    doctor = _therapist_doctor()
    transcript = _transcript(doctor)

    prompt = build_action_item_prompt(transcript, THERAPIST)

    assert "You are a therapist reviewing the transcript of a client visit" in prompt
    assert "Actions for the client" in prompt
    assert "Actions for the patient" not in prompt


def test_therapist_augmented_note_wording():
    doctor = _therapist_doctor()
    transcript = _transcript(doctor)

    note = augment_clinical_note({"Plan": "Rest."}, transcript)

    assert "spent in client care" in note["Plan"]


def test_therapist_intake_summary_prompt():
    doctor = _therapist_doctor()
    intake = SimpleCompleteIntakeFactory.build(doctor_id=doctor.id).save()

    prompt = intake_summary_prompt(intake)

    assert "You are a therapist with the following bio" in prompt
    assert "A client is coming in for an appointment" in prompt
    assert "The client was asked some follow up questions" in prompt


def test_therapist_intake_question_prompts():
    doctor = _therapist_doctor()
    intake = SimpleCompleteIntakeFactory.build(doctor_id=doctor.id).save()

    questions_prompt = generate_questions_prompt(intake, doctor)
    assert "You are a therapist with the following bio" in questions_prompt
    assert "A client is coming in for an appointment" in questions_prompt
    assert "What is the client's chief complaint?" in questions_prompt

    extract_prompt = extraction_prompt(intake, doctor)
    assert "You are a therapist that is about to see a client" in extract_prompt


def test_default_intake_prompts_unchanged():
    doctor = DoctorFactory.build().save()
    intake = SimpleCompleteIntakeFactory.build(doctor_id=doctor.id).save()

    prompt = intake_summary_prompt(intake)
    assert "You are a doctor with the following bio" in prompt
    assert "A patient is coming in for an appointment" in prompt
