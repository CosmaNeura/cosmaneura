"""Actual synthetic SOAP regression; model proof lives in the retained live receipt."""

import importlib.util
import json
from pathlib import Path

import pytest

from app.services import scribe_capture_notes as notes
from app.services import scribe_capture_plan_grounding as plans

from app.models.ai_visit_transcript import TranscriptEntry
from app.models.note_type_customization import NoteTypeCustomization

ROOT = Path(__file__).resolve().parents[2]
CASE = json.loads(
    (ROOT / "tools/capture-retention/desktop13-synthetic.json").read_text()
)


def test_actual_known_role_soap_omission_is_rejected_by_fixed_oracle():
    spec = importlib.util.spec_from_file_location(
        "capture_finite_oracle", ROOT / "tools/capture-retention/oracle.py"
    )
    oracle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oracle)
    assert CASE["synthetic_only"] and len(CASE["entries"]) == 39
    result = oracle.evaluate(CASE, CASE["archived_faulty_fields"], patient_only=True)
    assert result["failures"] == ["medication-list-unknown"]


def test_known_roles_without_medication_heading_keep_list_uncertainty_instruction():
    entries = [TranscriptEntry(**value) for value in CASE["entries"]]
    context = notes.capture_retention_context(
        entries,
        NoteTypeCustomization(**CASE["customization"]),
        field_names=tuple(CASE["schema"]),
    )
    assert (
        "18 Patient-labeled entries; 0 entries with unidentified speaker role"
        in context
    )
    assert "explicitly document an unspecified current medication list" in context
    assert "no Medications heading and speaker roles are known" in context
    assert "Do not write that the patient takes no medications" in context
    assert "NO role-identified Patient speech" not in context
    assert (
        "118/72" not in context
    )  # No fixture facts are inserted into runtime instructions.


@pytest.mark.parametrize(
    "text,clipped",
    [
        ("Do not take aspirin.", "take aspirin."),
        ("Take aspirin. I am pregnant.", "Take aspirin."),
        ("Take ibuprofen. My kidney function is poor.", "Take ibuprofen."),
        ("Start this drug. I am breastfeeding.", "Start this drug."),
        ("Proceed. My diagnosis is hemophilia.", "Proceed."),
    ],
)
def test_selected_instruction_keeps_complete_context_and_rejects_excerpt_channel(
    text, clipped
):
    g = plans.PlanGrounding(("Plan",), (plans.PlanSource.speech("Doctor", text),))
    _, rendered = g.resolve(
        json.dumps({"fields": {"Plan": ""}, "plan_source_refs": {"Plan": [1]}}),
        max_bytes=262144,
    )
    assert rendered["Plan"] == "- Clinician: " + text
    with pytest.raises(plans.PlanGroundingInvalid):
        g.resolve(
            json.dumps(
                {
                    "fields": {"Plan": ""},
                    "plan_source_refs": {
                        "Plan": [{"source_index": 1, "text": clipped}]
                    },
                }
            ),
            max_bytes=262144,
        )
