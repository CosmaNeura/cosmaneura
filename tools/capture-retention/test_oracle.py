"""Calibrate bounded semantic assertions against omissions and false assertions."""

import json
from pathlib import Path

import pytest
from oracle import evaluate

FIXTURE = json.loads(Path(__file__).with_name("fixtures.json").read_text())
CASES = {case["id"]: case for case in FIXTURE["cases"]}


def grade(index, text, **extra):
    case = FIXTURE["cases"][index]
    return evaluate(case, {"A - ASSESSMENT": text, **extra})


def test_archived_faulty_drafts_fail_for_observed_omissions():
    r2 = evaluate(CASES["R2"], CASES["R2"]["archived_faulty_draft"])
    r3 = evaluate(CASES["R3"], CASES["R3"]["archived_faulty_draft"])
    assert not r2["passed"] and "prior-mild-headache" in r2["failures"]
    assert not r3["passed"]
    assert {"current-pain-2/10", "negative-fall", "negative-radiation"} <= set(
        r3["failures"]
    )


GOOD = [
    "Unidentified speaker: current pain 2/10, corrected from the earlier value 4/10.",
    "Unidentified speaker: mild headache yesterday; no headache today.",
    "Unidentified speaker: no fall, no numbness, no weakness, no radiating arm pain.",
    "Unidentified speaker: no medication changes. Current medication list unknown.",
    "Unidentified speaker: questions about fall and radiation remain unanswered.",
    "Unidentified speaker: uncertain pain, possibly 2 or 4/10; unresolved.",
    "Unidentified speakers: Morgan had a headache yesterday and none today. Taylor has right shoulder pain 2/10.",
    "Unidentified speaker: shoulder discomfort. No examination findings supplied.",
]


@pytest.mark.parametrize("index,text", enumerate(GOOD, 2))
def test_supported_examples_pass(index, text):
    assert grade(index, text)["passed"]


def test_past_presence_with_explicit_current_absence_is_not_inverted_time():
    assert grade(
        3, "Unidentified speaker: mild headache was present yesterday but not today."
    )["passed"]
    assert not grade(
        3,
        "Unidentified speaker: mild headache yesterday. Severe headache present today.",
    )["passed"]


BAD = [
    (2, "Unidentified speaker: current pain 4/10; 2/10 is a future goal."),
    (2, "Unidentified speaker: mild pain. Marker 2 is a test."),
    (3, "Unidentified speaker: no headache today."),
    (
        3,
        "Unidentified speaker: mild headache yesterday. Severe headache present today.",
    ),
    (3, "Unidentified speaker: no headache yesterday; mild headache today."),
    (4, "Unidentified speaker: no numbness and no weakness."),
    (4, GOOD[2] + " Pain radiates down the arm."),
    (4, GOOD[2] + " Had a fall."),
    (5, "Unidentified speaker: no medication changes. Takes no medication."),
    (5, "Unidentified speaker: no current medications reported."),
    (6, "Unidentified speaker: no fall, no radiating arm pain."),
    (6, "Unidentified speaker: normal neurological findings."),
    (7, "Unidentified speaker: confirmed pain 2/10, corrected from 4/10."),
    (
        8,
        "Unidentified speakers: Taylor had a headache yesterday and none today. Morgan has right shoulder pain 2/10.",
    ),
    (9, "Unidentified speaker: all examinations are normal."),
]


@pytest.mark.parametrize("index,text", BAD)
def test_adversarial_number_time_negation_role_outputs_fail(index, text):
    assert not grade(index, text)["passed"]


def test_prospective_copy_does_not_satisfy_missing_narrative():
    assert not grade(2, "Unidentified speaker: mild pain.", **{"P - PLAN": GOOD[0]})[
        "passed"
    ]


def test_unknown_role_does_not_override_patient_only():
    assert not grade(2, GOOD[0], **{"S - SUBJECTIVE": "Patient reports pain 2/10."})[
        "passed"
    ]


def test_invented_plan_is_rejected():
    assert not grade(
        9, GOOD[7], **{"P - PLAN": "Take ibuprofen and follow up tomorrow."}
    )["passed"]
