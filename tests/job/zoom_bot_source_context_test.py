"""Selection mechanics are tested separately from clinical completeness."""

import json

import pytest
from app.services import zoom_bot_note_grounding as bot

from tests.job.zoom_bot_test_support import SOAP, response, transcript


def test_partial_question_is_exact_neutral_context_not_an_invented_denial():
    source = transcript(
        ("Doctor", "Have you had a fall or another injury?"),
        ("Patient", "There was no fall."),
    )
    g = bot.BotGrounding.build(source, SOAP, patient_only=True)
    note = g.resolve(
        response(
            g,
            context=[1, 2],
            prose={**dict.fromkeys(SOAP, ""), "Subjective": "There was no fall."},
        ),
        SOAP,
        require_plan_reason=False,
    )
    assert source.transcript_data[0].content in note["Objective"]
    assert json.dumps(note).count("There was no fall.") == 1
    assert "No injuries" not in json.dumps(note)
    assert "unanswered" not in json.dumps(note)
    assert "Answer not established" not in json.dumps(note)
    assert source.transcript_data[0].content not in note["Subjective"]


def test_fully_answered_question_is_not_mandatory_note_prose():
    source = transcript(
        ("Doctor", "Have you fainted?"), ("Patient", "I have never fainted.")
    )
    g = bot.BotGrounding.build(source, SOAP, patient_only=True)
    note = g.resolve(
        response(
            g, prose={**dict.fromkeys(SOAP, ""), "Subjective": "I have never fainted."}
        ),
        SOAP,
        require_plan_reason=False,
    )
    assert "Have you fainted?" not in json.dumps(note)


@pytest.mark.parametrize("refs", [[True], [0], [3], [1, 1], "1", None])
def test_invalid_context_references_reject(refs):
    g = bot.BotGrounding.build(
        transcript(("Doctor", "Any recent injury?")), SOAP, patient_only=True
    )
    raw = json.loads(response(g))
    raw["source_context_refs"] = refs
    with pytest.raises(bot.BotNoteContractInvalid):
        g.resolve(json.dumps(raw), SOAP, require_plan_reason=False)


def test_duplicate_context_key_and_boundary_reject():
    source = transcript(
        ("Doctor", "Any recent injury?"), ("system", "[Segment 2 starts]")
    )
    source.transcript_data[1].startTime = source.transcript_data[1].endTime = 0
    g = bot.BotGrounding.build(source, SOAP, patient_only=True, boundaries=(2,))
    for raw in [
        response(g, context=[2]),
        response(g)[:-1] + ', "source_context_refs": []}',
    ]:
        with pytest.raises(bot.BotNoteContractInvalid):
            g.resolve(raw, SOAP, require_plan_reason=False)


def test_context_never_invents_a_custom_schema_destination():
    fields = {
        "Patient Name": "Identity",
        "Assessment": "Impression",
        "Next steps": "Plan",
    }
    g = bot.BotGrounding.build(
        transcript(("Doctor", "Any recent injury?")), fields, patient_only=True
    )
    assert g.context.target is None
    assert "source_context_refs" not in g.response_schema(fields)["properties"]


def test_empty_model_selection_cannot_remove_conservative_multipart_scope():
    source = transcript(
        ("Doctor", "Have you had a fall or another injury?"),
        ("Patient", "There was no fall."),
    )
    g = bot.BotGrounding.build(source, SOAP, patient_only=True)
    # The legal empty model row remains unchanged; deterministic context keeps
    # the complete alternatives without asserting an answer relationship.
    note = g.resolve(
        response(
            g, prose={**dict.fromkeys(SOAP, ""), "Subjective": "There was no fall."}
        ),
        SOAP,
        require_plan_reason=False,
    )
    assert source.transcript_data[0].content in json.dumps(note)
