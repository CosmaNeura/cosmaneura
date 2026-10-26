"""Actual bot request/resolver boundaries; no model, database or network calls.

A handcrafted response verifies the contract, not source selection or clinical
quality. Actual R2/R3 evaluation and a real bot capture remain separate gates.
"""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app.jobs import summarize, transcribe
from app.services import recall_webhook_operations as operations
from app.services import scribe_capture_history_grounding as history
from app.services import scribe_capture_notes as capture
from app.services import zoom_bot_note_grounding as bot
from app.services import zoom_bot_note_policy as policy

from app.models.doctor import Terminology
from app.models.note_type_customization import NoteTypeCustomization

ROOT = Path(__file__).resolve().parents[2]
SOAP = {
    "Subjective": "Patient statements",
    "Objective": "Observed data",
    "Assessment": "Impression",
    "Plan": "Care plan",
}


def entries(*rows):
    return NS(
        doctor_id="synthetic",
        transcript_data=[
            NS(speaker=role, content=text, startTime=i * 1000, endTime=(i + 1) * 1000)
            for i, (role, text) in enumerate(rows)
        ],
    )


def inputs(case):
    cases = json.loads((ROOT / "tools/capture-retention/fixtures.json").read_text())[
        "cases"
    ]
    fixture = next(x for x in cases if x["id"] == case)
    template = json.loads(
        (ROOT / "tools/zoom-note-fidelity/templates.json").read_text()
    )["templates"][case]
    transcript = entries(*[(x["speaker"], x["content"]) for x in fixture["entries"]])
    return (
        transcript,
        NS(fields=template["fields"]),
        NS(field_examples=template["fieldExamples"]),
    )


def doctor():
    return NS(biography=None, terminology=Terminology.doctor_patient)


def value(grounding, fields=SOAP, *, plans=(), histories=(), generated=None):
    result = {key: "" for key in fields} if generated is None else generated
    if grounding.plan.fields or grounding.history.fields:
        result = {"fields": result}
    if grounding.plan.fields:
        result["plan_source_refs"] = {key: list(plans) for key in grounding.plan.fields}
    if grounding.history.fields:
        result[history.KEY] = {
            key: {"current_list": [], "change_history": list(histories)}
            if key in grounding.history.medication_fields
            else list(histories)
            for key in grounding.history.fields
        }
    return json.dumps(result)


def completion(raw, *, finish="stop"):
    return NS(choices=[NS(finish_reason=finish, message=NS(content=raw))])


@pytest.mark.parametrize("case", ["R2", "R3"])
@pytest.mark.parametrize(
    "custom",
    [
        {},
        {"require_plan_reason": False},
        {"ros_distinct_from_subjective": False},
        {
            "subjective_source": "patient_and_doctor",
            "pronoun_style": "neutral",
            "custom_instructions": "Use concise prose.",
        },
    ],
)
@pytest.mark.parametrize("source_indexes", [False, True])
def test_default_and_capture_prompt_bytes_unchanged(case, custom, source_indexes):
    t, schema, example = inputs(case)
    prompt = summarize.build_structured_prompt(
        t,
        example,
        schema,
        NoteTypeCustomization(**custom),
        action_items="- Retained synthetic action.",
        doctor=doctor(),
        source_indexes=source_indexes,
    )
    expected = json.loads(
        (ROOT / "tools/zoom-note-fidelity/default-prompt-sha256.json").read_text()
    )
    assert (
        hashlib.sha256(prompt.encode()).hexdigest()
        == expected[json.dumps([case, custom, source_indexes], sort_keys=True)]
    )


@pytest.mark.parametrize("case", ["R2", "R3"])
@pytest.mark.parametrize("unknown", [False, True])
def test_real_configured_call_reuses_capture_composition_actual_template_and_preferences(
    monkeypatch, case, unknown
):
    t, schema, example = inputs(case)
    if unknown:
        for row in t.transcript_data:
            row.speaker = "Unknown"
    c = NoteTypeCustomization(
        custom_instructions="Use compact wording; do not change source facts."
    )
    before = c.model_dump()
    g = bot.BotGrounding.build(t, schema.fields, patient_only=True)
    requests = []
    monkeypatch.setattr(summarize.Doctor, "one", lambda _: doctor())
    monkeypatch.setattr(
        summarize, "extract_action_items", lambda *_: pytest.fail("extractor called")
    )
    monkeypatch.setattr(
        capture,
        "capture_note",
        lambda **_: pytest.fail("capture authority path called"),
    )
    monkeypatch.setattr(
        summarize.openai.chat.completions,
        "create",
        lambda **kw: requests.append(kw) or completion(value(g, schema.fields)),
    )
    result = summarize.generate_clinical_note(t, example, schema, c, bot_fidelity=True)
    assert len(requests) == 1 and list(result) == list(schema.fields)
    request = requests[0]
    assert request["model"] == summarize.DEFAULT_MULTI_MODAL_MODEL
    assert (
        request["temperature"] == 0
        and request["top_p"] == 0.1
        and request["max_tokens"] == 16384
    )
    assert request["messages"][0] == {
        "role": "system",
        "content": capture.GROUNDING + bot.ROLE_AND_QUESTION_CONTRACT,
    }
    user = request["messages"][1]
    assert user["role"] == "user"
    assert json.dumps(schema.fields, indent=2) in user["content"]
    assert json.dumps(example.field_examples, indent=2) in user["content"]
    assert c.custom_instructions in user["content"]
    assert all(x.content in user["content"] for x in t.transcript_data)
    assert (
        capture.capture_retention_context(
            t.transcript_data, c, field_names=tuple(schema.fields)
        )
        in user["content"]
    )
    assert request["response_format"]["json_schema"][
        "schema"
    ] == capture.capture_response_schema(tuple(schema.fields), g.plan.fields, g.history)
    assert request["response_format"]["json_schema"]["strict"] is True
    assert c.model_dump() == before


def test_unknown_question_contract_changes_only_bot_request():
    t = entries(
        ("Unknown", "Have you fallen or had another injury?"),
        ("Unknown", "There was no fall."),
    )
    g = bot.BotGrounding.build(t, SOAP, patient_only=True)
    original = g.history.prompt(has_plans=True)
    assert bot._OMIT_QUESTIONS in original
    assert bot._OMIT_QUESTIONS not in g.history_prompt()
    assert bot._KEEP_QUESTIONS in g.history_prompt()
    assert g.history.prompt(has_plans=True) == original
    assert (
        "no-fall reply does not answer other injury" in bot.ROLE_AND_QUESTION_CONTRACT
    )
    # Selection is handcrafted: this demonstrates retained source context only.
    note = g.resolve(value(g, histories=[2, 1]), SOAP, require_plan_reason=True)
    assert note["Objective"] == (
        "Current medication list is unverified; review the source statements.\n\nReported transcript history:\n"
        "Transcript — Unidentified speaker:\nHave you fallen or had another injury?\n\n"
        "Transcript — Unidentified speaker:\nThere was no fall."
    )
    assert "no other injury" not in note["Objective"]


@pytest.mark.parametrize(
    "roles,patient_only,enabled",
    [
        (["Unknown", "Unknown"], True, True),
        (["Unknown", "Unknown"], False, False),
        (["Doctor", "Patient"], True, False),
        (["Unknown", "Patient"], True, False),
        (["Other", "Unknown"], True, False),
    ],
)
def test_history_scope_does_not_upgrade_or_broaden_roles(roles, patient_only, enabled):
    t = entries(*[(role, "I have a headache.") for role in roles])
    g = bot.BotGrounding.build(t, SOAP, patient_only=patient_only)
    assert bool(g.history.fields) is enabled
    assert [x.speaker for x in t.transcript_data] == roles
    for source, role in zip(g.plan.sources, roles, strict=True):
        assert source.attribution == (
            f"Transcript — model-classified {role} (role not independently verified)"
            if role in {"Doctor", "Patient", "Other"}
            else "Transcript — Unidentified speaker"
        )
        assert "Clinician" not in source.attribution


def test_whole_source_corrections_negatives_and_same_words_are_retained():
    t = entries(
        ("Unknown", "It began 12 days ago, not 20."),
        ("Unknown", "I had a headache yesterday, but none today."),
        ("Unknown", "I did not start or stop medicine."),
        ("Unknown", "The reading was 118/72, not 180/72."),
        ("Unknown", "I have pain."),
        ("Unknown", "I have pain."),
    )
    g = bot.BotGrounding.build(t, SOAP, patient_only=True)
    note = g.resolve(
        value(
            g,
            histories=[6, 5, 4, 3, 2, 1],
            generated={key: "Fabricated field." for key in SOAP},
        ),
        SOAP,
        require_plan_reason=False,
    )
    assert note["Objective"].count("I have pain.") == 2
    assert (
        "Fabricated field" not in note["Objective"]
        and "Fabricated field" not in note["Plan"]
    )
    assert note["Objective"].endswith(
        "\n\n".join(
            f"Transcript — Unidentified speaker:\n{x.content}"
            for x in t.transcript_data
        )
    )
    # The unprotected model prose still needs semantic review; this is not a
    # whole-note truth guarantee or a blanket source-only renderer.
    assert note["Assessment"] == "Fabricated field."


def test_prospective_plan_reuses_complete_sources_with_unverified_attribution():
    t = entries(
        (
            "Doctor",
            "Do not start it until the test result; follow up in 10 days, not 10 weeks.",
        ),
        ("Patient", "I understand the 10-day follow-up."),
    )
    g = bot.BotGrounding.build(t, SOAP, patient_only=True)
    note = g.resolve(value(g, plans=[2, 1]), SOAP, require_plan_reason=True)
    assert note["Plan"] == (
        "Transcript — model-classified Doctor (role not independently verified):\n"
        "Do not start it until the test result; follow up in 10 days, not 10 weeks.\n\n"
        "Transcript — model-classified Patient (role not independently verified):\n"
        "I understand the 10-day follow-up.\n\n"
        "Rationale beyond what the selected source states is not established."
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"Assessment": "Impression"},
        {"Medications": "Reported medicines"},
        {"Custom narrative": "Context"},
        {"Plan": "Care"},
    ],
)
def test_existing_custom_schema_and_empty_section_contract(fields):
    g = bot.BotGrounding.build(
        entries(("Unknown", "No new medicine.")), fields, patient_only=True
    )
    note = g.resolve(value(g, fields), fields, require_plan_reason=False)
    assert list(note) == list(fields)
    if "Medications" in fields:
        assert (
            note["Medications"]
            == "Current medication list is unverified; review the source statements."
        )
    if "Assessment" in fields:
        assert note["Assessment"] == ""
    if "Plan" in fields:
        assert note["Plan"] == "Review the transcript before completing this section."


@pytest.mark.parametrize("bad", [True, 0, -1, 4, "1", None])
def test_invalid_plan_refs_do_not_fall_back_to_generated_prose(bad):
    g = bot.BotGrounding.build(
        entries(("Patient", "Follow up tomorrow.")), SOAP, patient_only=True
    )
    with pytest.raises(bot.BotNoteContractInvalid):
        g.resolve(value(g, plans=[bad]), SOAP, require_plan_reason=True)


@pytest.mark.parametrize(
    "raw",
    [
        "{}",
        "[]",
        "null",
        '{"fields":{},"fields":{},"plan_source_refs":{}}',
        "x" * 262145,
    ],
)
def test_invalid_envelope_is_terminal(raw):
    g = bot.BotGrounding.build(
        entries(("Unknown", "No new medicine.")), SOAP, patient_only=True
    )
    with pytest.raises(bot.BotNoteContractInvalid):
        g.resolve(raw, SOAP, require_plan_reason=True)


def test_only_actual_server_merge_positions_are_excluded_from_clinical_refs():
    first = entries(("Unknown", "[Segment 2 starts]"), ("Unknown", "I have pain."))
    second = entries(("Unknown", "It is mild."))
    assert bot.segment_boundaries([first, second]) == (3,)
    merged = NS(
        transcript_data=first.transcript_data
        + [NS(speaker="system", content="[Segment 2 starts]", startTime=0, endTime=0)]
        + second.transcript_data
    )
    g = bot.BotGrounding.build(merged, SOAP, patient_only=True, boundaries=(3,))
    assert bool(g.history.fields)
    assert (
        g.plan.sources[0].kind == "transcript"
        and g.plan.sources[2].kind == "recording_boundary"
    )
    assert (
        "[Segment 2 starts]"
        in g.resolve(value(g, histories=[1, 4]), SOAP, require_plan_reason=False)[
            "Objective"
        ]
    )
    with pytest.raises(bot.BotNoteContractInvalid):
        g.resolve(value(g, plans=[3]), SOAP, require_plan_reason=False)
    with pytest.raises(bot.BotNoteContractInvalid):
        g.resolve(value(g, histories=[3]), SOAP, require_plan_reason=False)
    with pytest.raises(bot.BotNoteContractInvalid):
        bot.BotGrounding.build(merged, SOAP, patient_only=True, boundaries=(1,))


@pytest.mark.parametrize(
    "finish,raw",
    [("length", "{}"), ("content_filter", "{}"), ("stop", None), ("stop", "{}")],
)
def test_invalid_model_completion_has_one_call_no_repair(monkeypatch, finish, raw):
    t = entries(("Patient", "A reported symptom."))
    requests = []
    monkeypatch.setattr(summarize.Doctor, "one", lambda _: doctor())
    monkeypatch.setattr(
        summarize.openai.chat.completions,
        "create",
        lambda **kw: requests.append(kw) or completion(raw, finish=finish),
    )
    with pytest.raises(bot.BotNoteContractInvalid):
        summarize.generate_clinical_note(t, None, NS(fields=SOAP), bot_fidelity=True)
    assert len(requests) == 1


@pytest.mark.parametrize("eligible", [False, True])
def test_only_recall_dispatch_adds_explicit_candidate_hint(monkeypatch, eligible):
    t = NS(id="transcript")
    claim = NS(transcript_id=t.id, schema_pending=True, engagement_pending=False)
    calls = []
    monkeypatch.setattr(operations, "claim_pipeline", lambda _: claim)
    monkeypatch.setattr(operations.AIVisitTranscript, "get", lambda **_: t)
    monkeypatch.setattr(operations, "_mark_pipeline_step", lambda *_: True)
    monkeypatch.setattr(policy, "eligible", lambda _: eligible)
    monkeypatch.setattr(
        transcribe, "queue_schema_transcription", lambda *a, **kw: calls.append((a, kw))
    )
    operations.dispatch_pipeline("synthetic-operation")
    assert calls == [((t,), {"note_policy": policy.POLICY} if eligible else {})]


@pytest.mark.parametrize("hint", [None, policy.POLICY])
def test_fanout_default_queue_arguments_unchanged(monkeypatch, hint):
    calls = []
    monkeypatch.setattr(
        transcribe.DoctorNoteSchema,
        "where",
        lambda *_: NS(all=lambda: [NS(id="schema")]),
    )
    monkeypatch.setattr(summarize, "queue", lambda **kw: calls.append(kw))
    transcribe.queue_schema_transcription(
        NS(id="transcript", doctor_id="doctor"), note_policy=hint
    )
    assert calls == [
        dict(
            transcript_id="transcript",
            schema_id="schema",
            **({"note_policy": hint} if hint else {}),
        )
    ]


def test_merge_markers_alone_do_not_create_a_model_source():
    t = NS(
        transcript_data=[
            NS(speaker="system", content="[Segment 2 starts]", startTime=0, endTime=0)
        ]
    )
    with pytest.raises(bot.BotNoteContractInvalid):
        bot.BotGrounding.build(t, SOAP, patient_only=True, boundaries=(1,))


@pytest.mark.parametrize("source", ["", "  ", "denial\x00changed", "\ud800"])
def test_invalid_source_text_is_rejected_before_model_dispatch(source):
    with pytest.raises(bot.BotNoteContractInvalid):
        bot.BotGrounding.build(entries(("Unknown", source)), SOAP, patient_only=True)
