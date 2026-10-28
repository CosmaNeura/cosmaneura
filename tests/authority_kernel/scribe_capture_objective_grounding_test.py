"""Objective selection cannot publish invented units or conceal source limits.

The retained model response is original evidence. Modified envelopes below are
adversarial resolver inputs, never new model successes or acceptance receipts.
"""

import hashlib
import json
from pathlib import Path

import pytest

from app.services import scribe_capture_history_grounding as history
from app.services import scribe_capture_plan_grounding as plans

ROOT = Path(__file__).resolve().parents[2]
ACTUAL = json.loads(
    (ROOT / "tests/fixtures/capture_objective_actual01.json").read_text()
)
CASE = json.loads(
    (ROOT / "tools/capture-retention/desktop13-synthetic.json").read_text()
)


def grounding(
    sources,
    *,
    fields=("Subjective", "Objective", "Assessment", "Plan"),
    patient_only=True,
):
    return history.ReportedHistoryGrounding.for_capture(
        fields, ("Plan",), sources, patient_only=patient_only
    )


def test_retained_known_role_model_response_cannot_bypass_objective_source_refs():
    assert ACTUAL["synthetic_only"] and CASE["synthetic_only"]
    assert hashlib.sha256(ACTUAL["raw"].encode()).hexdigest() == ACTUAL["raw_sha256"]
    raw = json.loads(ACTUAL["raw"])
    assert "118/72 mmHg" in raw["fields"]["Objective"]
    assert all("mmHg" not in entry["content"] for entry in CASE["entries"])
    sources = tuple(
        plans.PlanSource.speech(e["speaker"], e["content"]) for e in CASE["entries"]
    )
    g = grounding(sources)
    assert g.fields == ("Objective",)
    with pytest.raises(history.HistoryGroundingInvalid):
        g.resolve(ACTUAL["raw"], max_bytes=262144)


def test_valid_reference_envelope_rejects_competing_units_and_renders_exact_sources():
    sources = tuple(
        plans.PlanSource.speech(e["speaker"], e["content"]) for e in CASE["entries"]
    )
    g = grounding(sources)
    value = json.loads(ACTUAL["raw"])
    value[history.KEY] = {"Objective": {"source_refs": [27, 26]}}
    with pytest.raises(history.HistoryGroundingInvalid):
        g.resolve(json.dumps(value), max_bytes=262144)
    value["fields"]["Objective"] = ""
    remaining, rendered = g.resolve(json.dumps(value), max_bytes=262144)
    assert rendered["Objective"] == (
        history.CONTEXT_HEADING
        + "- Patient: "
        + CASE["entries"][25]["content"]
        + "\n- Clinician: "
        + CASE["entries"][26]["content"]
    )
    assert "118/72, not 180/72" in rendered["Objective"]
    assert "mmHg" not in rendered["Objective"]
    subjective = json.loads(remaining)["fields"]["Subjective"]
    assert CASE["entries"][25]["content"] not in subjective
    assert subjective == value["fields"]["Subjective"].replace(
        "- " + CASE["entries"][25]["content"] + "  \n", ""
    )
    assert json.loads(remaining)["plan_source_refs"] == value["plan_source_refs"]


def test_objective_deduplication_does_not_trim_qualifiers_or_merge_people():
    text = "My home reading was 118/72 yesterday."
    sources = (
        plans.PlanSource.speech("Patient", text),
        plans.PlanSource.speech("Patient", text),
        plans.PlanSource.speech("Other", "My home reading was 120/74."),
        plans.PlanSource.speech(
            "Patient", "The value was corrected from 180/72 to 118/72."
        ),
    )
    g = grounding(sources)
    subjective = (
        "- "
        + text
        + "\n- My home reading was 120/74.\n- The value was corrected from 180/72 to 118/72. Keep the qualifier.\n"
    )
    raw = {
        "fields": {"Subjective": subjective, "Objective": ""},
        history.KEY: {"Objective": {"source_refs": [1, 3, 4]}},
    }
    remaining, rendered = g.resolve(json.dumps(raw), max_bytes=262144)
    assert json.loads(remaining)["Subjective"] == subjective
    assert all(sources[index - 1].text in rendered["Objective"] for index in [1, 3, 4])


def test_objective_deduplication_requires_patient_only_and_complete_source_line():
    text = "My home reading was 118/72 yesterday."
    sources = (plans.PlanSource.speech("Patient", text),)
    for patient_only, subjective in [
        (False, "- " + text + "\n"),
        (True, "- Reported home reading 118/72.\n"),
        (True, "- " + text + " I am not sure.\n"),
    ]:
        g = grounding(sources, patient_only=patient_only)
        raw = {
            "fields": {"Subjective": subjective, "Objective": ""},
            history.KEY: {"Objective": {"source_refs": [1]}},
        }
        remaining, _ = g.resolve(json.dumps(raw), max_bytes=262144)
        assert json.loads(remaining)["Subjective"] == subjective


@pytest.mark.parametrize("patient_only", [True, False])
def test_known_objective_does_not_copy_other_history_or_move_its_medication_limit(
    patient_only,
):
    sources = (
        plans.PlanSource.speech(
            "Patient", "I had a headache yesterday but none today."
        ),
        plans.PlanSource.speech("Patient", "My home blood pressure was 118/72."),
    )
    names = ("Subjective", "Objective", "Medications", "ROS", "Plan")
    g = grounding(sources, fields=names, patient_only=patient_only)
    assert g.fields == ("Objective",) and not g.medication_fields
    raw = {
        "fields": dict.fromkeys(names, ""),
        "plan_source_refs": {"Plan": []},
        history.KEY: {"Objective": {"source_refs": [2]}},
    }
    _, rendered = g.resolve(json.dumps(raw), max_bytes=262144)
    assert sources[0].text not in rendered["Objective"]
    assert history.MEDICATION_LIST_UNVERIFIED not in rendered["Objective"]
    complete = dict(raw["fields"])
    complete.update(rendered)
    limited = history.with_medication_verification_limit(
        complete, plan_fields=("Plan",)
    )
    assert limited["Objective"] == rendered["Objective"]
    assert limited["Medications"] == history.MEDICATION_LIST_UNVERIFIED


def test_empty_known_objective_selection_is_not_a_false_absence_assertion():
    g = grounding(
        (plans.PlanSource.speech("Patient", "My home blood pressure was 118/72."),)
    )
    raw = {
        "fields": {"Objective": ""},
        history.KEY: {"Objective": {"source_refs": []}},
    }
    _, rendered = g.resolve(json.dumps(raw), max_bytes=262144)
    assert rendered["Objective"] == history.REVIEW_REQUIRED
    assert "not mentioned" not in rendered["Objective"].lower()


@pytest.mark.parametrize("indexes", [[0], [2], [True], [1.0], [1, 1]])
def test_known_objective_invalid_source_references_fail_closed(indexes):
    g = grounding((plans.PlanSource.speech("Patient", "My home reading was 118/72."),))
    raw = {
        "fields": {"Objective": ""},
        history.KEY: {"Objective": {"source_refs": indexes}},
    }
    with pytest.raises(history.HistoryGroundingInvalid):
        g.resolve(json.dumps(raw), max_bytes=262144)


def test_mixed_roles_scratch_and_uncertainty_keep_original_order_and_complete_text():
    sources = (
        plans.PlanSource.speech(
            "Unknown", "The reading might be 118/72 or 120/74. I am not sure."
        ),
        plans.PlanSource.speech("Other", "I measured 120/74 yesterday, not today."),
        plans.PlanSource.scratch(
            "The reported values conflict; no examination was performed."
        ),
    )
    g = grounding(sources)
    raw = {
        "fields": {"Objective": ""},
        history.KEY: {"Objective": {"source_refs": [3, 2, 1]}},
    }
    _, rendered = g.resolve(json.dumps(raw), max_bytes=262144)
    assert rendered["Objective"] == history.CONTEXT_HEADING + "\n".join(
        "- " + source.attribution.removeprefix("Transcript — ") + ": " + source.text
        for source in sources
    )
    assert "Normal vital signs" not in rendered["Objective"]


@pytest.mark.parametrize(
    "heading",
    ["Objective / Assessment", "Objective signature", "Objective patient consent"],
)
def test_excluded_or_combined_fields_do_not_become_objective_source_destinations(
    heading,
):
    g = grounding(
        (plans.PlanSource.speech("Patient", "My home blood pressure was 118/72."),),
        fields=(heading, "Subjective", "Plan"),
    )
    assert not g.fields


def test_grounded_objective_cannot_accept_a_text_excerpt_or_unknown_role_label():
    source = plans.PlanSource(
        "transcript", "Transcript — Signed-in clinician", "My home reading was 118/72."
    )
    g = grounding((source,))
    raw = {
        "fields": {"Objective": ""},
        history.KEY: {"Objective": {"source_refs": [1]}},
    }
    with pytest.raises(history.HistoryGroundingInvalid):
        g.resolve(json.dumps(raw), max_bytes=262144)
    g = grounding((plans.PlanSource.speech("Patient", source.text),))
    raw[history.KEY]["Objective"]["source_refs"] = [
        {"source_index": 1, "text": "118/72"}
    ]
    with pytest.raises(history.HistoryGroundingInvalid):
        g.resolve(json.dumps(raw), max_bytes=262144)


@pytest.mark.parametrize(
    "heading",
    [
        "Objective medications",
        "Objective medication history",
        "Objective medications/history",
    ],
)
def test_objective_medication_categories_accept_the_same_original_roles(heading):
    sources = (
        plans.PlanSource.speech("Patient", "I take synthetic TestMed each morning."),
        plans.PlanSource.speech("Doctor", "The prior medicine was stopped last month."),
        plans.PlanSource.speech(
            "Other", "I cannot confirm the complete medication list."
        ),
    )
    g = grounding(sources, fields=("Subjective", heading, "Plan"))
    assert g.fields == (heading,) and g.medication_fields == (heading,)
    raw = {
        "fields": {"Subjective": "", heading: "", "Plan": ""},
        "plan_source_refs": {"Plan": []},
        history.KEY: {
            heading: {"current_list": [1], "change_history": [2], "source_context": [3]}
        },
    }
    remaining, rendered = g.resolve(json.dumps(raw), max_bytes=262144)
    assert (
        rendered[heading]
        == history.MEDICATION_LIST_UNVERIFIED
        + "\n\nSelected medication source context:\n"
        + "\n".join(
            "- " + source.attribution.removeprefix("Transcript — ") + ": " + source.text
            for source in sources
        )
    )
    assert json.loads(remaining)["plan_source_refs"] == raw["plan_source_refs"]


@pytest.mark.parametrize(
    "heading", ["Subjective plan", "Subjective follow up", "Subjective recommendations"]
)
def test_history_dedup_cannot_salvage_competing_prospective_prose(heading):
    source = plans.PlanSource.speech("Patient", "My home reading was 118/72 yesterday.")
    fields = {heading: "- " + source.text + "\n", "Objective": ""}
    plan_fields = plans.prospective_fields(fields)
    assert plan_fields == (heading,)
    prospective = plans.PlanGrounding(plan_fields, (source,))
    plan_envelope = {"fields": fields, "plan_source_refs": {heading: []}}
    with pytest.raises(plans.PlanGroundingInvalid):
        prospective.resolve(json.dumps(plan_envelope), max_bytes=262144)
    g = history.ReportedHistoryGrounding.for_capture(
        tuple(fields), plan_fields, (source,), patient_only=True
    )
    raw = {**plan_envelope, history.KEY: {"Objective": {"source_refs": [1]}}}
    remaining, rendered = g.resolve(json.dumps(raw), max_bytes=262144)
    assert json.loads(remaining)["fields"][heading] == fields[heading]
    assert source.text in rendered["Objective"]
    with pytest.raises(plans.PlanGroundingInvalid):
        prospective.resolve(remaining, max_bytes=262144)
