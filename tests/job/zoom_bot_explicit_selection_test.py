"""Source identity, negative scope and question coverage are separate contracts."""

import json

import pytest
from app.services import zoom_bot_note_grounding as bot
from app.services import zoom_bot_source_constraints as constraints

from tests.job.zoom_bot_test_support import (
    SOAP,
    primary_selection,
    response,
    transcript,
)


@pytest.mark.parametrize(
    "source",
    [
        "We are not changing treatment because I have no fever.",
        "I have no fever; we will continue treatment.",
        "We will treat the rash because there is no fever.",
        "We are not changing treatment, but the patient has no fever.",
        "I have not taken any medicine, but we will start therapy.",
    ],
)
def test_each_historical_negative_survives_future_cues_in_same_source(source):
    entries = (constraints.Source("Unverified speaker (raw)", source),)
    assert not constraints.prospective_negative_only(source)
    assert constraints.required_context(entries) == ((1, source),)
    with pytest.raises(constraints.BotSourceConstraintInvalid):
        constraints.validate(
            {"Objective": "", "Plan": source}, entries, ("Plan",), {"Plan": [1]}, ()
        )
    constraints.validate(
        {"Objective": source, "Plan": source}, entries, ("Plan",), {"Plan": [1]}, ()
    )


def test_multiple_negative_decisions_need_their_own_local_care_cues():
    source = "We are not starting medicine; we are not changing treatment."
    assert constraints.prospective_negative_only(source)
    entries = (constraints.Source("Unverified speaker (raw)", source),)
    with pytest.raises(constraints.BotSourceConstraintInvalid):
        constraints.validate(
            {"Objective": "", "Plan": source}, entries, ("Plan",), {}, ()
        )
    constraints.validate(
        {"Objective": "", "Plan": source}, entries, ("Plan",), {"Plan": [1]}, ()
    )


def test_primary_history_retains_original_negative_without_prose_or_duplicate_quotes():
    source = transcript(
        ("Patient", "I have never fainted."),
        ("Patient", "The dizziness is mild."),
        ("Doctor", "Return next week."),
    )
    g = bot.BotGrounding.build(source, SOAP, patient_only=True)
    raw = json.loads(response(g, plans=[3]))
    raw["primary_history_source_kinds"] = primary_selection(g, [2])
    note = g.resolve(json.dumps(raw), SOAP, require_plan_reason=False)
    assert note["Subjective"].count("I have never fainted.") == 1
    assert "The dizziness is mild." in note["Subjective"]
    assert "I have never fainted." not in note["Objective"]
    assert "Return next week." not in note["Subjective"]
    for bad in (
        {"3": "clinical_history"},
        {"2": True},
        {"0": "clinical_history"},
        {"2": "invalid"},
    ):
        raw["primary_history_source_kinds"] = bad
        with pytest.raises(bot.BotNoteContractInvalid):
            g.resolve(json.dumps(raw), SOAP, require_plan_reason=False)
    raw["primary_history_source_kinds"] = primary_selection(g, [1, 2])
    raw["fields"]["Subjective"] = "A competing paraphrase."
    with pytest.raises(bot.BotNoteContractInvalid):
        g.resolve(json.dumps(raw), SOAP, require_plan_reason=False)


def test_primary_custom_history_and_patient_only_role_boundaries():
    fields = {"HPI": "History", "Plan": "Care"}
    g = bot.BotGrounding.build(
        transcript(
            ("Patient", "I have no fever."), ("Doctor", "Clinician observation.")
        ),
        fields,
        patient_only=True,
    )
    assert g.primary.target == "HPI" and g.primary.allowed == (1,)
    note = g.resolve(response(g, fields), fields, require_plan_reason=False)
    assert "I have no fever." in note["HPI"]
    both = bot.BotGrounding.build(
        transcript(
            ("Patient", "I have no fever."), ("Doctor", "Clinician observation.")
        ),
        fields,
        patient_only=False,
    )
    assert both.primary.allowed == (1, 2)


def test_every_question_is_structurally_reviewed_without_answer_status_claims():
    source = transcript(
        ("Doctor", "Any cough or wheeze?"),
        ("Patient", "No cough."),
        ("Doctor", "Any falls or other injury?"),
        ("Patient", "No falls."),
    )
    g = bot.BotGrounding.build(source, SOAP, patient_only=True)
    raw = json.loads(response(g, context=[1, 2, 3, 4]))
    note = g.resolve(json.dumps(raw), SOAP, require_plan_reason=False)
    assert "Any falls or other injury?" in note["Objective"]
    assert "No other injury" not in json.dumps(note) and "unanswered" not in json.dumps(
        note
    )
    assert json.dumps(note).count("No falls.") == 1
    for scopes in (
        [{"question_ref": 1, "source_refs": []}],
        [
            {"question_ref": 1, "source_refs": []},
            {"question_ref": 1, "source_refs": []},
        ],
        [
            {"question_ref": 1, "source_refs": [2]},
            {"question_ref": 3, "source_refs": []},
        ],
        [
            {"question_ref": 1, "source_refs": [True]},
            {"question_ref": 3, "source_refs": []},
        ],
    ):
        raw["source_context_refs"] = {
            "question_scopes": scopes,
            "other_source_refs": [],
        }
        with pytest.raises(bot.BotNoteContractInvalid):
            g.resolve(json.dumps(raw), SOAP, require_plan_reason=False)


def test_unknown_patient_only_subjective_is_empty_without_implementation_boilerplate():
    g = bot.BotGrounding.build(
        transcript(("Unverified speaker (raw)", "I have no fever.")),
        SOAP,
        patient_only=True,
    )
    raw = json.loads(response(g, histories=[1]))
    note = g.resolve(json.dumps(raw), SOAP, require_plan_reason=False)
    assert note["Subjective"] == "" and "I have no fever." in note["Objective"]
    raw["fields"]["Subjective"] = "No patient-labeled entries; preference prevents use."
    with pytest.raises(bot.BotNoteContractInvalid):
        g.resolve(json.dumps(raw), SOAP, require_plan_reason=False)
