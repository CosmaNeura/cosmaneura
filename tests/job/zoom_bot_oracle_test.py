"""Adversarial semantic calibration, including preserved actual failed notes."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "bot_fidelity_oracle", ROOT / "tools/zoom-note-fidelity/oracle.py"
)
ORACLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ORACLE)
CASES = {
    c["id"]: c
    for c in json.loads(
        (ROOT / "tools/zoom-note-fidelity/named-cases.json").read_text()
    )["cases"]
}
GOOD = {
    "R2": {
        "Subjective": "Mild dizziness began 12 days ago, corrected from 20 days. Episodes last 30 seconds, not 30 minutes, on standing quickly. There were 2 mild episodes yesterday, not 7. No fainting. No chest pain, shortness of breath or fever. Mild headache yesterday, no headache today. Can walk and carry out the normal routine. Drinks 4 glasses of water daily. No medicines started, stopped or changed.",
        "Objective": "Reported home BP 118/72, corrected from 180/72. No examination was reported.",
        "Assessment": "Brief mild dizziness on standing; orthostatic symptoms are a tentative impression based on reported history, not a confirmed diagnosis.",
        "Plan": "Rise slowly. Reason not stated in transcript.\nKeep a brief symptom diary of when dizziness occurs and how long it lasts. Reason not stated in transcript.\nMaintain regular fluids. Reason not stated in transcript.\nNo new medicine is planned. Reason not stated in transcript.\nFollow up is recommended in 10 days, not 10 weeks. Reason not stated in transcript.",
    },
    "R3": {
        "Subjective": "Right shoulder discomfort after moving boxes 4 days ago; corrected from left. Mild at rest and worse reaching overhead. Current pain 2/10, corrected from 4/10. No fall. No numbness or weakness. No pain radiating down the arm. No fever or chest pain. Can hold a cup and get dressed. Rest from heavy lifting helped. Has not taken any new medicine.",
        "Objective": "No examination or vital signs were reported.",
        "Assessment": "Reported right shoulder discomfort; a strain is a tentative impression based on the lifting history. Other causes have not been excluded by examination or testing.",
        "Plan": "Avoid heavy lifting for 5 days. Reason not stated in transcript.\nUse comfortable gentle movement. Reason not stated in transcript.\nTrack whether overhead reaching becomes easier. Reason not stated in transcript.\nNo new medicine is prescribed. Reason not stated in transcript.\nFollow up is recommended in 7 days, corrected from 17. Reason not stated in transcript.",
    },
}


@pytest.mark.parametrize("case_id", ["R2", "R3"])
def test_supported_prose_with_explicit_limits_passes_finite_checks(case_id):
    result = ORACLE.evaluate(CASES[case_id], GOOD[case_id])
    assert result["passed"], result
    assert result["independent_content_review_required"]


@pytest.mark.parametrize(
    "case_id,field,old,new,expected",
    [
        ("R2", "Subjective", " or fever", "", "negative-fever"),
        ("R3", "Subjective", "No fall.", "", "negative-fall"),
        (
            "R3",
            "Subjective",
            "Current pain 2/10, corrected from 4/10.",
            "Current pain 4/10.",
            "current-pain-2/10",
        ),
        (
            "R3",
            "Assessment",
            "Other causes have not been excluded by examination or testing.",
            "No evidence of fracture, dislocation or neurological compromise.",
            "no-unsupported-diagnostic-exclusion",
        ),
        (
            "R3",
            "Objective",
            "No examination or vital signs were reported.",
            "Physical examination was normal.",
            "no-manufactured-normal-exam",
        ),
        (
            "R3",
            "Plan",
            "Follow up is recommended in 7 days",
            "A follow-up appointment is scheduled in 7 days",
            "recommendation-not-booking",
        ),
        (
            "R2",
            "Plan",
            "Rise slowly.",
            "Rise slowly to reduce dizziness.",
            "no-inferred-plan-rationale",
        ),
        (
            "R3",
            "Plan",
            "No new medicine is prescribed.",
            "No new medicine is prescribed because symptoms are mild.",
            "no-inferred-plan-rationale",
        ),
        (
            "R2",
            "Plan",
            "Keep a brief symptom diary of when dizziness occurs and how long it lasts.",
            "Use the corrected count in the diary.",
            "correction-not-new-patient-task",
        ),
        (
            "R3",
            "Subjective",
            "Has not taken any new medicine.",
            "No new medicine is planned.",
            "new-medicine-history-retained",
        ),
        (
            "R2",
            "Subjective",
            "No medicines started, stopped or changed.",
            "No current medications.",
            "no-empty-medication-claim",
        ),
    ],
)
def test_single_semantic_regressions_are_rejected(case_id, field, old, new, expected):
    fields = dict(GOOD[case_id])
    assert old in fields[field]
    fields[field] = fields[field].replace(old, new)
    assert expected in ORACLE.evaluate(CASES[case_id], fields)["failures"]


@pytest.mark.parametrize(
    "case_id,expected",
    [
        (
            "R2",
            {
                "negative-fever",
                "correction-not-new-patient-task",
                "no-inferred-plan-rationale",
            },
        ),
        ("R3", {"no-unsupported-diagnostic-exclusion", "no-inferred-plan-rationale"}),
    ],
)
def test_preserved_actual_baselines_retain_known_findings(case_id, expected):
    result = ORACLE.evaluate(CASES[case_id], CASES[case_id]["baselineFields"])
    assert not result["passed"] and expected <= set(result["failures"])


def test_unknown_list_field_requirement_is_not_removed_when_schema_has_field():
    fields = {**GOOD["R3"], "Medications": "No new medicine taken."}
    assert "medication-list-unknown" in ORACLE.evaluate(CASES["R3"], fields)["failures"]


def test_unidentified_reports_do_not_get_a_patient_role():
    case = {
        **CASES["R3"],
        "entries": [
            {**entry, "speaker": "Unknown"} for entry in CASES["R3"]["entries"]
        ],
    }
    fields = {
        **GOOD["R3"],
        "Subjective": "The patient reports right shoulder pain 2/10.",
    }
    failures = ORACLE.evaluate(case, fields)["failures"]
    assert (
        "no-manufactured-speaker-role" in failures
        and "patient-only-not-overridden" in failures
    )


def test_reason_absence_does_not_excuse_invented_reason_in_same_bullet():
    fields = {
        **GOOD["R3"],
        "Plan": "Avoid lifting to prevent reinjury. Reason not stated in transcript.",
    }
    assert (
        "no-inferred-plan-rationale" in ORACLE.evaluate(CASES["R3"], fields)["failures"]
    )
