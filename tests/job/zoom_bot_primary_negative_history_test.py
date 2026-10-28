"""Known duplicate quotation output must fail without changing its content."""

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app.services import zoom_bot_note_grounding as bot
from app.services import zoom_bot_source_constraints as constraints

from tests.job.zoom_bot_test_support import context_selection, primary_selection

ROOT = Path(__file__).resolve().parents[2] / "tools/zoom-note-fidelity"
SOAP = {
    "Subjective": "History",
    "Objective": "Data",
    "Assessment": "Impression",
    "Plan": "Care",
}


def test_retained_r10_quote_pile_does_not_satisfy_primary_history_contract():
    retained = json.loads((ROOT / "bot-r10-negative-duplication.json").read_text())
    source = next(
        case
        for case in json.loads((ROOT / "named-cases.json").read_text())["cases"]
        if case["id"] == "R2"
    )
    g = bot.BotGrounding.build(
        NS(transcript_data=[NS(**entry) for entry in source["entries"]]),
        SOAP,
        patient_only=True,
    )
    raw = json.loads(retained["raw"])
    raw["source_context_refs"] = context_selection(g)
    if getattr(g, "primary", None) is not None and g.primary.target:
        raw["primary_history_source_kinds"] = primary_selection(g, [])
    # Preserve the competing prose unchanged; a missing new envelope key must
    # not be the reason this retained failure rejects.
    with pytest.raises(bot.BotNoteContractInvalid):
        g.resolve(json.dumps(raw), SOAP, require_plan_reason=True)


def test_patient_negative_belongs_in_subjective_once_without_objective_quote_pile():
    entries = (constraints.Source("Patient", "I have never fainted."),)
    note = dict.fromkeys(SOAP, "")
    note["Subjective"] = "I have never fainted."
    constraints.validate(note, entries, ("Plan",), {}, ())
    note["Objective"] = 'Supporting quotation: "I have never fainted."'
    with pytest.raises(constraints.BotSourceConstraintInvalid):
        constraints.validate(note, entries, ("Plan",), {}, ())


def test_unverified_negative_never_moves_into_patient_only_subjective():
    entries = (
        constraints.Source(
            "Unverified speaker (shared participant)", "I have never fainted."
        ),
    )
    note = dict.fromkeys(SOAP, "")
    note["Objective"] = "I have never fainted."
    constraints.validate(note, entries, ("Plan",), {}, ("Objective",))
    assert constraints.negative_history_field(entries, SOAP, ("Plan",)) == "Objective"
    assert (
        constraints.negative_history_field(
            entries, {"Subjective": "History", "Plan": "Care"}, ("Plan",)
        )
        is None
    )
    assert note["Subjective"] == ""
