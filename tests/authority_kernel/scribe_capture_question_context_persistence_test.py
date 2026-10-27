"""Actual capture finalization against disposable PG; no configured model calls."""

import json

import pytest

from app.services import scribe_capture_history_grounding as history
from app.services import scribe_capture_notes as notes

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import AIVisitTranscript

from tests.authority_kernel.scribe_capture_notes_test import rows
from tests.authority_kernel.scribe_capture_plan_grounding_test import (
    PROSPECTIVE,
    Model,
    admission_case,
    capture_engine,
    physical_case,
)

__all__ = ["admission_case", "capture_engine"]


class ContextModel(Model):
    def __init__(self, *, invalid=False):
        super().__init__({h: [3] if h == "P - PLAN" else [] for h in PROSPECTIVE})
        self.invalid = invalid

    async def complete(self, prompt, *, json_output):
        value = json.loads(await super().complete(prompt, json_output=json_output))
        value[history.KEY]["O - OBJECTIVE"] = {
            "source_refs": [1, 1] if self.invalid else [1, 2],
        }
        return json.dumps(value)


async def case(f, monkeypatch, *, invalid=False):
    args, _ = await physical_case(
        f,
        monkeypatch,
        [
            "Did you fall or have another injury?",
            "I did not fall.",
            "Avoid heavy lifting for five days.",
        ],
        role="unknown",
    )
    model = ContextModel(invalid=invalid)
    return {**args, "model": model}, model


@pytest.mark.asyncio
async def test_actual_capture_saves_question_context_once_and_replays_without_model(
    admission_case, monkeypatch
):
    f = admission_case
    args, model = await case(f, monkeypatch)
    result = await notes.capture_note(**args)
    assert result.fields is not None
    objective = result.fields["O - OBJECTIVE"]
    assert "Selected transcript source context" in objective
    assert "Did you fall or have another injury?" in objective
    assert objective.count("I did not fall.") == 1
    assert "no other injury" not in objective.lower()
    assert "unanswered" not in objective.lower()
    assert result.fields["P - PLAN"] == (
        "- Unidentified speaker: Avoid heavy lifting for five days."
    )
    assert len(rows(f, AIVisitNote)) == len(rows(f, AIVisitTranscript)) == 1
    assert await notes.capture_note(**{**args, "prepare": False}) == result
    assert await notes.capture_note(**args) == result
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_actual_capture_rejects_duplicate_source_with_no_note_or_model_retry(
    admission_case, monkeypatch
):
    f = admission_case
    args, model = await case(f, monkeypatch, invalid=True)
    with pytest.raises(notes.CaptureNoteUnconfirmed):
        await notes.capture_note(**args)
    assert len(rows(f, AIVisitNote)) == 0
    assert len(rows(f, AIVisitTranscript)) == 1
    assert len(model.calls) == 1
