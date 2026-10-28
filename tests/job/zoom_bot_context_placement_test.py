"""Literal history applies to custom fields; proven Plan placement is narrow."""

import json
from pathlib import Path

import pytest
from app.services import zoom_bot_note_grounding as bot
from app.services import zoom_bot_source_constraints as constraints
from app.services import zoom_bot_source_passages as passages
from pydantic import BaseModel

from app.models.ai_visit_transcript import TranscriptEntry

from tests.job.zoom_bot_test_support import SOAP, context_selection


class PromptTranscript(BaseModel):
    doctor_id: str
    transcript_data: list[TranscriptEntry]


@pytest.mark.parametrize(
    "heading",
    [
        "HPI",
        "Chief complaint",
        "ChiefComplaint",
        "History of present illness",
        "Symptoms discussed",
    ],
)
def test_patient_negatives_remain_required_in_custom_history_sections(heading):
    entries = (constraints.Source("Patient", "I have no fever."),)
    fields = {heading: "", "Plan": ""}
    assert constraints.negative_history_field(entries, fields, ("Plan",)) == heading
    with pytest.raises(constraints.BotSourceConstraintInvalid):
        constraints.validate(fields, entries, ("Plan",), {}, ())
    fields[heading] = "I have no fever."
    constraints.validate(fields, entries, ("Plan",), {}, ())


@pytest.mark.parametrize(
    "heading", ["Subjective", "HPI", "Chief complaint", "History of present illness"]
)
def test_unknown_roles_cannot_gain_patient_only_history_by_alias(heading):
    entries = (constraints.Source("Unverified speaker (raw)", "I have no fever."),)
    assert constraints.negative_history_field(entries, {heading: ""}, ()) is None
    assert (
        constraints.negative_history_field(entries, {heading: "", "Objective": ""}, ())
        == "Objective"
    )


@pytest.mark.parametrize(
    "decision",
    [
        "We are not starting a new medicine.",
        "We are not changing treatment.",
        "No new treatment.",
        "No new medication is planned.",
    ],
)
def test_exact_validated_prospective_negative_plan_does_not_need_history_duplicate(
    decision,
):
    entries = (constraints.Source("Unverified speaker (raw)", decision),)
    note = {"Objective": "", "Plan": decision}
    constraints.validate(note, entries, ("Plan",), {"Plan": [1]}, ())
    # A candidate or model mention outside validated Plan references is not
    # enough to waive source retention in an existing history destination.
    if constraints.required_context(entries):
        with pytest.raises(constraints.BotSourceConstraintInvalid):
            constraints.validate(note, entries, ("Plan",), {}, ())


@pytest.mark.parametrize(
    "source",
    [
        "No new medicine was started.",
        "I have not taken any new medicine.",
        "We are not changing treatment. I have no fever.",
    ],
)
def test_historical_or_mixed_negatives_cannot_be_waived_by_arbitrary_plan_ref(source):
    entries = (constraints.Source("Unverified speaker (raw)", source),)
    with pytest.raises(constraints.BotSourceConstraintInvalid):
        constraints.validate(
            {"Objective": "", "Plan": source}, entries, ("Plan",), {"Plan": [1]}, ()
        )


@pytest.mark.parametrize("refs", [[True], [0], [2]])
def test_invalid_plan_reference_cannot_waive_required_history(refs):
    entries = (
        constraints.Source(
            "Unverified speaker (raw)", "We are not starting a new medicine."
        ),
    )
    with pytest.raises(constraints.BotSourceConstraintInvalid):
        constraints.validate(
            {"Objective": "", "Plan": entries[0].content},
            entries,
            ("Plan",),
            {"Plan": refs},
            (),
        )


def test_retained_actual_r7_selection_is_not_rejected_for_plan_negative_placement():
    root = Path(__file__).resolve().parents[2] / "tools/zoom-note-fidelity"
    recorded = json.loads((root / "r7-provider-input-equivalent.json").read_text())
    retained = json.loads((root / "bot-r11-retained-r7.json").read_text())
    source = PromptTranscript(
        doctor_id="synthetic",
        transcript_data=[
            TranscriptEntry(**entry) for entry in recorded["candidateEntries"]
        ],
    )
    prepared, boundaries, _ = passages.prepare(source)
    g = bot.BotGrounding.build(prepared, SOAP, patient_only=True, boundaries=boundaries)
    # Keep the exact selections for the placement regression. New envelope and
    # empty patient-only-field contracts are supplied mechanically; this is not
    # a new model result or reclassification of the original rejected run.
    raw = json.loads(retained["raw"])
    if g.context.target:
        raw["source_context_refs"] = context_selection(g)
    for field in getattr(g, "empty_subjective", ()):
        raw["fields"][field] = ""
    note = g.resolve(json.dumps(raw), SOAP, require_plan_reason=True)
    decision = "We are not starting a new medicine."
    assert decision in note["Plan"] and decision not in note["Objective"]
    assert "I have not started, stopped or changed any medicine." in note["Objective"]
    assert "11872, not 18072" in note["Objective"]
