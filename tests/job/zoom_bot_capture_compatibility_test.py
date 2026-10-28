"""A capture-only Objective change must not silently advance the bot contract."""

import pytest

from app.services import scribe_capture_history_grounding as capture
from app.services import scribe_capture_notes as notes
from app.services.zoom_bot_note_grounding import BotGrounding

from tests.job.zoom_bot_test_support import SOAP, transcript


@pytest.mark.parametrize("patient_only", [True, False])
def test_bot_history_does_not_call_the_evolving_capture_factory(
    monkeypatch, patient_only
):
    def capture_changed(*_args, **_kwargs):
        pytest.fail("Bot must retain its evaluated history factory")

    monkeypatch.setattr(
        capture.ReportedHistoryGrounding, "for_capture", capture_changed
    )
    entries = transcript(("Patient", "A source measurement was corrected."))
    bot = BotGrounding.build(entries, SOAP, patient_only=patient_only)
    assert bot.history.fields == ()
    assert capture.KEY not in bot.response_schema(SOAP)["properties"]


def test_unknown_bot_preserves_original_source_identity_and_provider_schema(
    monkeypatch,
):
    entries = transcript(
        ("Unverified speaker (first)", "A complete source statement."),
        ("Unverified speaker (second)", "A different source statement."),
    )
    entries.transcript_data[0].source_speaker_id = "source-a"
    entries.transcript_data[1].source_speaker_id = "source-b"
    bot = BotGrounding.build(entries, SOAP, patient_only=True)
    assert bot.history.identities == (
        ("Unverified speaker (first)", "source-a"),
        ("Unverified speaker (second)", "source-b"),
    )
    original_schema = notes.capture_response_schema

    def capture_schema_changed(fields, plan_fields, history_grounding):
        value = original_schema(fields, plan_fields, history_grounding)
        for heading in history_grounding.fields:
            value["properties"]["fields"]["properties"][heading]["enum"] = [""]
        return value

    monkeypatch.setattr(notes, "capture_response_schema", capture_schema_changed)
    schema = bot.response_schema(SOAP)["properties"]["fields"]["properties"]
    assert schema["Objective"] == {"type": "string"}
    assert (
        schema["Subjective"]
        == schema["Plan"]
        == {
            "type": "string",
            "enum": [""],
        }
    )
    assert bot.history._render([1, 2]) == (
        "- Unidentified speaker: A complete source statement.\n"
        "- Unidentified speaker: A different source statement."
    )
