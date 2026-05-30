from app.models.note_type_customization import (
    NoteTypeCustomization,
    PlanOrdering,
    PronounStyle,
    SubjectiveSource,
    default_customization,
)


def test_defaults_match_current_behavior():
    c = default_customization()
    assert c.subjective_source == SubjectiveSource.patient_only
    assert c.pronoun_style == PronounStyle.infer_from_transcript
    assert c.plan_ordering == PlanOrdering.patient_then_doctor
    assert c.flag_new_medications is True
    assert c.require_plan_reason is True
    assert c.ros_distinct_from_subjective is True
    assert c.custom_instructions is None
