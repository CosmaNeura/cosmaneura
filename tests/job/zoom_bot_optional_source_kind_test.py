"""Optional source selection is distinct from mandatory facts and rendering."""

import json

import pytest
from app.services import zoom_bot_note_grounding as bot
from app.services import zoom_bot_source_constraints as constraints
from app.services.zoom_bot_primary_history import KEY

from tests.job.zoom_bot_test_support import SOAP, response, transcript


def test_optional_source_kinds_do_not_reclassify_or_remove_mandatory_negatives():
    g = bot.BotGrounding.build(
        transcript(
            ("Patient", "I have no cough."),
            ("Patient", "The discomfort began yesterday."),
            ("Patient", "Please adjust the microphone."),
            ("Patient", "I will keep a diary."),
        ),
        SOAP,
        patient_only=True,
    )
    assert g.primary.mandatory == (1,)
    assert g.primary.optional == (2, 3, 4)
    raw = json.loads(response(g, plans=[4]))
    raw[KEY] = {"2": "clinical_history", "3": "nonclinical", "4": "care_plan"}
    note = g.resolve(json.dumps(raw), SOAP, require_plan_reason=False)
    assert "I have no cough." in note["Subjective"]
    assert "The discomfort began yesterday." in note["Subjective"]
    assert "Please adjust the microphone." not in json.dumps(note)
    assert (
        "I will keep a diary." in note["Plan"]
        and "I will keep a diary." not in note["Subjective"]
    )
    schema = g.response_schema(SOAP)["properties"][KEY]
    assert set(schema["properties"]) == {"2", "3", "4"}
    assert "1" not in schema["properties"]
    for bad in (
        {"2": "clinical_history", "3": "nonclinical"},
        {"1": "nonclinical", **raw[KEY]},
        {**raw[KEY], "3": "invented_kind"},
        {**raw[KEY], "3": True},
    ):
        invalid = {**raw, KEY: bad}
        with pytest.raises(bot.BotNoteContractInvalid):
            g.resolve(json.dumps(invalid), SOAP, require_plan_reason=False)
    # Kind validity does not prove the clinical correctness of model selection.
    raw[KEY] = dict.fromkeys(raw[KEY], "nonclinical")
    incomplete = g.resolve(json.dumps(raw), SOAP, require_plan_reason=False)
    assert "I have no cough." in incomplete["Subjective"]
    assert "The discomfort began yesterday." not in incomplete["Subjective"]


@pytest.mark.parametrize(
    "source",
    [
        "Plan: two days without driving.",
        "I understand the plan: three weeks without swimming.",
        "We agree to this plan: ten days without competitive sports.",
    ],
)
def test_without_activity_requires_explicit_prospective_plan_and_valid_selection(
    source,
):
    assert constraints.prospective_negative_only(source)
    entries = (constraints.Source("Patient", source),)
    constraints.validate(
        {"Subjective": "", "Plan": source}, entries, ("Plan",), {"Plan": [1]}, ()
    )
    with pytest.raises(constraints.BotSourceConstraintInvalid):
        constraints.validate(
            {"Subjective": "", "Plan": source}, entries, ("Plan",), {}, ()
        )


@pytest.mark.parametrize(
    "source",
    [
        "I spent three weeks without swimming.",
        "Plan: two days without fever.",
        "I understand the plan: three weeks without swimming because I have no fever.",
        "Plan: two days without driving; I have no cough.",
        "Plan: three days without exercise, and I have no fever.",
    ],
)
def test_without_activity_cannot_waive_historical_or_symptom_negatives(source):
    assert not constraints.prospective_negative_only(source)
    entries = (constraints.Source("Patient", source),)
    assert constraints.required_context(entries) == ((1, source),)
    with pytest.raises(constraints.BotSourceConstraintInvalid):
        constraints.validate(
            {"Subjective": "", "Plan": source}, entries, ("Plan",), {"Plan": [1]}, ()
        )


@pytest.mark.parametrize(
    "heading",
    [
        "HPI and plan",
        "History of present illness / recommendations",
        "Subjective follow-up",
    ],
)
def test_combined_channel_is_disjoint_and_final_output_has_prospective_provenance(
    heading,
):
    fields = {heading: "Combined information", "Objective": "Reported data"}
    g = bot.BotGrounding.build(
        transcript(
            ("Unverified speaker (raw)", "I have no fever."),
            ("Unverified speaker (raw)", "Return next week."),
        ),
        fields,
        patient_only=True,
    )
    raw = json.loads(response(g, fields, plans=[2], histories=[1]))
    note = g.resolve(json.dumps(raw), fields, require_plan_reason=False)
    assert (
        note[heading]
        == "Prospective source context:\n- Unidentified speaker: Return next week."
    )
    assert (
        "I have no fever." not in note[heading]
        and "I have no fever." in note["Objective"]
    )
    assert g.combined_prospective_fields == (heading,)
    assert heading not in g.empty_subjective and heading not in g.history.fields
    assert g.primary.target != heading and g.context.target != heading
    for prose in ["The patient denies fever.", "Doctor advised return next week."]:
        raw["fields"][heading] = prose
        with pytest.raises(bot.BotNoteContractInvalid):
            g.resolve(json.dumps(raw), fields, require_plan_reason=False)
    schema = g.response_schema(fields)
    assert schema["properties"]["fields"]["properties"][heading]["enum"] == [""]
    assert (
        "prospective channel"
        in schema["properties"]["plan_source_refs"]["properties"][heading][
            "description"
        ]
    )
