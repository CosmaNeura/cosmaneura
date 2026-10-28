"""Exact source placement and identity must survive compact rendering."""

import json
from uuid import UUID

import pytest

from app.services import scribe_capture_history_grounding as history
from app.services import zoom_bot_note_grounding as bot
from app.services.scribe_capture_plan_grounding import PlanSource

from tests.job.zoom_bot_test_support import (
    SOAP,
    primary_selection,
    response,
    transcript,
)


def test_optional_plan_source_is_not_duplicated_but_historical_negative_stays():
    source = transcript(
        ("Patient", "I have no fever."),
        ("Patient", "I will keep a symptom diary."),
        ("Patient", "I have not used medicine; we will reassess next week."),
    )
    g = bot.BotGrounding.build(source, SOAP, patient_only=True)
    raw = json.loads(response(g, plans=[2, 3], context=[2]))
    raw["primary_history_source_kinds"] = primary_selection(g, [1, 2, 3])
    note = g.resolve(json.dumps(raw), SOAP, require_plan_reason=False)
    assert "I will keep a symptom diary." not in note["Subjective"]
    assert "I will keep a symptom diary." not in note["Objective"]
    assert "I will keep a symptom diary." in note["Plan"]
    assert "I have not used medicine; we will reassess next week." in note["Subjective"]
    assert "I have not used medicine; we will reassess next week." in note["Plan"]


def test_equal_words_in_prose_do_not_prove_same_source_identity():
    g = bot.BotGrounding.build(
        transcript(("Patient", "I feel better."), ("Doctor", "I feel better.")),
        SOAP,
        patient_only=True,
    )
    note = g.resolve(
        response(
            g,
            context=[2],
            prose={**dict.fromkeys(SOAP, ""), "Subjective": "I feel better."},
        ),
        SOAP,
        require_plan_reason=False,
    )
    assert "I feel better." in note["Subjective"]
    assert "Model-labeled clinician: I feel better." in note["Objective"]


def test_unknown_context_has_one_heading_and_retains_exact_selected_words():
    g = bot.BotGrounding.build(
        transcript(
            ("Unverified speaker (raw)", "I have no fever."),
            ("Unverified speaker (raw)", "Any other concern?"),
        ),
        SOAP,
        patient_only=True,
    )
    note = g.resolve(
        response(g, histories=[1], context=[2]), SOAP, require_plan_reason=False
    )
    assert note["Objective"].count(history.CONTEXT_HEADING) == 1
    assert note["Objective"].count("I have no fever.") == 1
    assert note["Objective"].count("Any other concern?") == 1


def test_grouping_preserves_source_ids_order_and_multiline_text():
    from app.services.zoom_bot_source_rendering import render_sources

    texts = ("An exact first statement.\nAnother line.", "An exact second statement.")
    sources = tuple(
        PlanSource("transcript", "Transcript — Unidentified speaker", text)
        for text in texts
    )
    same = (("raw", UUID(int=1)), ("raw", UUID(int=1)))
    grouped = render_sources([1, 2], sources, same)
    assert grouped.count("Unidentified speaker") == 1
    assert all(text in grouped for text in texts)
    assert grouped.index(texts[0]) < grouped.index(texts[1])
    distinct = (("raw", UUID(int=1)), ("raw", UUID(int=2)))
    assert render_sources([1, 2], sources, distinct).count("Unidentified speaker") == 2
    assert render_sources([1, 2], sources, ()).count("Unidentified speaker") == 2
    labels = (("raw A", None), ("raw B", None))
    assert render_sources([1, 2], sources, labels).count("Unidentified speaker") == 2


@pytest.mark.parametrize(
    "heading",
    [
        "Subjective",
        "HPI",
        "Chief complaint",
        "ChiefComplaint",
        "History of present illness",
    ],
)
def test_unknown_role_cannot_enter_patient_only_alias_by_prose_or_source_selection(
    heading,
):
    fields = {heading: "Patient history", "Objective": "Data", "Plan": "Care"}
    g = bot.BotGrounding.build(
        transcript(("Unverified speaker (raw)", "I have no fever.")),
        fields,
        patient_only=True,
    )
    assert heading in g.empty_subjective
    assert heading not in g.history.fields and g.context.target != heading
    raw = json.loads(response(g, fields, histories=[1]))
    note = g.resolve(json.dumps(raw), fields, require_plan_reason=False)
    assert note[heading] == "" and "I have no fever." in note["Objective"]
    raw["fields"][heading] = "The patient denies fever."
    with pytest.raises(bot.BotNoteContractInvalid):
        g.resolve(json.dumps(raw), fields, require_plan_reason=False)
