"""Finite medication checks distinguish historical reports and future decisions."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "medication_scope_oracle", ROOT / "tools/capture-retention/oracle.py"
)
assert SPEC is not None and SPEC.loader is not None
oracle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(oracle)

HISTORY = "I have not started, stopped, or changed any medicine."
NO_NEW = "We are not starting a new medicine."
NO_CHANGES = "No medication changes planned."
UNKNOWN = "The current medication list was not specified."
CASE = {
    "checks": ["medications"],
    "entries": [
        {"speaker": "Patient", "content": HISTORY},
        {"speaker": "Doctor", "content": NO_NEW},
        {"speaker": "Doctor", "content": NO_CHANGES},
    ],
}


def evaluate(history=HISTORY, plan=None, *, heading="Plan"):
    return oracle.evaluate(
        CASE,
        {
            "Subjective": history + " " + UNKNOWN,
            heading: plan
            if plan is not None
            else f"- Clinician: {NO_NEW}\n- Clinician: {NO_CHANGES}",
        },
        required_plan_source_refs=(2, 3),
    )


def test_separate_history_and_both_prospective_decisions_pass():
    assert evaluate()["passed"]


@pytest.mark.parametrize("history", [NO_CHANGES, "No medication changes are planned."])
def test_planned_unchanged_does_not_satisfy_retrospective_history(history):
    assert "medications-unchanged" in evaluate(history=history)["failures"]


def test_no_new_medicine_does_not_cover_planned_stops_or_dose_changes():
    result = evaluate(plan=f"- Clinician: {NO_NEW}")
    assert result["failures"] == ["planned-source-3"]


def test_history_in_plan_does_not_satisfy_future_no_change_decision():
    result = evaluate(plan=f"- Clinician: {NO_NEW}\n- Patient: {HISTORY}")
    assert result["failures"] == ["planned-source-3"]


def test_future_statement_copied_to_history_does_not_satisfy_plan():
    result = evaluate(history=HISTORY + " " + NO_CHANGES, plan=f"- Clinician: {NO_NEW}")
    assert result["failures"] == ["planned-source-3"]


@pytest.mark.parametrize("heading", ["Medication plan", "Medication follow-up"])
def test_prospective_medication_heading_cannot_supply_missing_history(heading):
    result = evaluate(
        history="",
        plan=f"- Patient: {HISTORY}\n- Clinician: {NO_NEW}\n- Clinician: {NO_CHANGES}",
        heading=heading,
    )
    assert result["failures"] == ["medications-unchanged"]


def test_wrong_speaker_cannot_satisfy_source_bound_plan_expectation():
    result = evaluate(plan=f"- Clinician: {NO_NEW}\n- Patient: {NO_CHANGES}")
    assert result["failures"] == ["planned-source-3"]


@pytest.mark.parametrize("refs", [(0,), (4,), (True,), (2, 2)])
def test_invalid_expectation_is_not_silently_graded(refs):
    with pytest.raises(ValueError, match="invalid required plan source"):
        oracle.evaluate(CASE, {}, required_plan_source_refs=refs)
