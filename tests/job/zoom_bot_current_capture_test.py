"""Offline activation contracts; handcrafted responses are not model evidence."""

import json
from types import SimpleNamespace as NS
from typing import cast

import pytest

from app.jobs import summarize
from app.services import scribe_capture_history_grounding as history
from app.services import zoom_bot_note_grounding as bot
from app.services import zoom_bot_note_policy as policy

from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.doctor import Terminology
from app.models.doctor_note_example import DoctorNoteExample
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.note_type_customization import NoteTypeCustomization, PronounStyle

from tests.job.zoom_bot_test_support import SOAP, response, transcript


def test_answered_medication_question_does_not_gain_old_missing_answer_assertion():
    source = transcript(
        ("Doctor", "Tell me whether you have started or stopped any medicines."),
        ("Patient", "I have not started, stopped, or changed any medicine."),
        ("Doctor", "Keep a brief symptom diary."),
    )
    g = bot.BotGrounding.build(source, SOAP, patient_only=True)
    raw = response(
        g,
        plans=[3],
        prose={
            **dict.fromkeys(SOAP, ""),
            "Subjective": source.transcript_data[1].content,
        },
    )
    note = g.resolve(raw, SOAP, require_plan_reason=True)
    assert source.transcript_data[1].content in note["Subjective"]
    assert "Answer not established" not in json.dumps(note)
    assert "unanswered" not in json.dumps(note)
    assert history.MEDICATION_LIST_UNVERIFIED in note["Objective"]
    assert source.transcript_data[2].content in note["Plan"]
    assert bot.PLAN_REASON_LIMITATION in note["Plan"]
    # The old fabricated classification is rejected, not silently repaired.
    old = json.loads(raw)
    old["source_review"] = {
        "unresolved_questions": [
            {
                "question_ref": 1,
                "answer_refs": [2],
                "unanswered_parts": ["started or stopped any medicines"],
            }
        ]
    }
    with pytest.raises(bot.BotNoteContractInvalid):
        g.resolve(json.dumps(old), SOAP, require_plan_reason=True)


def test_answered_and_partial_context_remains_neutral_without_pair_classification():
    source = transcript(
        ("Unverified speaker (A)", "Any falls or other injuries?"),
        ("Unverified speaker (B)", "No falls."),
        ("Unverified speaker (A)", "Any medication changes?"),
        ("Unverified speaker (B)", "No medication changes."),
    )
    g = bot.BotGrounding.build(source, SOAP, patient_only=True)
    note = g.resolve(
        response(g, histories=[1, 2, 3, 4]), SOAP, require_plan_reason=False
    )
    assert all(entry.content in note["Objective"] for entry in source.transcript_data)
    assert "Answer not established" not in note["Objective"]
    assert "not answered" not in note["Objective"]
    assert "No injuries" not in note["Objective"]
    assert note["Subjective"] == ""
    assert set(note) == set(SOAP)


@pytest.mark.parametrize("refs", [[0], [99], [True], [1, 1]])
def test_invalid_plan_selections_reject_without_fallback(refs):
    g = bot.BotGrounding.build(
        transcript(("Patient", "Past history.")), SOAP, patient_only=True
    )
    with pytest.raises(bot.BotNoteContractInvalid):
        g.resolve(response(g, plans=refs), SOAP, require_plan_reason=False)


def test_model_cannot_supply_plan_prose_or_select_recording_boundary():
    source = transcript(
        ("Patient", "History."),
        ("system", "[Segment 2 starts]"),
        ("Doctor", "Return in ten days."),
    )
    source.transcript_data[1].startTime = source.transcript_data[1].endTime = 0
    g = bot.BotGrounding.build(source, SOAP, patient_only=True, boundaries=(2,))
    with pytest.raises(bot.BotNoteContractInvalid):
        g.resolve(response(g, plans=[2]), SOAP, require_plan_reason=False)
    with pytest.raises(bot.BotNoteContractInvalid):
        g.resolve(
            response(
                g,
                plans=[3],
                prose={**dict.fromkeys(SOAP, ""), "Plan": "Inferred advice"},
            ),
            SOAP,
            require_plan_reason=False,
        )
    note = g.resolve(response(g, plans=[3]), SOAP, require_plan_reason=False)
    assert note["Plan"] == "- Model-labeled clinician: Return in ten days."


def test_provider_schema_example_preferences_and_single_call_remain(monkeypatch):
    source = transcript(
        ("Patient", "I have a mild headache."), ("Doctor", "Keep a diary.")
    )
    fields = {"Patient Name": "Identity", **SOAP}
    schema = NS(fields=fields)
    example = NS(field_examples={"Subjective": "EXAMPLE STYLE ONLY"})
    customization = NoteTypeCustomization(
        custom_instructions="Use short sentences.", pronoun_style=PronounStyle.neutral
    )
    before = customization.model_dump()
    g = bot.BotGrounding.build(source, fields, patient_only=True)
    raw = response(
        g,
        fields,
        plans=[2],
        prose={
            **dict.fromkeys(fields, ""),
            "Patient Name": "Synthetic name",
            "Subjective": "Mild headache.",
        },
    )
    calls, options = [], []

    def create(**kwargs):
        calls.append(kwargs)
        return NS(choices=[NS(finish_reason="stop", message=NS(content=raw))])

    def with_options(**kwargs):
        options.append(kwargs)
        return NS(chat=NS(completions=NS(create=create)))

    monkeypatch.setattr(summarize.openai, "with_options", with_options)
    monkeypatch.setattr(
        summarize.Doctor,
        "one",
        lambda _: NS(biography=None, terminology=Terminology.doctor_patient),
    )
    monkeypatch.setattr(
        summarize, "extract_action_items", lambda *_: pytest.fail("extra model call")
    )
    note = summarize.generate_clinical_note(
        cast(AIVisitTranscript, source),
        cast(DoctorNoteExample, example),
        cast(DoctorNoteSchema, schema),
        customization,
        bot_fidelity=True,
    )
    assert len(calls) == 1 and options == [{"timeout": 30.0, "max_retries": 0}]
    prompt = calls[0]["messages"][1]["content"]
    assert "EXAMPLE STYLE ONLY" in prompt and "Use short sentences." in prompt
    assert "infer it from the clinical context" not in prompt
    assert (
        "Review of Systems (ROS) section:" not in prompt
    )  # No destination in this actual schema.
    contract = calls[0]["response_format"]["json_schema"]["schema"]
    assert next(iter(contract["properties"])) == "plan_source_refs"
    assert set(contract["properties"]) == {
        "plan_source_refs",
        "source_context_refs",
        "fields",
    }
    assert set(contract["properties"]["fields"]["properties"]) == set(fields)
    assert customization.model_dump() == before
    assert note["Patient Name"] == "Synthetic name"
    assert json.dumps(note).count(bot.ROLE_QUALIFICATION) == 1


def test_pilot_queue_has_no_automatic_retry_and_default_queue_is_preserved(monkeypatch):
    routed = []
    monkeypatch.setattr(
        summarize.perform_celery, "delay", lambda **kw: routed.append(("default", kw))
    )
    monkeypatch.setattr(
        summarize.perform_bot_celery, "delay", lambda **kw: routed.append(("bot", kw))
    )
    summarize.queue(transcript_id="synthetic", schema_id="synthetic")
    summarize.queue(
        transcript_id="synthetic", schema_id="synthetic", note_policy=policy.POLICY
    )
    assert [route for route, _ in routed] == ["default", "bot"]
    assert getattr(summarize.perform_bot_celery, "autoretry_for") == ()
    assert summarize.perform_bot_celery.max_retries == 0
    assert getattr(summarize.perform_celery, "autoretry_for") == (Exception,)
