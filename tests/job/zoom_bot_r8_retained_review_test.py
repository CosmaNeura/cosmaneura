"""Original model outputs remain failed evidence; upgrades here are handcrafted."""

import json
from pathlib import Path
from types import SimpleNamespace as NS

from app.services import zoom_bot_note_grounding as bot

from tests.job.zoom_bot_test_support import (
    SOAP,
    context_selection,
    primary_selection,
)

ROOT = Path(__file__).resolve().parents[2] / "tools/zoom-note-fidelity"


def retained(case_id, refs):
    cases = json.loads((ROOT / "named-cases.json").read_text())["cases"]
    entries = next(case["entries"] for case in cases if case["id"] == case_id)
    outputs = json.loads((ROOT / "bot-r8-retained-responses.json").read_text())["cases"]
    raw = json.loads(next(case["raw"] for case in outputs if case["id"] == case_id))
    source = NS(transcript_data=[NS(**entry) for entry in entries])
    grounding = bot.BotGrounding.build(source, SOAP, patient_only=True)
    # Handcrafted compatibility rendering, never new model evidence. Original
    # outputs and their failed statuses remain preserved in the fixture.
    if getattr(grounding, "context", None) is not None:
        raw["source_context_refs"] = context_selection(grounding, refs)
    if getattr(grounding, "primary", None) is not None and grounding.primary.target:
        raw["fields"][grounding.primary.target] = ""
        raw["primary_history_source_kinds"] = primary_selection(
            grounding,
            [
                index
                for index in grounding.primary.allowed
                if index not in raw["plan_source_refs"]["Plan"]
            ],
        )
    return grounding.resolve(json.dumps(raw), SOAP, require_plan_reason=True), entries


def test_r2_conservative_multipart_context_preserves_original_clinical_facts():
    note, entries = retained("R2", [])
    text = json.dumps(note)
    # Multipart context may remain even when each reply was explicit.
    assert entries[11]["content"] in text
    assert entries[13]["content"] in text
    assert entries[14]["content"] in text
    assert entries[23]["content"] in text


def test_r3_empty_model_context_keeps_server_owned_multipart_quote():
    note, entries = retained("R3", [])
    assert entries[12]["content"] in json.dumps(note)
    assert entries[14]["content"] in json.dumps(note)
    # This is a handcrafted compatibility rendering of unchanged failed raw
    # evidence, not a new successful model result.
    corrected, _ = retained("R3", [13, 15])
    assert entries[12]["content"] in corrected["Objective"]
    assert entries[14]["content"] in corrected["Subjective"]
    assert "No other injury" not in json.dumps(corrected)
    assert entries[40]["content"] not in corrected["Plan"]
