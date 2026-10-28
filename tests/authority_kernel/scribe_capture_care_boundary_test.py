"""Explicit selection duties; retained actual failure is never a new success.

These controls verify the prompt boundary and exact source rendering. They do
not prove that a model makes the required semantic selections; actual-model
acceptance remains a separate gate.
"""

import hashlib
import json
from pathlib import Path

import pytest

from app.services import scribe_capture_history_grounding as history
from app.services import scribe_capture_plan_grounding as plans


@pytest.mark.parametrize("speaker", ["Patient", "Doctor", "Unknown"])
@pytest.mark.parametrize("has_plans", [True, False])
def test_complete_clinical_requests_and_prospective_eligibility_are_explicit(
    speaker, has_plans
):
    fields = ("Subjective", "Objective", "Assessment", "Plan")
    g = history.ReportedHistoryGrounding.for_capture(
        fields,
        ("Plan",),
        (plans.PlanSource.speech(speaker, "Independent source content."),),
        patient_only=True,
    )
    prompt = g.prompt(has_plans=has_plans)
    assert "Only these existing note fields use reported_history_source_refs" in prompt
    assert (
        "questions and requests are relevant source context independently of whether a reply was recorded"
        in prompt
    )
    assert (
        "Select each complete relevant request and all its actual later responses"
        in prompt
    )
    assert "Do not drop a request after selecting a narrower reply" in prompt
    assert "or expand that reply to cover the other requested information" in prompt
    assert "A question never supplies its answer" in prompt
    assert "Do not classify selected entries as questions or answers" in prompt
    boundary = (
        "Apply the existing Plan eligibility boundary before any history omission rule"
    )
    assert boundary in prompt
    assert (
        "A prospective source must contain an explicit future care decision or its implementation"
        in prompt
    )
    assert (
        "a report of past or current medication behavior are not prospective care by themselves"
        in prompt
    )
    assert "Future tense addressed to a note writer or software" in prompt
    assert (
        "only if it also contains a separate explicit future care decision or implementation"
        in prompt
    )
    assert prompt.index(boundary) < prompt.index(
        "omission rules never remove those eligible prospective sources"
    )
    assert (
        "negated, conditional, corrected or repeated decisions and care commitments"
        in prompt
    )
    assert "Independent source content." not in prompt


@pytest.mark.parametrize(
    "speaker,text",
    [
        (
            "Doctor",
            "Put this in the note: we will not begin a new medicine; we will review the symptoms next week.",
        ),
        (
            "Patient",
            "Please keep the history accurate. I will record each episode and bring the diary to follow-up.",
        ),
        (
            "Patient",
            "I did not take a new medicine yesterday. If symptoms return, I will follow the agreed return precautions.",
        ),
        (
            "Doctor",
            "The draft needs a correction. The earlier procedure is cancelled; we will discuss alternatives tomorrow.",
        ),
    ],
)
def test_genuine_mixed_care_decisions_and_commitments_keep_complete_sources(
    speaker, text
):
    sources = (plans.PlanSource.speech(speaker, text),)
    g = history.ReportedHistoryGrounding.for_capture(
        ("Objective", "Plan"), ("Plan",), sources, patient_only=True
    )
    value = {
        "fields": {"Objective": "", "Plan": ""},
        "plan_source_refs": {"Plan": [1]},
        history.KEY: {"Objective": {"source_refs": []}},
    }
    raw, grounded = g.resolve(json.dumps(value), max_bytes=20000)
    _, care = plans.PlanGrounding(("Plan",), sources).resolve(raw, max_bytes=20000)
    assert (
        care["Plan"]
        == f"- {sources[0].attribution.removeprefix('Transcript — ')}: {text}"
    )
    assert text not in grounded["Objective"]


def test_actual_editorial_plan_failure_and_partial_context_gap_remain_failed():
    fixture = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "tests/fixtures/capture_care_boundary_actual01.json"
        ).read_text()
    )
    assert fixture["synthetic_only"]
    assert fixture["sourceHead"] == "19817cfbef01205ba117c4aab3106c3b6b44bab1"
    assert hashlib.sha256(fixture["raw"].encode()).hexdigest() == fixture["rawSHA256"]
    value = json.loads(fixture["raw"])
    assert 43 in value["plan_source_refs"]["Plan"]
    assert fixture["planInclusion"] == {
        "passed": False,
        "missingWholeSourceRefs": [],
        "unexpectedSourceRefs": [43],
        "selectedPlanRefs": [33, 34, 35, 36, 37, 39, 43],
    }
    assert fixture["optionalQuestionContext"]["failures"] == [
        {"field": "Objective", "sourceRef": 14}
    ]
    assert 14 not in value[history.KEY]["Objective"]["source_refs"]
    assert 16 in value[history.KEY]["Objective"]["source_refs"]
    assert fixture["modelCalls"] == 1 and fixture["databaseConnections"] == 0
    assert fixture["fullClinicalAcceptance"] is False
