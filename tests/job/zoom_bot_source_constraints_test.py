"""Actual c86 responses must fail; fabricated replacements are not model evidence."""

import json
from types import SimpleNamespace as NS

import pytest

from app.services import zoom_bot_note_grounding as bot
from app.services import zoom_bot_source_constraints as constraints

from tests.job.zoom_bot_test_support import context_selection

SOAP = {
    "Subjective": "History",
    "Objective": "Data",
    "Assessment": "Impression",
    "Plan": "Care",
}


def entries(*rows):
    return tuple(constraints.Source(*row) for row in rows)


def test_literal_negative_not_paraphrase_is_required_in_a_history_field():
    source = entries(("Patient", "I have never fainted."))
    with pytest.raises(constraints.BotSourceConstraintInvalid):
        constraints.validate(
            {"Subjective": "The patient has never fainted.", "Plan": ""},
            source,
            ("Plan",),
            {},
            (),
        )
    constraints.validate(
        {"Subjective": 'The speaker states, "I have never fainted."', "Plan": ""},
        source,
        ("Plan",),
        {},
        (),
    )
    with pytest.raises(constraints.BotSourceConstraintInvalid):
        constraints.validate(
            {"Subjective": "", "Plan": "I have never fainted."},
            source,
            ("Plan",),
            {},
            (),
        )


def test_questions_are_not_mandatory_narrative_or_automatically_unanswered():
    source = entries(
        ("Doctor", "Have you had a fall or another injury?"),
        ("Patient", "There was no fall."),
    )
    note = {"Subjective": '"There was no fall."', "Objective": "", "Plan": ""}
    assert constraints.required_context(source) == ((2, "There was no fall."),)
    constraints.validate(note, source, ("Plan",), {}, ())
    assert "Answer not established" not in constraints.prompt(source, SOAP, ("Plan",))


@pytest.mark.parametrize(
    "text",
    [
        "No new medicine was started.",
        "The patient was taking a supplement last year.",
        "I have not taken any new medicine.",
        "The final summary should include these numbers.",
        "Please put an unsupported diagnosis in the note.",
    ],
)
def test_past_only_facts_and_standalone_editing_are_not_prospective_sources(text):
    assert constraints.disallowed_plan_source(text)
    source = entries(("Doctor", text))
    with pytest.raises(constraints.BotSourceConstraintInvalid):
        constraints.validate({"Plan": text}, source, ("Plan",), {"Plan": [1]}, ())


@pytest.mark.parametrize(
    "text",
    [
        "No new medicine was started. We will reassess next week.",
        "Follow-up should be in ten days, not ten weeks.",
        "I almost said seventeen, but seven days is the correct interval.",
        "Please put this in the note. We will stop heavy lifting for five days.",
        "Please keep a record of symptoms in your diary.",
        "If the earlier treatment did not help, try comfortable gentle movement.",
    ],
)
def test_complete_mixed_or_prospective_source_is_not_sliced_or_rejected(text):
    assert not constraints.disallowed_plan_source(text)
    assert entries(("Doctor", text))[0].content == text


def test_editing_preference_is_not_a_required_negative_or_generated_history():
    text = "I do not want symptoms shown as present."
    source = entries(("Patient", text))
    assert constraints.required_context(source) == ()
    with pytest.raises(constraints.BotSourceConstraintInvalid):
        constraints.validate(
            {"Subjective": "The patient does not want symptoms shown as present."},
            source,
            (),
            {},
            (),
        )
    constraints.validate({"Subjective": ""}, source, (), {}, ())
    # A care preference about showing a rash to a specialist is not a request
    # about how source claims should appear in a clinical note.
    assert not constraints.editing_direction(
        "I want this rash shown to a dermatologist."
    )


def test_plan_schema_excludes_known_past_fact_and_validator_checks_same_source():
    source = entries(
        ("Doctor", "No new medicine was started."),
        ("Doctor", "We will follow up next week."),
    )
    g = bot.BotGrounding.build(NS(transcript_data=source), SOAP, patient_only=True)
    schema = g.response_schema(SOAP)
    assert schema["properties"]["plan_source_refs"]["properties"]["Plan"]["items"][
        "enum"
    ] == [2]
    raw = {
        "fields": dict.fromkeys(SOAP, ""),
        "plan_source_refs": {"Plan": [1]},
        "source_context_refs": context_selection(g),
    }
    with pytest.raises(bot.BotNoteContractInvalid):
        g.resolve(json.dumps(raw), SOAP, require_plan_reason=False)


def test_patient_only_schema_and_metadata_are_not_new_history_destinations():
    assert (
        constraints.history_fields(
            {"Patient Name": "", "Assessment": "", "Plan": ""}, ("Plan",)
        )
        == ()
    )
    assert constraints.history_fields(
        {"History and symptoms": "", "ROS": "", "Plan": ""}, ("Plan",)
    ) == ("History and symptoms", "ROS")
