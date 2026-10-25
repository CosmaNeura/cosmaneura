"""Opt-in actual model plus complete disposable capture-note persistence.

The ordinary contract suite remains secretless. This explicitly separate test
requires a scoped staging model credential and the existing owned-DB guard.
"""

import importlib.util
import json
import os
from pathlib import Path

import pytest

from app.services import scribe_capture_notes as notes
from app.services.scribe_workflow import CaptureTranscript

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_transcript import TranscriptEntry
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.note_type_customization import NoteTypeCustomization, SubjectiveSource
from sqlmodel import Session

from tests.authority_kernel.scribe_capture_notes_test import (
    admission_case as _admission_case,
)
from tests.authority_kernel.scribe_capture_notes_test import (
    capture_engine as _capture_engine,
)
from tests.authority_kernel.scribe_capture_notes_test import rows, setup

admission_case = _admission_case
capture_engine = _capture_engine
TOOLS = Path(__file__).resolve().parents[2] / "tools/capture-retention"
FIXTURE = json.loads((TOOLS / "fixtures.json").read_text())


def test_source_role_context_does_not_infer_from_spoken_introductions():
    entries = [
        TranscriptEntry(
            speaker="Unknown", content="I am Doctor Lane.", startTime=0, endTime=1000
        )
    ]
    context = notes.capture_retention_context(entries, NoteTypeCustomization())
    assert (
        "0 Patient-labeled entries; 1 entries with unidentified speaker role" in context
    )
    assert "NO role-identified Patient speech" in context
    assert "I am Doctor Lane" not in context


def test_known_patient_and_provider_preferences_remain_distinct():
    entries = [
        TranscriptEntry(
            speaker="Patient", content="My pain is mild.", startTime=0, endTime=1000
        )
    ]
    assert "NO role-identified" not in notes.capture_retention_context(
        entries, NoteTypeCustomization()
    )
    entries[0].speaker = "Unknown"
    assert "NO role-identified" not in notes.capture_retention_context(
        entries,
        NoteTypeCustomization(subjective_source=SubjectiveSource.patient_and_doctor),
    )


@pytest.mark.asyncio
async def test_actual_model_retention_save_and_reopen(admission_case, monkeypatch):
    key = os.environ.get("SCRIBE_RETENTION_MODEL_KEY")
    destination = os.environ.get("SCRIBE_RETENTION_OUTPUT")
    if not key or not destination:
        pytest.skip("Opt-in scoped staging model credential/output required")
    output = Path(destination)
    assert not output.exists(), "Preserve prior results; select a fresh output path"
    f = admission_case
    args = await setup(f, monkeypatch)
    case = FIXTURE["cases"][1]  # Complete observed R3, roles remain Unknown.
    with Session(f.engine) as session:
        schema = session.get(DoctorNoteSchema, f.schema_ref)
        assert schema is not None
        schema.fields = FIXTURE["schema"]
        session.add(schema)
        session.commit()
    payload = f.client.result.model_dump(mode="json")
    payload["segments"] = [
        {
            "content": entry["content"],
            "speaker_id": "observed-single-speaker",
            "speaker_role": "unknown",
            "start_ms": index * 1000,
            "end_ms": (index + 1) * 1000,
            "confidence": None,
        }
        for index, entry in enumerate(case["entries"])
    ]
    f.client.result = CaptureTranscript.model_validate(payload)
    client = notes.openai.__class__(
        api_version="2025-03-01-preview",
        api_key=key,
        azure_endpoint="https://openaitestvhs.openai.azure.com/",
    )
    monkeypatch.setattr(notes, "openai", client)

    class ActualModel:
        def __init__(self):
            self.outputs = []

        async def complete(self, prompt, *, json_output):
            raw = await notes.ConfiguredNoteModel().complete(
                prompt, json_output=json_output
            )
            self.outputs.append(raw)
            with output.with_suffix(".model.json").open("x") as stream:
                os.chmod(stream.name, 0o600)
                json.dump(
                    {
                        "raw_response": raw,
                        "grounding": notes.GROUNDING,
                        "prompt": prompt,
                        "scope": "synthetic actual-model response before contract validation",
                    },
                    stream,
                    indent=2,
                )
            return raw

    model = ActualModel()
    args["model"] = model
    result = await notes.capture_note(**args)
    assert result.fields is not None
    spec = importlib.util.spec_from_file_location(
        "retention_oracle", TOOLS / "oracle.py"
    )
    assert spec and spec.loader
    oracle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oracle)
    evaluation = oracle.evaluate(case, result.fields)
    reopened = await notes.capture_note(**{**args, "prepare": False})
    retry = await notes.capture_note(**args)
    evidence = {
        "scope": "actual configured staging model; actual local PostgreSQL; synthetic downstream capture fixture",
        "oracle": evaluation,
        "resolved_fields": result.fields,
        "raw_responses": model.outputs,
        "model_call_count": len(model.outputs),
        "saved_row_count": len(rows(f, AIVisitNote)),
        "save_reopen_same": reopened == result == retry,
    }
    with output.open("x") as stream:
        os.chmod(output, 0o600)
        json.dump(evidence, stream, indent=2)
    assert len(model.outputs) == len(rows(f, AIVisitNote)) == 1
    assert reopened == result == retry
    assert evaluation["passed"], evaluation["failures"]
