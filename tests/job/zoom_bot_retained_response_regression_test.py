"""Same source-level assertion is run against c86 and the repaired candidate."""

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app.services import zoom_bot_note_grounding as bot

from tests.job.zoom_bot_test_support import context_selection

ROOT = Path(__file__).resolve().parents[2]
SOAP = {
    "Subjective": "History",
    "Objective": "Data",
    "Assessment": "Impression",
    "Plan": "Care",
}


@pytest.mark.parametrize("case_id", ["R2", "R3"])
def test_retained_unsafe_response_is_rejected_before_note_fields(case_id):
    retained = json.loads(
        (ROOT / "tools/zoom-note-fidelity/bot-c86-retained-failures.json").read_text()
    )
    fixtures = json.loads(
        (ROOT / "tools/zoom-note-fidelity/named-cases.json").read_text()
    )["cases"]
    case = next(row for row in retained["cases"] if row["id"] == case_id)
    source = next(row for row in fixtures if row["id"] == case_id)
    transcript = NS(transcript_data=[NS(**entry) for entry in source["entries"]])
    g = bot.BotGrounding.build(transcript, SOAP, patient_only=True)
    raw = case["raw"]
    if getattr(g, "context", None) is not None and g.context.target:
        # Mechanical envelope upgrade only; keep the failing clinical text and
        # selections unchanged so missing new keys cannot make this test pass.
        upgraded = json.loads(raw)
        upgraded["source_context_refs"] = context_selection(g)
        raw = json.dumps(upgraded)
    with pytest.raises(bot.BotNoteContractInvalid):
        g.resolve(raw, SOAP, require_plan_reason=True)
