"""Source-word bounds; selection correctness is a separate clinical gate."""

import json
from types import SimpleNamespace

import pytest

from app.jobs import summarize
from app.services import zoom_bot_note_grounding as bot

from app.models.note_type_customization import NoteTypeCustomization

from tests.job.zoom_bot_fidelity_test import inputs


@pytest.fixture
def grounded_case():
    transcript, schema, example, _ = inputs()
    transcript.transcript_data = [
        SimpleNamespace(
            speaker="Doctor",
            content="Follow up in seven days; this is a recommendation.",
        ),
        SimpleNamespace(
            speaker="Unknown", content="No, I cannot do that. Should I call tomorrow?"
        ),
        SimpleNamespace(
            speaker="Patient",
            content="Correction: I can call in two days, not tomorrow.",
        ),
    ]
    return (
        transcript,
        schema,
        example,
        {
            bot.KEY: {
                "Subjective": [3],
                "Objective": [],
                "Assessment": [2],
                "Plan": [3, 1, 2],
            }
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
                        content=raw if raw is not None else json.dumps(value)
                    ),
                )
            ]
        )

    monkeypatch.setattr(summarize.openai.chat.completions, "create", create)
    result = summarize.generate_clinical_note(
        transcript, example, schema, custom, bot_fidelity=True
    )
    assert len(calls) == 1
    return result


def test_all_fields_are_exact_selected_sources_in_order(monkeypatch, grounded_case):
    transcript, schema, _, value = grounded_case
    result = generate(monkeypatch, grounded_case)
    sources = bot.grounding_for(transcript, schema.fields).sources
    for field, refs in value[bot.KEY].items():
        if not refs:
            assert (
                result[field] == "Review the transcript before completing this section."
            )
            continue
        expected = (
            bot.DRAFT_REVIEW
            + "\n\n"
            + "\n\n".join(
                sources[i - 1].attribution + ":\n" + sources[i - 1].text
                for i in sorted(refs)
            )
        )
        if field == "Plan":
            expected += "\n\n" + bot.REASON_REVIEW
        assert result[field] == expected
    assert "not independently verified" in result["Plan"]
    assert "unidentified speaker [source 2]" in result["Assessment"]
    assert "Should I call tomorrow?" in result["Assessment"]


@pytest.mark.parametrize("refs", [[0], [4], [True], [1, 1], ["1"], None, [1] * 101])
def test_invalid_refs_never_return_a_note(monkeypatch, grounded_case, refs):
    grounded_case[3][bot.KEY]["Assessment"] = refs
    with pytest.raises(bot.BotNoteContractInvalid):
        generate(monkeypatch, grounded_case)


@pytest.mark.parametrize(
    "change",
    ["generated_fields", "extra", "missing", "nonarray", "oversize", "wrong_envelope"],
)
def test_no_generated_field_or_malformed_envelope_is_accepted(
    monkeypatch, grounded_case, change
):
    value = grounded_case[3]
    if change == "generated_fields":
        value["fields"] = {"Assessment": "No serious disease."}
    elif change == "extra":
        value[bot.KEY]["invented"] = [1]
    elif change == "missing":
        value[bot.KEY].pop("Subjective")
    elif change == "nonarray":
        value[bot.KEY]["Assessment"] = "No other injury."
    elif change == "oversize":
        value["padding"] = "x" * 262145
    else:
        value["extra"] = "not allowed"
    with pytest.raises(bot.BotNoteContractInvalid):
        generate(monkeypatch, grounded_case)


def test_duplicate_keys_and_truncated_completion_fail(monkeypatch, grounded_case):
    raw = json.dumps(grounded_case[3]).replace(
        '"Assessment": [2]', '"Assessment": [1], "Assessment": [2]'
    )
    with pytest.raises(bot.BotNoteContractInvalid):
        generate(monkeypatch, grounded_case, raw)
    with pytest.raises(bot.BotNoteContractInvalid):
        generate(monkeypatch, grounded_case, finish_reason="length")


def test_custom_heading_is_equally_grounded(monkeypatch, grounded_case):
    t, s, e, _ = grounded_case
    s.fields = {"Custom decision area": "Requested reasoning"}
    result = generate(monkeypatch, (t, s, e, {bot.KEY: {"Custom decision area": [2]}}))
    assert result == {
        "Custom decision area": bot.DRAFT_REVIEW
        + "\n\nTranscript — unidentified speaker [source 2]:\n"
        + t.transcript_data[1].content
    }


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
    assert all(e.content in result["Plan"] for e in grounded_case[0].transcript_data)


def test_distinct_unidentified_people_remain_separate_whole_blocks(
    monkeypatch, grounded_case
):
    grounded_case[0].transcript_data = [
        SimpleNamespace(speaker="Unknown", content="Morgan says the left wrist hurts."),
        SimpleNamespace(
            speaker="Unknown", content="Taylor says the right shoulder hurts."
        ),
    ]
    grounded_case[3][bot.KEY] = {
        "Subjective": [],
        "Objective": [],
        "Assessment": [2, 1],
        "Plan": [],
    }
    result = generate(monkeypatch, grounded_case)["Assessment"]
    assert result.index("Morgan") < result.index("Taylor")
    assert result.count("unidentified speaker") == 2
    assert "model-classified" not in result


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
        summarize.generate_clinical_note(transcript, example, schema, bot_fidelity=True)
