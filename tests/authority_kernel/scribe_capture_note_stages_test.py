"""Real configured stage contracts; no provider or database connection."""

import json
from types import SimpleNamespace

import pytest

from app.services import scribe_capture_notes as notes
from app.services.scribe_capture_deadline import CaptureBudget, capture_budget
from app.services.scribe_capture_note_stages import CaptureNoteStages


def setup_stages(monkeypatch, *, fields=None, replies=None):
    fields = fields or {
        "Subjective": "History",
        "Objective": "Measurements",
        "Plan": "Care",
    }
    sources = (
        notes.plans.PlanSource.speech(
            "Unknown", "Have you had a fall or another injury?"
        ),
        notes.plans.PlanSource.speech("Unknown", "There was no fall."),
        notes.plans.PlanSource.speech("Unknown", "Avoid heavy lifting for five days."),
    )
    plan = notes.plans.PlanGrounding(notes.plans.prospective_fields(fields), sources)
    history = notes.history.ReportedHistoryGrounding.for_capture(
        tuple(fields), plan.fields, sources, patient_only=True
    )
    replies = replies or [
        {"plan_source_refs": {"Plan": [3]}},
        {"reported_history_source_refs": {"Objective": {"source_refs": [1, 2]}}},
        {"fields": {"Subjective": "Patient source unavailable."}},
    ]
    calls = []

    def options(**values):
        assert values == {"timeout": 30.0, "max_retries": 0}

        def create(**request):
            calls.append(request)
            reply = replies[len(calls) - 1]
            raw = reply if isinstance(reply, str) else json.dumps(reply)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop", message=SimpleNamespace(content=raw)
                    )
                ]
            )

        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

    monkeypatch.setattr(notes.openai, "with_options", options)
    schema = notes.capture_response_schema(tuple(fields), plan.fields, history)

    async def checkpoint():
        pass

    stages = CaptureNoteStages(
        response_schema=schema,
        plan=plan,
        history=history,
        model_factory=notes.ConfiguredNoteModel,
        max_bytes=notes.MAX_MODEL_BYTES,
        checkpoint=checkpoint,
    )
    return stages, calls, plan, history


@pytest.mark.asyncio
async def test_configured_stages_keep_original_sources_and_resolve_complete_question(
    monkeypatch,
):
    stages, calls, plan, history = setup_stages(monkeypatch)
    prompt = "Original complete synthetic sources and saved preferences."
    raw = await stages.complete(prompt, json_output=True)
    remaining, rendered_history = history.resolve(raw, max_bytes=notes.MAX_MODEL_BYTES)
    fields, rendered_plan = plan.resolve(remaining, max_bytes=notes.MAX_MODEL_BYTES)
    assert len(calls) == 3
    assert [
        list(c["response_format"]["json_schema"]["schema"]["properties"]) for c in calls
    ] == [["plan_source_refs"], ["reported_history_source_refs"], ["fields"]]
    assert all(c["messages"][0]["content"] == notes.GROUNDING for c in calls)
    assert all(c["messages"][1]["content"].startswith(prompt) for c in calls)
    assert all(c["model"] == "gpt-4.1-2025-04-14" for c in calls)
    assert '"source_refs": [1, 2]' not in calls[2]["messages"][1]["content"]
    assert "Have you had a fall or another injury?" in rendered_history["Objective"]
    assert "There was no fall." in rendered_history["Objective"]
    assert "Avoid heavy lifting for five days." in rendered_plan["Plan"]
    assert json.loads(fields)["Subjective"] == "Patient source unavailable."


@pytest.mark.asyncio
async def test_grounded_only_schema_needs_no_narrative_call(monkeypatch):
    stages, calls, _, _ = setup_stages(
        monkeypatch, fields={"Objective": "Measurements", "Plan": "Care"}
    )
    value = json.loads(await stages.complete("Complete sources", json_output=True))
    assert len(calls) == 2
    assert value["fields"] == {"Objective": "", "Plan": ""}


@pytest.mark.asyncio
async def test_custom_narrative_fields_preserve_names_order_and_values(monkeypatch):
    fields = {
        "My context": "Custom narrative",
        "Objective": "Measurements",
        "Plan": "Care",
        "Additional detail": "Custom text",
    }
    replies = [
        {"plan_source_refs": {"Plan": [3]}},
        {"reported_history_source_refs": {"Objective": {"source_refs": [1, 2]}}},
        {
            "fields": {
                "My context": "Recorded custom context",
                "Additional detail": "Not mentioned",
            }
        },
    ]
    stages, calls, _, _ = setup_stages(monkeypatch, fields=fields, replies=replies)
    value = json.loads(await stages.complete("Complete sources", json_output=True))
    assert list(value["fields"]) == list(fields)
    assert value["fields"]["My context"] == "Recorded custom context"
    assert value["fields"]["Additional detail"] == "Not mentioned"
    shape = calls[2]["response_format"]["json_schema"]["schema"]
    assert list(shape["properties"]["fields"]["properties"]) == [
        "My context",
        "Additional detail",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        '{"plan_source_refs":{"Plan":[3]},"plan_source_refs":{"Plan":[]}}',
        {"plan_source_refs": {"Plan": [99]}},
    ],
)
async def test_invalid_plan_channel_stops_before_another_provider_stage(
    monkeypatch, reply
):
    stages, calls, _, _ = setup_stages(monkeypatch, replies=[reply])
    with pytest.raises((ValueError, notes.plans.PlanGroundingInvalid)):
        await stages.complete("Complete sources", json_output=True)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_cancelled_capture_cannot_admit_the_next_stage(monkeypatch):
    stages, _, _, _ = setup_stages(monkeypatch)
    budget = CaptureBudget(30)
    calls = []

    class CancellingModel:
        async def complete(self, *_args, **_kwargs):
            calls.append(1)
            budget.cancel()
            return '{"plan_source_refs":{"Plan":[3]}}'

    stages.model_factory = lambda **_: CancellingModel()
    with capture_budget(budget), pytest.raises(TimeoutError):
        await stages.complete("Complete sources", json_output=True)
    assert calls == [1]


@pytest.mark.asyncio
async def test_checkpoint_refusal_before_second_stage_stops_provider_calls(monkeypatch):
    stages, calls, _, _ = setup_stages(monkeypatch)
    checked = 0

    async def checkpoint():
        nonlocal checked
        checked += 1
        if checked == 2:
            raise notes.CaptureNoteConflict

    stages.checkpoint = checkpoint
    with pytest.raises(notes.CaptureNoteConflict):
        await stages.complete("Complete sources", json_output=True)
    assert checked == 2
    assert len(calls) == 1
