"""Required context shape cannot invent an anchor or validate its meaning."""

import json
from copy import deepcopy

import pytest

from app.services import zoom_bot_note_grounding as bot
from app.services import zoom_bot_source_dispositions as p

from tests.job import zoom_bot_grounding_test as g
from tests.job.zoom_bot_dispositions_test import FIELDS


@pytest.fixture
def grounded_case():
    return g.grounded_case.__wrapped__()


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        [],
        {"additional_refs": []},
        {"first_ref": None, "additional_refs": []},
        {"first_ref": True, "additional_refs": []},
        {"first_ref": 1, "additional_refs": [1]},
        {"first_ref": 99, "additional_refs": []},
        {"first_ref": 3, "additional_refs": []},
        {"first_ref": 1, "additional_refs": [], "reason": "invented"},
    ],
)
def test_missing_invalid_duplicate_or_self_anchor_remains_terminal(
    monkeypatch, grounded_case, bad
):
    value = g.model_value(grounded_case[3])
    value[bot.KEY][2]["context"] = bad
    with pytest.raises(bot.BotNoteContractInvalid):
        g.generate(monkeypatch, grounded_case, json.dumps(value))


def test_decoder_preserves_order_refs_without_repair_or_mutation(grounded_case):
    value = g.model_value(grounded_case[3])
    before = deepcopy(value)
    assert p.decode_model_rows(value[bot.KEY]) == grounded_case[3][bot.KEY]
    assert value == before
    with pytest.raises(p.DispositionInvalid):
        p.decode_model_rows(
            grounded_case[3][bot.KEY]
        )  # Old wire shape is not repaired.


def test_unanswered_question_cannot_carry_a_claimed_reply(grounded_case):
    value = g.model_value(grounded_case[3])[bot.KEY]
    value[1]["answer_scope"] = "unanswered"
    with pytest.raises(p.DispositionInvalid):
        p.decode_model_rows(value)


def test_context_shape_does_not_make_an_unrelated_anchor_true():
    rows = [
        g.row(1, "reported_history", "Subjective"),
        g.row(2, "contextual_correction", "Subjective", [1]),
    ]
    decoded = p.decode_model_rows(g.model_value({bot.KEY: rows})[bot.KEY])
    assert p.resolve(decoded, FIELDS, ["Patient"] * 2, patient_only=True)[0][
        "Subjective"
    ] == [1, 2]
    # The source meaning is deliberately not checked here. Independent review
    # must still reject unrelated topic/person/time pairs, even if shape passes.


def test_context_warning_once_per_field_keeps_each_source_marker_and_words(
    monkeypatch, grounded_case
):
    value = grounded_case[3][bot.KEY]
    value[5]["context_refs"] = [3]
    result = g.generate(monkeypatch, grounded_case)
    for field, text in result.items():
        assert text.count(p.CONTEXT_REVIEW) <= 1
        assert text.count(bot.DRAFT_REVIEW) <= 1
    assert "[source 3] model-classified Patient — context only" in result["Subjective"]
    assert "[source 6] model-classified Patient" in result["Subjective"]
    for index in [3, 6]:
        assert (
            grounded_case[0].transcript_data[index - 1].content in result["Subjective"]
        )
    assert (
        "no source-stated clinical impression was selected" not in result["Assessment"]
    )


def test_no_array_cardinality_extensions_and_azure_object_budget(grounded_case):
    t, s, *_ = grounded_case
    schema = p.schema(
        s.fields, [e.speaker for e in t.transcript_data], patient_only=True
    )
    properties = 0

    def walk(value):
        nonlocal properties
        if isinstance(value, dict):
            assert not (
                {"minItems", "maxItems", "uniqueItems", "allOf", "if", "then"}
                & value.keys()
            )
            properties += len(value.get("properties", {}))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(schema)
    assert properties <= 100
