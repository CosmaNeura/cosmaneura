"""Prompt contracts only; actual model semantics require separate evaluation."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.jobs import summarize, transcribe
from app.services import recall_webhook_operations as operations
from app.services import zoom_bot_note_grounding as bot
from app.services import zoom_bot_note_policy as policy

from app.models.doctor import Terminology
from app.models.note_type_customization import NoteTypeCustomization

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = json.loads((ROOT / "tools/zoom-note-fidelity/templates.json").read_text())


def inputs(case="R2"):
    fixture = json.loads((ROOT / "tools/capture-retention/fixtures.json").read_text())
    case = next(item for item in fixture["cases"] if item["id"] == case)
    transcript = SimpleNamespace(
        doctor_id="synthetic-doctor",
        transcript_data=[
            SimpleNamespace(speaker=e["speaker"], content=e["content"])
            for e in case["entries"]
        ],
    )
    template = TEMPLATES["templates"][case["id"]]
    schema = SimpleNamespace(fields=template["fields"])
    example = SimpleNamespace(field_examples=template["fieldExamples"])
    doctor = SimpleNamespace(biography=None, terminology=Terminology.doctor_patient)
    return transcript, schema, example, doctor


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
def test_default_and_capture_style_prompts_keep_exact_baseline_bytes(
    case, custom, source_indexes
):
    t, schema, example, doctor = inputs(case)
    value = summarize.build_structured_prompt(
        t,
        example,
        schema,
        NoteTypeCustomization(**custom),
        action_items="- Retained synthetic action.",
        doctor=doctor,
        source_indexes=source_indexes,
    )
    key = json.dumps([case, custom, source_indexes], sort_keys=True)
    expected = json.loads(
        (ROOT / "tools/zoom-note-fidelity/default-prompt-sha256.json").read_text()
    )
    assert hashlib.sha256(value.encode()).hexdigest() == expected[key]


@pytest.mark.parametrize("case", ["R2", "R3"])
def test_selection_prompt_keeps_source_schema_examples_and_preferences(case):
    t, schema, example, _ = inputs(case)
    c = NoteTypeCustomization(custom_instructions="Prefer compact prose.")
    before = c.model_dump()
    prompt = bot.build_selection_prompt(t, example, schema, c)
    assert json.dumps(schema.fields, indent=2) in prompt
    assert json.dumps(example.field_examples, indent=2) in prompt
    assert json.dumps(c.model_dump(mode="json"), indent=2) in prompt
    assert all(entry.content in prompt for entry in t.transcript_data)
    assert "Do not generate any clinical prose" in prompt
    assert "Include the source context" in prompt
    assert "Patient-only Subjective" in prompt
    assert "no new inference is requested or accepted" in prompt
    assert c.model_dump() == before


def test_bot_has_one_final_call_and_no_action_extractor(monkeypatch):
    t, schema, example, doctor = inputs()
    t.transcript_data = [
        SimpleNamespace(speaker="Unknown", content="Recording check complete.")
    ]
    monkeypatch.setattr(summarize.Doctor, "one", lambda _: doctor)
    monkeypatch.setattr(
        summarize, "extract_action_items", lambda *_: pytest.fail("extractor called")
    )
    requests = []

    def create(**kw):
        requests.append(kw)
        content = json.dumps(
            {
                bot.KEY: [
                    {
                        "source_index": 1,
                        "purpose": "nonclinical",
                        "field": None,
                        "answer_scope": "not_question",
                        "context_refs": [],
                    }
                ]
            }
        )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop", message=SimpleNamespace(content=content)
                )
            ]
        )

    monkeypatch.setattr(summarize.openai.chat.completions, "create", create)
    note = summarize.generate_clinical_note(t, example, schema, bot_fidelity=True)
    assert list(note) == list(schema.fields)
    assert len(requests) == 1
    request = requests[0]
    assert request["response_format"]["type"] == "json_schema"
    assert request["response_format"]["json_schema"]["strict"] is True
    assert request["model"] == summarize.DEFAULT_MULTI_MODAL_MODEL
    assert "[source 1]" in request["messages"][0]["content"]
    assert "model classifications" in request["messages"][0]["content"]
    assert "Review the transcript" in note["Plan"]


@pytest.mark.parametrize("eligible", [False, True])
def test_only_recall_dispatch_adds_explicit_candidate_hint(monkeypatch, eligible):
    t = SimpleNamespace(id="transcript")
    claim = SimpleNamespace(
        transcript_id=t.id, schema_pending=True, engagement_pending=False
    )
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
def test_fanout_does_not_change_default_queue_arguments(monkeypatch, hint):
    calls = []
    monkeypatch.setattr(
        transcribe.DoctorNoteSchema,
        "where",
        lambda *_: SimpleNamespace(all=lambda: [SimpleNamespace(id="schema")]),
    )
    monkeypatch.setattr(summarize, "queue", lambda **kw: calls.append(kw))
    transcribe.queue_schema_transcription(
        SimpleNamespace(id="transcript", doctor_id="doctor"), note_policy=hint
    )
    assert calls == [
        {
            "transcript_id": "transcript",
            "schema_id": "schema",
            **({"note_policy": hint} if hint else {}),
        }
    ]
