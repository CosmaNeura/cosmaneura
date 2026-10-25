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


def test_question_without_answer_can_leave_findings_unmentioned():
    assert grade(6, "Not mentioned in transcript.")["passed"]


def test_actual_source_quotation_is_accepted():
    assert grade(4, GOOD[2] + ' "There was no fall."')["passed"]


PERSON_REPORTS = [
    entry["content"] for entry in CASES["RET-07-person-and-role-boundary"]["entries"]
]


def source_blocks(reports):
    return "Reported transcript history:\n" + "\n\n".join(
        "Transcript — Unidentified speaker:\n" + report for report in reports
    )


def test_separate_complete_source_blocks_preserve_self_identified_person_scope():
    text = source_blocks(PERSON_REPORTS)
    assert grade(8, text)["passed"]
    assert not grade(8, text + "\n\nTaylor had a headache yesterday.")["passed"]


@pytest.mark.parametrize(
    "reports",
    [
        [" ".join(PERSON_REPORTS)],  # Merged reports are not one source block.
        [part for report in PERSON_REPORTS for part in report.split(". ")],
        [
            "I am Morgan. My right shoulder pain is two out of ten.",
            "I am Taylor. I had a headache yesterday and none today.",
        ],
        [
            "I am Morgan.",
            "I had a headache yesterday and none today.",
            "I am Taylor.",
            "My right shoulder pain is two out of ten.",
        ],
        [PERSON_REPORTS[0], PERSON_REPORTS[0]],
        [PERSON_REPORTS[0], PERSON_REPORTS[1].replace("two", "four")],
    ],
)
def test_person_scope_cannot_cross_or_rewrite_source_blocks(reports):
    assert not grade(8, source_blocks(reports))["passed"]


@pytest.mark.parametrize("contrast", ["but", "however", "yet"])
def test_negation_does_not_cross_contrast_into_positive_findings(contrast):
    result = grade(
        4,
        f"Unidentified speaker: no fall, {contrast} numbness and weakness are present. No radiating arm pain.",
    )
    assert not result["passed"]
    assert {"negative-numbness", "negative-weakness"} <= set(result["failures"])


def test_appended_positive_counterclaim_cannot_hide_behind_valid_denial():
    assert not grade(4, GOOD[2] + " Numbness and weakness are present.")["passed"]
    assert grade(
        4, "Unidentified speaker: no fall, numbness, weakness or radiating arm pain."
    )["passed"]


def test_r2_earlier_negatives_and_fluid_measurement_cannot_be_lost_to_late_summary():
    complete = (
        "Unidentified speaker: 12 days of mild dizziness, lasting 30 seconds. "
        "2 episodes yesterday. Blood pressure 118/72. 4 glasses of water daily. "
        "No fainting. No chest pain, no shortness of breath, no fever. "
        "Mild headache yesterday; no headache today. "
        "No medication changes; current medication list unspecified."
    )
    assert grade(0, complete)["passed"]
    assert "negative-fever" in grade(0, complete.replace(", no fever", ""))["failures"]
    assert (
        "daily-fluid-intake"
        in grade(0, complete.replace("4 glasses of water daily. ", ""))["failures"]
    )


def test_future_medication_decision_does_not_replace_reported_history():
    future_only = "Unidentified speaker: no medication changes are planned. Current medication list not specified."
    assert "medications-unchanged" in grade(5, future_only)["failures"]
    assert grade(
        5, future_only + " I have not started, stopped, or changed any medicine."
    )["passed"]


def test_new_medication_history_cannot_gain_a_visit_only_time_limit():
    case = {**CASES["R3"], "checks": ["r3_numbers"]}
    base = (
        "Unidentified speaker: right shoulder pain for 4 days. No fever or chest pain. "
        "Current medication list unknown. "
    )
    assert evaluate(case, {"O - OBJECTIVE": base + "No new medicine was started."})[
        "passed"
    ]
    assert (
        "new-medicine-history-retained"
        in evaluate(
            case,
            {"O - OBJECTIVE": base + "No new medicine was started during this visit."},
        )["failures"]
    )


def test_r3_change_report_cannot_stand_in_for_an_established_current_list():
    case = {**CASES["R3"], "checks": ["r3_numbers"]}
    fields = {
        "O - OBJECTIVE": "Unidentified speaker: right shoulder pain for 4 days. No fever or chest pain.",
        "Medications": "Selected current medication reports: I have not taken any new medicine.",
    }
    assert "medication-list-unknown" in evaluate(case, fields)["failures"]
    fields["Medications"] = (
        "Current medication list is unverified. Unidentified speaker: I have not taken any new medicine."
    )
    assert evaluate(case, fields)["passed"]


BAD = [
    (2, GOOD[0] + " (source 99)"),
    (2, GOOD[0] + ' "I have no fever."'),
    (2, "Unidentified speaker: current pain 4/10; 2/10 is a future goal."),
    (2, "Unidentified speaker: current pain 4/10, corrected from 2/10."),
    (2, "Unidentified speaker: earlier pain 2/10, corrected to 4/10."),
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
    (6, "Unidentified speaker: denies weakness or numbness."),
    (6, "Unidentified speaker: normal neurological findings."),
    (7, "Unidentified speaker: confirmed pain 2/10, corrected from 4/10."),
    (
        7,
        "Unidentified speaker: pain is 2/10. Earlier it was 4/10. Weather is uncertain.",
    ),
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


@pytest.mark.parametrize("role", ["patient", "clinician", "doctor", "provider"])
def test_unknown_role_caveat_in_another_field_does_not_permit_attributed_claim(role):
    result = grade(
        2, GOOD[0], **{"Medications": f"The {role} stated no medication changes."}
    )
    assert "no-manufactured-speaker-role" in result["failures"]


def test_invented_plan_is_rejected():
    assert not grade(
        9, GOOD[7], **{"P - PLAN": "Take ibuprofen and follow up tomorrow."}
    )["passed"]
