"""Reference-only instructions must not blank unrelated clinical narratives.

Archived actual failures below are evidence, not new-source model successes.
"""

import hashlib
import json
from pathlib import Path

import pytest

from app.services import scribe_capture_history_grounding as history
from app.services import scribe_capture_plan_grounding as plans


@pytest.mark.parametrize(
    "speaker,patient_only,expected",
    [
        ("Patient", True, ("O - OBJECTIVE",)),
        ("Unknown", True, ("O - OBJECTIVE", "ROS")),
        ("Unknown", False, ("O - OBJECTIVE",)),
    ],
)
def test_scope_names_only_grounded_fields_and_keeps_remaining_narrative_duties(
    speaker, patient_only, expected
):
    g = history.ReportedHistoryGrounding.for_capture(
        ("S - SUBJECTIVE", "O - OBJECTIVE", "ROS", "A - ASSESSMENT", "P - PLAN"),
        ("P - PLAN",),
        (plans.PlanSource.speech(speaker, "Separate synthetic source text."),),
        patient_only=patient_only,
    )
    assert g.fields == expected
    prompt = g.prompt(has_plans=True)
    assert (
        "Only these existing note fields use reported_history_source_refs and empty generated strings: "
        + json.dumps(expected)
        + "."
        in prompt
    )
    assert (
        "Apply the reference-selection, empty-string and omission instructions below only to that exact field list"
        in prompt
    )
    assert (
        "Every other original schema field continues to use its original narrative instructions and source preferences unless the prospective-plan contract applies to it"
        in prompt
    )
    assert "retain the supported Patient-labeled history and symptoms" in prompt
    assert (
        "explicit negatives, corrections, quantified history and prior/current temporal distinctions"
        in prompt
    )
    assert "if none was stated, mark it not mentioned" in prompt
    assert (
        "omission rules never remove those eligible prospective sources" in prompt
    )
    assert "A decision with a different scope remains separate" in prompt
    assert "Do not apply history recap omissions to plan_source_refs" in prompt
    assert "Separate synthetic source text." not in prompt
    contract = json.loads(
        prompt.split(history.CONTRACT_START, 1)[1].split(history.CONTRACT_END, 1)[0]
    )
    assert contract["reported_history_fields"] == list(expected)


def test_retained_actual_empty_narrative_failures_are_not_regraded_or_model_successes():
    fixture = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "tests/fixtures/capture_narrative_scope_actual01.json"
        ).read_text()
    )
    assert (
        fixture["synthetic_only"]
        and fixture["source"] == "1f355e583fa306626bc86e29069363c2ce573a1e"
    )
    assert len(fixture["rows"]) == 3
    for row in fixture["rows"]:
        assert hashlib.sha256(row["raw"].encode()).hexdigest() == row["rawSHA256"]
        value = json.loads(row["raw"])
        assert value["fields"]["Subjective"] == value["fields"]["Assessment"] == ""
        assert row["subjectiveSchema"] == {"type": "string"}
        assert row["objectiveSchema"] == {"type": "string", "enum": [""]}
        assert not row["originalOracle"]["passed"]
        assert len(row["originalOracle"]["failures"]) == 11
        assert not row["originalPlanInclusion"]["passed"]
        assert row["modelCalls"] == 1
    assert fixture["providerCalls"] == 3 and fixture["databaseConnections"] == 0
    assert fixture["fullClinicalAcceptance"] is False
