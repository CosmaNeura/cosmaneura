"""Generation restrictions complement, and never replace, source validation."""

import copy
import json
from types import SimpleNamespace

import pytest
from openai.lib._pydantic import _ensure_strict_json_schema

from app.jobs import summarize
from app.services import zoom_bot_note_grounding as bot

from app.models.note_type_customization import NoteTypeCustomization

from tests.job import zoom_bot_grounding_test as grounding_tests
from tests.job.zoom_bot_grounding_test import generate, model_value, row
from tests.job.zoom_bot_test_support import inputs


@pytest.fixture
def grounded_case():
    return grounding_tests.grounded_case.__wrapped__()


@pytest.mark.parametrize("empty", ["sources", "fields"])
def test_empty_generation_scope_fails_before_model_dispatch(monkeypatch, empty):
    transcript, schema, example, _ = inputs()
    if empty == "sources":
        transcript.transcript_data = []
    else:
        schema.fields = {}
    monkeypatch.setattr(
        summarize.openai.chat.completions,
        "create",
        lambda **_: pytest.fail("empty scope dispatched a model call"),
    )
    with pytest.raises(bot.BotNoteContractInvalid):
        bot.generate_grounded_note(transcript, example, schema, None)


@pytest.mark.parametrize("patient_only", [True, False])
def test_actual_request_uses_current_source_roles_and_supported_sdk_schema_shape(
    monkeypatch, grounded_case, patient_only
):
    transcript, schema, example, value = grounded_case
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content=json.dumps(model_value(value))),
                )
            ]
        )

    monkeypatch.setattr(summarize.openai.chat.completions, "create", create)
    customization = NoteTypeCustomization(
        subjective_source="patient_only" if patient_only else "patient_and_doctor"
    )
    bot.generate_grounded_note(transcript, example, schema, customization)
    assert len(requests) == 1
    actual = requests[0]["response_format"]["json_schema"]["schema"]
    assert actual == bot.selection_schema(
        schema.fields,
        [e.speaker for e in transcript.transcript_data],
        patient_only=patient_only,
    )
    strict = copy.deepcopy(actual)
    assert _ensure_strict_json_schema(strict, path=(), root=strict) == actual
    # This checks installed SDK handling, not Azure endpoint acceptance.


def test_complete_corrections_keep_their_own_words_without_invented_external_anchors(
    monkeypatch, grounded_case
):
    transcript, _, _, value = grounded_case
    transcript.transcript_data = [
        SimpleNamespace(
            speaker="Patient", content="It happened three times, not eight."
        ),
        SimpleNamespace(
            speaker="Patient",
            content="The home reading was 115 over 75, not 155 over 75.",
        ),
        SimpleNamespace(speaker="Doctor", content="Return in six days, not six weeks."),
    ]
    value[bot.KEY] = [
        row(1, "reported_history", "Subjective"),
        row(2, "reported_history", "Subjective"),
        row(3, "care_plan", "Plan"),
    ]
    original = [e.content for e in transcript.transcript_data]
    result = generate(monkeypatch, grounded_case)
    assert all(text in "\n".join(result.values()) for text in original)
    assert [e.content for e in transcript.transcript_data] == original
    assert (
        result["Assessment"]
        == "Review required: no source-stated clinical impression was selected."
    )


@pytest.mark.parametrize("bad", ["role", "answered_field", "history_plan_anchor"])
def test_schema_bypassing_model_response_still_reaches_unchanged_validator(
    monkeypatch, grounded_case, bad
):
    _, _, _, value = grounded_case
    if bad == "role":
        value[bot.KEY][0] = row(1, "reported_history", "Subjective")
    elif bad == "answered_field":
        value[bot.KEY][1] = row(
            2, "clinical_question", "Objective", [3], "fully_answered"
        )
    else:
        value[bot.KEY][0] = row(1, "reported_history", "Objective")
    with pytest.raises(bot.BotNoteContractInvalid):
        generate(monkeypatch, grounded_case)
