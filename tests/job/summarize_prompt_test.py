from unittest.mock import patch

from app.jobs import summarize
from app.jobs.summarize import _instruction_block, build_structured_prompt
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.note_type_customization import (
    NoteTypeCustomization,
    PlanOrdering,
    PronounStyle,
    SubjectiveSource,
    default_customization,
)
from tests.factories import DoctorFactory

# Verbatim copy of the numbered instruction block from origin/master
# (lines 1–16, each indented 8 spaces, typos and double-spaces preserved).
# This constant pins byte-identity of the default rendered prompt.
ORIGINAL_DEFAULT_BLOCK = """\
        1. Keep in mind your BIO and the kind of visit that the patient is coming in for. This will determine what information is most important to include in your note.
        2. Carefully review the transcript and extract all relevant medical information.
        3. Organize the information into the appropriate sections of the clinical note schema.
          - Within each section, you should group information based on condition.
          - E.g. if the patient discusses diabetes and hypertension, and 3 steps in the plan relate to diabetes and 2 steps relate to hypertension, the plan should list the 3 diabetes steps, THEN the 2 hyptertension steps.
          - Sort based on importance of the condition. If hypertension is the patient's main concern, it should be listed first.
          - Be consistent with the order of conditions across fields. I.e. if anxiety is covered first in the plan, it should also be covered first in the assessment, subjective, and objective sections.
          - In the Plan section specifically, within each condition's bullets, list patient-actionable items (e.g. medications to take, lifestyle changes, symptoms to monitor, follow-ups the patient must initiate) before doctor-actionable items (e.g. labs to order, referrals to send, follow-up calls the doctor must place). This makes it easier for the patient to scan their own to-dos.
          - Do not create subsections within the fields for the conditions. Prefer "flat" lists of items unless absolutely necessary.
        4. Use clear, concise, and professional medical language.
        5. Avoid including any irrelevant personal details or small talk from the transcript.
        6. If certain information is not provided in the transcript, leave that section blank or write "Not mentioned in transcript" as appropriate. E.g. if the patient does not mention respiratory problems, do NOT assume that they do not have them.
        7. Ensure that all information in the clinical note is directly based on the content of the transcript. Do not add any speculative or assumed information.
        8. Pronouns: When the transcript reveals the patient's pronouns -- via self-identification, the doctor's use of "she/her" or "he/him" when referring to the patient, or other unambiguous context -- the note MUST use those pronouns when referring to the patient. Do not default to "the patient" everywhere when pronouns are clear from the transcript. Use "they/them" ONLY when no pronoun usage is evident anywhere in the transcript. This overrides any clinical convention of defaulting to "the patient" -- mix in the patient's actual pronouns when they are known, even if the example note's style differs on this point.
        9. Subjective section: source ONLY from lines spoken by the patient (lines beginning with "Patient:" in the transcript above). The doctor's interpretations, diagnostic impressions, follow-up questions, and clinical observations belong in the Assessment or Objective sections, never in Subjective. Phrasings like "the patient reports X" or "the patient denies Y" are acceptable as long as X and Y themselves came from a Patient: line.
        10. Medications section: when this section exists in the schema, list both medications the patient is already taking (from "Patient:" lines) AND medications the doctor is prescribing or initiating during this visit. Tag newly-prescribed items inline with "(new this visit)" so they can be distinguished from the patient's existing medication list at a glance. Do not put newly-prescribed meds only in the Plan section -- the Medications section should reflect the post-visit medication list as a whole, with new additions flagged.
        11. Plan section bullets MUST include the reason for each action -- the "why" -- using "for X" or "to X" phrasing. For example: "Order MRI to rule out disc pathology" rather than "Order MRI"; "Continue metformin for ongoing diabetes management" rather than just "Continue metformin"; "Avoid heavy lifting to prevent re-injury" rather than just "Avoid heavy lifting"; "Follow up in two weeks to reassess back pain and review MRI results" rather than just "Follow up in two weeks" or "Follow up if symptoms worsen". This applies to EVERY Plan bullet, no exceptions -- including follow-up bullets, monitoring bullets, and continue-current-treatment bullets. A trigger condition (e.g. "if symptoms worsen") is NOT a reason; the bullet must state the PURPOSE of the action (e.g. "to monitor for new neurological symptoms"). When the transcript does not explicitly state the reason, infer it from the clinical context (the patient's complaint, the assessment, the medication's indication). Do not invent reasons unrelated to the visit.
        12. Review of Systems (ROS) section: when the schema includes a "Review of Systems" field (or similarly-named system-by-system survey field like "ROS"), populate it with the doctor's system-by-system findings from targeted questions asked during the visit -- both positives and pertinent negatives, organised by body system (HEENT, Respiratory, Cardiovascular, GI, GU, Musculoskeletal, Neuro, Skin, Psych, Constitutional, etc.). When the schema has BOTH a Subjective AND a Review of Systems field, divide the patient information between them with NO overlap: Subjective gets the patient's chief complaint and initial narrative (what brought them in, in their own words); Review of Systems gets the responses to the doctor's follow-up "any chest pain? any nausea? any headaches?" survey. Subjective should not list the ROS pertinent negatives; those belong only in Review of Systems. If the doctor did not ask explicit ROS questions, write "Not performed" or "Deferred" in the ROS section; do not pad ROS by restating Subjective.
        13. Ensure all action items are included in the relevant sections of the note. But do not repeat yourself -- if a section already mentions the action item, don't mention it again.
        14. Imitate the style and conventions that you see in the example note.  But do NOT include any of the information from the example in the note you write. This is an example taken from a DIFFERENT patient.
        15. Respond only in JSON format with the schema above. JSON property values should be valid markdown. If you include a markdown list, each item in the list should be followed by a newline character.
        16. Take a deep breath and think step by step."""

# distinctive verbatim substrings, one per configurable #33 instruction
PRONOUN = "MUST use those pronouns"
SUBJECTIVE = "source ONLY from lines spoken by the patient"
NEW_MEDS = "(new this visit)"
PLAN_REASON = "MUST include the reason"
ROS = "Review of Systems (ROS) section"
PLAN_ORDER = "patient-actionable items"


def test_default_block_preserves_all_hash33_behaviors():
    block = _instruction_block(default_customization())
    for phrase in (PRONOUN, SUBJECTIVE, NEW_MEDS, PLAN_REASON, ROS, PLAN_ORDER):
        assert phrase in block, f"missing default phrase: {phrase}"
    # fixed house rules survive
    assert "Respond only in JSON format" in block
    assert "Take a deep breath and think step by step." in block
    # renumbered sequentially: default has exactly 16 instructions
    assert "\n16. " in block
    assert "\n17. " not in block


def test_require_plan_reason_off_removes_only_that_instruction():
    block = _instruction_block(NoteTypeCustomization(require_plan_reason=False))
    assert PLAN_REASON not in block
    assert SUBJECTIVE in block  # unrelated instruction untouched


def test_flag_new_medications_off_removes_meds_instruction():
    block = _instruction_block(NoteTypeCustomization(flag_new_medications=False))
    assert NEW_MEDS not in block


def test_plan_ordering_as_discussed_drops_patient_first_subbullet():
    block = _instruction_block(NoteTypeCustomization(plan_ordering=PlanOrdering.as_discussed))
    assert PLAN_ORDER not in block


def test_ros_off_removes_ros_instruction():
    block = _instruction_block(NoteTypeCustomization(ros_distinct_from_subjective=False))
    assert ROS not in block


def test_pronoun_neutral_changes_instruction():
    block = _instruction_block(NoteTypeCustomization(pronoun_style=PronounStyle.neutral))
    assert PRONOUN not in block
    assert "do not infer pronouns" in block


def test_subjective_patient_and_doctor_relaxes_restriction():
    block = _instruction_block(NoteTypeCustomization(subjective_source=SubjectiveSource.patient_and_doctor))
    assert SUBJECTIVE not in block


def test_custom_instructions_appended_to_full_prompt():
    doctor = DoctorFactory.build().save()
    schema = DoctorNoteSchema(
        doctor_id=doctor.id, note_type="SOAP", fields={"Subjective": "patient story"}
    ).save()
    transcript = AIVisitTranscript(
        doctor_id=doctor.id,
        appointment_id=None,
        patient_id=None,
        duration=60,
        transcript_data=[
            TranscriptEntry(speaker="Patient", content="My back hurts.", startTime=0, endTime=1)
        ],
    )
    c = NoteTypeCustomization(custom_instructions="Always use metric units.")
    with patch.object(summarize, "extract_action_items", return_value="none"):
        prompt = build_structured_prompt(transcript, None, schema, c)
    assert "Always use metric units." in prompt
    assert "Additional provider instructions:" in prompt


def test_generate_clinical_note_forwards_customization():
    doctor = DoctorFactory.build().save()
    schema = DoctorNoteSchema(
        doctor_id=doctor.id, note_type="SOAP", fields={"Subjective": "patient story"}
    ).save()
    transcript = AIVisitTranscript(
        doctor_id=doctor.id,
        appointment_id=None,
        patient_id=None,
        duration=60,
        transcript_data=[
            TranscriptEntry(speaker="Patient", content="My back hurts.", startTime=0, endTime=1)
        ],
    )
    c = NoteTypeCustomization(require_plan_reason=False)

    captured = {}

    def fake_build(t, e, s, customization=None):
        captured["customization"] = customization
        return "PROMPT"

    with patch.object(summarize, "build_structured_prompt", side_effect=fake_build), \
         patch.object(summarize.openai.chat.completions, "create") as create:
        create.return_value.choices = [
            type("M", (), {"message": type("X", (), {"content": "{}"})()})()
        ]
        summarize.generate_clinical_note(
            transcript=transcript, example_note=None, note_schema=schema, customization=c
        )

    assert captured["customization"] is c


def test_default_prompt_block_byte_identical_to_master():
    doctor = DoctorFactory.build().save()
    schema = DoctorNoteSchema(
        doctor_id=doctor.id, note_type="SOAP", fields={"Subjective": "x"}
    ).save()
    transcript = AIVisitTranscript(
        doctor_id=doctor.id,
        appointment_id=None,
        patient_id=None,
        duration=60,
        transcript_data=[
            TranscriptEntry(speaker="Patient", content="hi", startTime=0, endTime=1)
        ],
    )
    with patch.object(summarize, "extract_action_items", return_value="none"):
        prompt = build_structured_prompt(transcript, None, schema, None)
    assert ORIGINAL_DEFAULT_BLOCK in prompt
