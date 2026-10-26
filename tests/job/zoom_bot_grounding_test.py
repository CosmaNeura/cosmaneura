"""Source-word bounds; purpose/selection correctness is a separate clinical gate."""

import json
from types import SimpleNamespace

import pytest

from app.jobs import summarize
from app.services import zoom_bot_note_grounding as bot
from app.services import zoom_bot_source_dispositions as purpose

from app.models.note_type_customization import NoteTypeCustomization

from tests.job.zoom_bot_test_support import inputs, model_value, row


@pytest.fixture
def grounded_case():
    transcript, schema, example, _ = inputs()
    transcript.transcript_data = [
        SimpleNamespace(speaker="Doctor", content="We can consider a call tomorrow."),
        SimpleNamespace(
            speaker="Unknown", content="No, I cannot do that. Should I call later?"
        ),
        SimpleNamespace(
            speaker="Patient",
            content="Correction: I can call in two days, not tomorrow.",
        ),
        SimpleNamespace(
            speaker="Doctor", content="The final note should retain earlier details."
        ),
        SimpleNamespace(
            speaker="Doctor", content="My impression is a mild muscle strain."
        ),
        SimpleNamespace(speaker="Patient", content="The discomfort is mild today."),
    ]
    return (
        transcript,
        schema,
        example,
        {
            bot.KEY: [
                row(1, "care_plan", "Plan"),
                row(2, "clinical_question", "Objective", [3], "partial"),
                row(3, "contextual_correction", "Plan", [1]),
                row(4, "nonclinical", None),
                row(5, "clinical_impression", "Assessment"),
                row(6, "reported_history", "Subjective"),
            ]
        },
    )


def generate(monkeypatch, case, raw=None, finish_reason="stop", custom=None):
    transcript, schema, example, value = case
    calls = []

    def create(**kw):
        calls.append(kw)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason=finish_reason,
                    message=SimpleNamespace(
                        content=raw
                        if raw is not None
                        else json.dumps(model_value(value))
                    ),
                )
            ]
        )

    monkeypatch.setattr(summarize.openai.chat.completions, "create", create)
    result = bot.generate_grounded_note(transcript, example, schema, custom)
    assert len(calls) == 1
    return result


def test_exact_sources_preserve_question_context_and_exclude_instruction(
    monkeypatch, grounded_case
):
    transcript, _, _, value = grounded_case
    value[bot.KEY].reverse()
    result = generate(monkeypatch, grounded_case)
    assert result["Subjective"].endswith(transcript.transcript_data[5].content)
    assert result["Assessment"].endswith(transcript.transcript_data[4].content)
    assert "final note" not in "\n".join(result.values())
    assert "Should I call later?" in result["Objective"]
    assert "[source 2] unidentified speaker" in result["Objective"]
    assert purpose.CONTEXT_REVIEW in result["Objective"]
    assert result["Objective"].index("[source 2]") < result["Objective"].index(
        "[source 3]"
    )
    assert result["Plan"].index("[source 1]") < result["Plan"].index("[source 3]")
    for index in [1, 3]:
        assert transcript.transcript_data[index - 1].content in result["Plan"]
    assert bot.REASON_REVIEW in result["Plan"]


@pytest.mark.parametrize(
    "change",
    ["generated_fields", "extra", "missing", "nonarray", "oversize", "old_envelope"],
)
def test_no_generated_field_or_malformed_envelope_is_accepted(
    monkeypatch, grounded_case, change
):
    value = grounded_case[3]
    if change == "generated_fields":
        value["fields"] = {"Assessment": "No serious disease."}
    elif change == "extra":
        value[bot.KEY][0]["invented"] = "safe"
    elif change == "missing":
        value[bot.KEY].pop()
    elif change == "nonarray":
        value[bot.KEY] = "No other injury."
    elif change == "oversize":
        value["padding"] = "x" * 262145
    else:
        value.clear()
        value["field_source_refs"] = {"Assessment": [1]}
    with pytest.raises(bot.BotNoteContractInvalid):
        generate(monkeypatch, grounded_case, json.dumps(value))


def test_duplicate_keys_and_truncated_completion_fail(monkeypatch, grounded_case):
    raw = json.dumps(model_value(grounded_case[3])).replace(
        '"source_index": 1', '"source_index": 1, "source_index": 1'
    )
    with pytest.raises(bot.BotNoteContractInvalid):
        generate(monkeypatch, grounded_case, raw)
    with pytest.raises(bot.BotNoteContractInvalid):
        generate(monkeypatch, grounded_case, finish_reason="length")


def test_custom_heading_keeps_exact_key_and_question_words(monkeypatch, grounded_case):
    t, s, e, _ = grounded_case
    t.transcript_data = [t.transcript_data[1]]
    s.fields = {"Questions for review": "Outstanding history or care questions"}
    value = {
        bot.KEY: [
            row(1, "clinical_question", "Questions for review", scope="unanswered")
        ]
    }
    result = generate(monkeypatch, (t, s, e, value))
    assert list(result) == ["Questions for review"]
    assert result["Questions for review"].endswith(t.transcript_data[0].content)
    assert purpose.CONTEXT_REVIEW in result["Questions for review"]


@pytest.mark.parametrize("content", ["x" * 262145, "bad\x00source", "bad\ud800source"])
def test_source_expansion_unicode_and_nul_are_rejected(
    monkeypatch, grounded_case, content
):
    grounded_case[0].transcript_data[0].content = content
    with pytest.raises(bot.BotNoteContractInvalid):
        generate(monkeypatch, grounded_case)


def test_reason_preference_cannot_change_source_words(monkeypatch, grounded_case):
    result = generate(
        monkeypatch,
        grounded_case,
        custom=NoteTypeCustomization(require_plan_reason=False),
    )
    assert bot.REASON_REVIEW not in result["Plan"]
    assert grounded_case[0].transcript_data[0].content in result["Plan"]
    assert grounded_case[0].transcript_data[2].content in result["Plan"]


def test_identical_words_and_unknown_roles_cannot_collapse_people(
    monkeypatch, grounded_case
):
    t, s, e, _ = grounded_case
    t.transcript_data = [
        SimpleNamespace(speaker="Unknown", content="My shoulder hurts.")
        for _ in range(2)
    ]
    value = {
        bot.KEY: [
            row(2, "reported_history", "Objective"),
            row(1, "reported_history", "Objective"),
        ]
    }
    result = generate(monkeypatch, (t, s, e, value))
    assert result["Objective"].count("My shoulder hurts.") == 2
    assert result["Objective"].count("unidentified speaker") == 2
    assert result["Objective"].index("[source 1]") < result["Objective"].index(
        "[source 2]"
    )
    assert (
        result["Assessment"]
        == "Review required: no source-stated clinical impression was selected."
    )
    assert (
        result["Subjective"] == "Review the transcript before completing this section."
    )


@pytest.mark.parametrize("content", ["bad\x00source", "bad\ud800source"])
def test_invalid_source_is_terminal_before_model_dispatch(
    monkeypatch, grounded_case, content
):
    grounded_case[0].transcript_data[0].content = content
    monkeypatch.setattr(
        summarize.openai.chat.completions,
        "create",
        lambda **_: pytest.fail("model dispatched"),
    )
    with pytest.raises(bot.BotNoteContractInvalid):
        transcript, schema, example = grounded_case[:3]
        bot.generate_grounded_note(transcript, example, schema, None)
