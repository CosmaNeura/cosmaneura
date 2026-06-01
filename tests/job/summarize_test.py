# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportArgumentType=false, reportReturnType=false
"""Live-LLM evals for note generation.

These are a TEST mechanism only: they never run in production and never change
note output. They are gated by RUN_LIVE_LLM_TESTS=true + Azure OpenAI creds and
skip in CI. The judge model is configurable via OPENAI_EVAL_MODEL (see
tests/eval.py) so a deployed model can stand in for gpt-4.1-mini on staging.

Per-config evals build a NoteTypeCustomization and pass it through
generate_clinical_note(..., customization=...) to verify the model honors the
provider's settings. Each note type's config is independent — see
tests/job/summarize_customization_isolation_test.py.
"""

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
from app.models.note_type_customization import NoteTypeCustomization, SubjectiveSource
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
        "by the doctor). The note must NOT refer to the patient herself as "
        "'they' or 'them' (using 'they' to refer to symptoms, conditions, or "
        "other plurals such as headaches is fine -- only patient-individual "
        "references count). The note must NOT avoid pronouns entirely by "
        "always saying 'the patient' -- a natural clinical note about this "
        "transcript should use she/her at least once."
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


# Issue #33 item: in the Plan section, within each condition's bullets,
# the patient-actionable items (medications to take, lifestyle changes,
# self-monitoring) should appear before the doctor-actionable items
# (labs to order, referrals to send). Seeds a transcript with one
# condition (diabetes management) and a clear mix of both kinds of
# todos, asserts the ordering via eval_n_of_m.
def test_plan_bullets_sort_patient_before_doctor():
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
        duration=180,
        transcript_data=[
            TranscriptEntry(
                startTime=0,
                endTime=5000,
                speaker="Doctor",
                content="How have your blood sugars been running since our last visit?",
            ),
            TranscriptEntry(
                startTime=5000,
                endTime=15000,
                speaker="Patient",
                content="They've been creeping up. Mornings have been around 170 and after dinner sometimes hits the 230s. I've been pretty consistent with the metformin but I think dinner is what's tripping me up.",
            ),
            TranscriptEntry(
                startTime=15000,
                endTime=25000,
                speaker="Doctor",
                content="Okay, that's higher than where we want to be. I'm going to add a second medication -- empagliflozin, 10mg once daily in the morning. Start that tomorrow.",
            ),
            TranscriptEntry(
                startTime=25000,
                endTime=33000,
                speaker="Doctor",
                content="I also want you to check your blood sugar twice a day, once fasting and once two hours after dinner, and keep a log so we can review it next time.",
            ),
            TranscriptEntry(
                startTime=33000,
                endTime=42000,
                speaker="Doctor",
                content="And try to avoid late-evening meals -- ideally finish dinner by seven so your body has time to bring the numbers down before bed.",
            ),
            TranscriptEntry(
                startTime=42000,
                endTime=50000,
                speaker="Doctor",
                content="On my end I'll order an HbA1c lab so we have a current number, and I'll send a referral over to nutrition counseling so they can help with meal planning.",
            ),
            TranscriptEntry(
                startTime=50000,
                endTime=55000,
                speaker="Patient",
                content="Sounds good, I'll start the new medication tomorrow.",
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
        "In the 'Plan' section of this clinical note, the patient-actionable "
        "bullets -- items the patient must do themselves (taking the new "
        "medication empagliflozin daily, checking blood sugar twice a day, "
        "avoiding late-evening meals) -- must appear BEFORE the "
        "doctor-actionable bullets -- items the doctor will do (ordering the "
        "HbA1c lab, sending the nutrition referral). The bullets do not need "
        "to be labelled as patient or doctor; the test is about their reading "
        "order in the Plan section. If a doctor-actionable bullet appears "
        "above any patient-actionable bullet, the test fails."
    )

    eval_n_of_m(call_llm=create_clinical_note, criteria=criteria, n=1, m=2)


# Issue #33 item: the Medications section should distinguish current
# medications (what the patient is already taking) from newly-prescribed
# medications (what the doctor adds at this visit). Seeds a transcript
# where the patient lists two current meds at intake and the doctor
# prescribes a third in response to new heartburn complaints; asserts
# the Medications section tags omeprazole inline as newly prescribed.
def test_medications_section_distinguishes_current_from_prescribed():
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

    transcript = AIVisitTranscript(
        doctor_id=appt.doctor_id,
        appointment_id=appt.id,
        patient_id=patient.id,
        duration=150,
        transcript_data=[
            TranscriptEntry(
                startTime=0,
                endTime=4000,
                speaker="Doctor",
                content="Before we get started, can you remind me what medications you're currently taking?",
            ),
            TranscriptEntry(
                startTime=4000,
                endTime=14000,
                speaker="Patient",
                content="Sure, I take lisinopril 10mg once a day in the morning, and atorvastatin 20mg at bedtime. That's it.",
            ),
            TranscriptEntry(
                startTime=14000,
                endTime=20000,
                speaker="Doctor",
                content="Great. And what brings you in today?",
            ),
            TranscriptEntry(
                startTime=20000,
                endTime=32000,
                speaker="Patient",
                content="I've been getting a lot of heartburn lately, especially at night. It's been going on for about a month now, maybe two or three times a week.",
            ),
            TranscriptEntry(
                startTime=32000,
                endTime=40000,
                speaker="Doctor",
                content="Any difficulty swallowing, weight loss, or blood in the stool?",
            ),
            TranscriptEntry(
                startTime=40000,
                endTime=46000,
                speaker="Patient",
                content="No, none of those. Just the heartburn.",
            ),
            TranscriptEntry(
                startTime=46000,
                endTime=58000,
                speaker="Doctor",
                content="Let's start you on omeprazole, 20mg once daily about thirty minutes before breakfast, for the next two months. We'll see if that takes care of it.",
            ),
            TranscriptEntry(
                startTime=58000,
                endTime=62000,
                speaker="Patient",
                content="Sounds good, thank you.",
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
        "The 'Medications' section of this clinical note must (a) list "
        "lisinopril and atorvastatin as the patient's current medications, "
        "and (b) list omeprazole AND clearly tag it as newly prescribed at "
        "this visit. Acceptable inline labels for omeprazole include "
        "'(new this visit)', '(NEW)', '(prescribed today)', "
        "'(initiated today)', '(started today)', or any similar phrase that "
        "tells the reader this med was added at this visit. If the "
        "Medications section omits omeprazole, or includes omeprazole but "
        "does NOT distinguish it from the current medications via an inline "
        "label, the test fails."
    )

    eval_n_of_m(call_llm=create_clinical_note, criteria=criteria, n=1, m=2)


# Issue #33 item: every Plan bullet must include a stated reason -- the
# "why" of the action -- using "for X" or "to X" phrasing. Seeds a
# lumbar-strain transcript with four distinct actions (ibuprofen, heat
# application, lifting restriction, MRI order), each of which should
# pick up an explicit reason.
def test_plan_bullets_include_reason():
    patient = Patient.where(Patient.name == "Ghengis Khan").one()
    assert patient is not None

    appt = Appointment.where(Appointment.patient_id == patient.id).first()
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
                endTime=4000,
                speaker="Doctor",
                content="What's been bothering you?",
            ),
            TranscriptEntry(
                startTime=4000,
                endTime=14000,
                speaker="Patient",
                content="I've had really bad lower back pain for about two weeks now. It started after I helped a friend move some furniture.",
            ),
            TranscriptEntry(
                startTime=14000,
                endTime=22000,
                speaker="Doctor",
                content="Any numbness, tingling, or weakness in the legs? Any change in bowel or bladder function?",
            ),
            TranscriptEntry(
                startTime=22000,
                endTime=28000,
                speaker="Patient",
                content="No, none of that. Just the pain in my lower back, kind of a deep ache.",
            ),
            TranscriptEntry(
                startTime=28000,
                endTime=40000,
                speaker="Doctor",
                content="Okay, that sounds most consistent with a lumbar strain from the lifting. Let me give you a few things. Take ibuprofen 600mg three times a day with food.",
            ),
            TranscriptEntry(
                startTime=40000,
                endTime=50000,
                speaker="Doctor",
                content="Apply a heating pad to the area for about 20 minutes, two or three times a day.",
            ),
            TranscriptEntry(
                startTime=50000,
                endTime=58000,
                speaker="Doctor",
                content="And no heavy lifting at all for the next two weeks. I really mean it.",
            ),
            TranscriptEntry(
                startTime=58000,
                endTime=70000,
                speaker="Doctor",
                content="I'm also going to order a lumbar MRI just to be safe -- given how long the pain has been going on, I want to make sure there isn't a disc issue we're missing.",
            ),
            TranscriptEntry(
                startTime=70000,
                endTime=74000,
                speaker="Patient",
                content="Okay, that all makes sense.",
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
        "Every bullet in the 'Plan' section of this clinical note must "
        "include an explicit reason for the action -- a 'for X' or 'to X' "
        "clause explaining the purpose. Acceptable: 'Order MRI to rule out "
        "disc pathology', 'Take ibuprofen 600mg three times a day for pain "
        "control', 'Avoid heavy lifting to prevent re-injury'. UNACCEPTABLE: "
        "bullets that state only the action with no reason, e.g. 'Order MRI', "
        "'Take ibuprofen 600mg three times a day', or 'Apply heating pad for "
        "20 minutes'. If ANY Plan bullet lacks a stated reason -- including "
        "common-sense actions like 'follow up in 2 weeks' which must say "
        "what the follow-up is FOR -- the test fails. Reasons must be "
        "clinically grounded (related to the patient's complaint or the "
        "standard indication for the action), not invented."
    )

    eval_n_of_m(call_llm=create_clinical_note, criteria=criteria, n=1, m=2)


# Issue #33 item: when the schema includes a Review of Systems field,
# ROS must contain the doctor's system-by-system findings from
# targeted questions, NOT a paraphrase of Subjective. Constructs an
# in-memory DoctorNoteSchema (not saved) with a Review of Systems
# field, seeds a transcript where the patient describes a cough +
# fever and the doctor performs a multi-system ROS survey, and
# asserts the two sections do not duplicate.
def test_review_of_systems_distinct_from_subjective():
    patient = Patient.where(Patient.name == "Samwise Gamgee").one()
    assert patient is not None

    appt = Appointment.where(Appointment.patient_id == patient.id).one()
    assert appt is not None

    # In-memory schema with a Review of Systems field. Not saved --
    # generate_clinical_note only reads .fields and (via doctor_id)
    # the doctor's biography.
    schema = DoctorNoteSchema(
        doctor_id=appt.doctor_id,
        note_type="Inline schema with ROS",
        fields={
            "CC": "Summarize the main reason for the patient's visit in one sentence.",
            "Subjective": "Document the patient's own description of their symptoms, concerns, and any relevant personal or family medical history.",
            "Review of Systems": "System-by-system findings from the doctor's targeted questions during the visit (positives and pertinent negatives). Should NOT repeat the patient's initial narrative from Subjective.",
            "Assessment": "List the primary diagnosis and any secondary diagnoses, along with the rationale for each.",
            "Plan": "Outline the proposed management plan, including treatments, medications, follow-up, and referrals.",
        },
    )

    transcript = AIVisitTranscript(
        doctor_id=appt.doctor_id,
        appointment_id=appt.id,
        patient_id=patient.id,
        duration=180,
        transcript_data=[
            TranscriptEntry(
                startTime=0,
                endTime=4000,
                speaker="Doctor",
                content="What brings you in today?",
            ),
            TranscriptEntry(
                startTime=4000,
                endTime=18000,
                speaker="Patient",
                content="I've had a dry cough for about three days now, and a low-grade fever -- I'd say around 100 degrees on and off. I just haven't been feeling like myself.",
            ),
            TranscriptEntry(
                startTime=18000,
                endTime=24000,
                speaker="Doctor",
                content="Any chest pain or shortness of breath?",
            ),
            TranscriptEntry(
                startTime=24000,
                endTime=32000,
                speaker="Patient",
                content="No chest pain. A little short of breath when I climb stairs, but nothing at rest.",
            ),
            TranscriptEntry(
                startTime=32000,
                endTime=38000,
                speaker="Doctor",
                content="Sore throat, runny nose, headache?",
            ),
            TranscriptEntry(
                startTime=38000,
                endTime=46000,
                speaker="Patient",
                content="My throat's a little scratchy, yes. No runny nose. No headache.",
            ),
            TranscriptEntry(
                startTime=46000,
                endTime=52000,
                speaker="Doctor",
                content="Any nausea, vomiting, or diarrhea?",
            ),
            TranscriptEntry(
                startTime=52000,
                endTime=58000,
                speaker="Patient",
                content="No, none of that. Appetite's a bit off but I'm keeping food down.",
            ),
            TranscriptEntry(
                startTime=58000,
                endTime=64000,
                speaker="Doctor",
                content="Any joint pain, rash, or new fatigue beyond what you mentioned?",
            ),
            TranscriptEntry(
                startTime=64000,
                endTime=72000,
                speaker="Patient",
                content="No joint pain, no rash. I'm tired but I figure that's the fever.",
            ),
        ],
    )

    def create_clinical_note() -> str:
        return json.dumps(
            generate_clinical_note(
                transcript=transcript,
                example_note=None,
                note_schema=schema,
            )
        )

    criteria = (
        "This clinical note's 'Review of Systems' section must contain "
        "the doctor's system-by-system findings from the targeted "
        "questions asked during the visit -- pertinent positives and "
        "negatives organised by body system (e.g. 'HEENT: mild sore "
        "throat, no headache', 'Respiratory: mild dyspnea on exertion', "
        "'GI: denies nausea, vomiting, diarrhea', 'Cardiovascular: no "
        "chest pain', 'Musculoskeletal: no joint pain', 'Skin: no rash'). "
        "The Review of Systems MUST NOT be a paraphrase or restatement "
        "of the Subjective section. The Subjective section should "
        "describe the patient's initial narrative (3-day dry cough, "
        "low-grade fever, general malaise). If the Review of Systems "
        "reads as a narrative summary of the patient's chief complaint "
        "rather than organised system-by-system findings from the "
        "doctor's survey, the test fails."
    )

    eval_n_of_m(call_llm=create_clinical_note, criteria=criteria, n=1, m=2)


# ---------------------------------------------------------------------------
# Per-config evals — verify NoteTypeCustomization is honoured by the LLM
# ---------------------------------------------------------------------------


def _soap_schema_and_transcript() -> tuple[DoctorNoteSchema, AIVisitTranscript]:
    """Return the seeded Test SOAP Note schema and transcript for Samwise Gamgee."""
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

    return schema, transcript


def test_require_plan_reason_default_keeps_reasons():
    schema, transcript = _soap_schema_and_transcript()
    customization = NoteTypeCustomization(
        doctor_note_schema_id=schema.id, require_plan_reason=True
    )

    def create():
        note = generate_clinical_note(
            transcript=transcript,
            example_note=None,
            note_schema=schema,
            customization=customization,
        )
        return json.dumps(note)

    eval_n_of_m(
        call_llm=create,
        criteria=(
            "Every bullet in the Plan section states a reason or purpose for the "
            "action (e.g. a 'to ...' or 'for ...' clause), not just the bare action."
        ),
        n=1,
        m=2,
    )


def test_subjective_source_patient_and_doctor_produces_coherent_note():
    schema, transcript = _soap_schema_and_transcript()
    customization = NoteTypeCustomization(
        doctor_note_schema_id=schema.id,
        subjective_source=SubjectiveSource.patient_and_doctor,
    )

    def create():
        note = generate_clinical_note(
            transcript=transcript,
            example_note=None,
            note_schema=schema,
            customization=customization,
        )
        return json.dumps(note)

    eval_n_of_m(
        call_llm=create,
        criteria="The note is a coherent clinical note with a populated Subjective section.",
        n=1,
        m=2,
    )
