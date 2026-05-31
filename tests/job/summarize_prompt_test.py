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
