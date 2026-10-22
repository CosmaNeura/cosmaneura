"""Historical wording stays compatible with the unchanged lexical safety gate."""

import json

import pytest

import app.services.session_prep_generator as generator
import app.services.session_prep_runtime as runtime

from tests.services.session_prep_generator_test import (
    _fake,
    _historical_selection,
    _neutral_historical_brief,
)

FIELDS = (
    "recap",
    "trajectory_and_material_changes",
    "talking_points",
    "open_threads",
    "cautions",
    "suggested_outline",
)
HISTORICAL_REJECTIONS = (
    "2026-10-07: a planned increase to the 50 mg dose was documented.",
    "2026-10-08: no increase to the 50 mg dose was implemented.",
    "Review whether the earlier increase to the 50 mg dose was implemented.",
    "2026-10-08: the planned decrease of the 50 mg dose was documented.",
    "2026-10-08: the prior stop of medication was documented.",
    "Discuss the documented start of medication.",
)


def _rejected(selection, text, field="recap"):
    prepared = _neutral_historical_brief(selection)
    if field == "recap":
        prepared.recap.text = text
    else:
        item = prepared.recap.model_copy(update={"text": text})
        setattr(prepared, field, [item])
    return prepared


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("text", HISTORICAL_REJECTIONS)
def test_nouns_negations_and_questions_do_not_bypass_medication_guard(field, text):
    selection = _historical_selection()
    with pytest.raises(generator.SessionPrepGenerationError) as caught:
        generator.validate_generated_brief(_rejected(selection, text, field), selection)
    assert caught.value.safety_categories == ("medication_action",)
    assert caught.value.failure_code == "output_safety_04"


@pytest.mark.parametrize("text", HISTORICAL_REJECTIONS)
def test_fresh_neutral_history_retains_dates_doses_and_corrections(monkeypatch, text):
    selection = _historical_selection()
    expected = _neutral_historical_brief(selection)
    rejected = _rejected(selection, text + " private-rejected-wording-sentinel")
    calls = _fake(
        monkeypatch,
        None,
        responses=[
            (rejected.model_dump_json(), "stop", None),
            (expected.model_dump_json(), "stop", None),
        ],
    )
    result = generator.generate_prepared_brief(selection)
    assert result == expected
    generator.validate_generated_brief(result, selection)
    first, second = calls["requests"]
    assert first["messages"][1] == second["messages"][1]
    source_payload = json.loads(first["messages"][1]["content"])
    assert [item["sections"] for item in source_payload["reviewed_notes"]] == [
        source.content for source in selection.sources
    ]
    assert result.recap.source_refs == [
        source.source_ref for source in selection.sources[:2]
    ]
    history = result.trajectory_and_material_changes[0]
    assert history.source_refs == [
        source.source_ref for source in selection.sources[1:]
    ]
    assert "2026-10-07" in history.text and "2026-10-08" in history.text
    assert "50 mg instead of 25 mg" in history.text
    assert "use of 50 mg was not confirmed" in history.text
    assert "superseded by the documented 25 mg daily plan" in history.text
    assert "documented use of losartan 25 mg daily" in result.recap.text
    assert "2026-09-30: office blood pressure 148/92" in result.recap.text
    # This exercises what both model requests receive, not whether a live model
    # will comply. No unsafe draft or matched source phrase is retransmitted.
    for call in calls["requests"]:
        prompt = call["messages"][0]["content"]
        assert generator._MEDICATION_HISTORY_INSTRUCTIONS in prompt
        assert "nouns, historical" in prompt
        assert "negated statements, quotations, and discussion questions" in prompt
        assert all(field in prompt for field in FIELDS)
    assert "private-rejected-wording-sentinel" not in json.dumps(calls["requests"])
    assert len(calls["requests"]) == 2


@pytest.mark.parametrize("text", HISTORICAL_REJECTIONS)
def test_second_historical_violation_is_rejected_without_rewriting_or_third_call(
    monkeypatch, text
):
    selection = _historical_selection()
    rejected = _rejected(selection, text)
    calls = _fake(monkeypatch, rejected.model_dump_json())
    with pytest.raises(generator.SessionPrepGenerationError) as caught:
        generator.generate_prepared_brief(selection)
    assert caught.value.failure_code == "output_safety_04"
    assert len(calls["requests"]) == 2
    assert calls["requests"][0]["messages"][1] == calls["requests"][1]["messages"][1]
    # The guard remains equally strict for generators injected into the worker.
    with pytest.raises(generator.SessionPrepGenerationError):
        generator.validate_generated_brief(rejected, selection)


def test_v6_digest_differs_from_v5_for_the_exact_same_reviewed_sources(monkeypatch):
    selection = _historical_selection()
    assert generator.PROMPT_VERSION == runtime.PROMPT_VERSION == "source_only_prep_v6"
    manifest = runtime.source_manifest(selection)
    current = runtime.source_digest(selection)
    monkeypatch.setattr(runtime, "PROMPT_VERSION", "source_only_prep_v5")
    assert runtime.source_manifest(selection) == manifest
    assert runtime.source_digest(selection) != current
