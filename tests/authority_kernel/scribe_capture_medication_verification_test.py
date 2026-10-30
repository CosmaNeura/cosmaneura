"""Verification metadata is retained without rewriting clinical source facts."""

import json

import pytest

from app.services import scribe_capture_history_grounding as history
from app.services import scribe_capture_plan_grounding as plans


def apply(fields, plan_fields=("Plan",)):
    return history.with_medication_verification_limit(fields, plan_fields=plan_fields)


def test_known_role_soap_cannot_drop_verification_limit_with_model_prose():
    fields = {
        "Subjective": "The patient has not started, stopped, or changed any medicine.",
        "Objective": "Home blood pressure reading: 118/72.",
        "Plan": "- Clinician: No medication changes planned.",
    }
    result = apply(fields)
    assert result["Objective"] == (
        history.MEDICATION_LIST_UNVERIFIED + "\n\n" + fields["Objective"]
    )
    assert result["Subjective"] == fields["Subjective"]
    assert result["Plan"] == fields["Plan"]
    assert history.MEDICATION_LIST_UNVERIFIED not in fields["Objective"]


def test_concise_history_assessment_plan_retains_source_and_verification_limit():
    """The normal concise template must not hide the current-list limitation."""
    fields = {
        "History": "",
        "Assessment": "No assessment was explicitly stated.",
        "Plan": "- Unidentified speaker: No medication changes planned.",
    }
    source = plans.PlanSource.speech(
        "Unknown", "I have not started, stopped, or changed any medicine."
    )
    grounding = history.ReportedHistoryGrounding.for_capture(
        tuple(fields), ("Plan",), (source,), patient_only=True
    )
    assert grounding.fields == ("History",)
    raw = json.dumps(
        {
            "fields": fields,
            history.KEY: {"History": {"source_refs": [1]}},
        }
    )
    _, rendered = grounding.resolve(raw, max_bytes=262144)
    generated = {**fields, **rendered}
    result = apply(generated)

    assert tuple(result) == tuple(fields)
    assert result["History"] == (
        history.MEDICATION_LIST_UNVERIFIED + "\n\n" + generated["History"]
    )
    assert source.text in result["History"]
    assert "Unidentified speaker" in result["History"]
    assert result["Assessment"] == fields["Assessment"]
    assert result["Plan"] == fields["Plan"]
    assert apply(result) == result
    assert history.MEDICATION_LIST_UNVERIFIED not in generated["History"]


def test_reported_current_medicines_remain_complete_without_absence_assertion():
    fields = {
        "Medications": "The patient reports taking metformin 500 mg twice daily.",
        "Objective": "Not mentioned in transcript.",
    }
    result = apply(fields)
    assert result["Medications"].endswith(fields["Medications"])
    assert "unverified" in result["Medications"]
    assert "not specified" not in result["Medications"]
    assert "no medications" not in result["Medications"]
    assert result["Objective"] == fields["Objective"]


def test_existing_source_rendered_limit_is_not_duplicated():
    fields = {
        "Objective": history.MEDICATION_LIST_UNVERIFIED + "\n\nSelected source context."
    }
    assert apply(fields) == fields
    assert apply(apply(fields)) == fields


def test_medication_heading_is_preferred_over_objective_regardless_of_field_order():
    fields = {
        "History": "A source-bound history.",
        "Objective": "A reported home reading.",
        "Medications": "A reported list.",
    }
    result = apply(fields)
    assert result["History"] == fields["History"]
    assert result["Objective"] == fields["Objective"]
    assert result["Medications"].startswith(history.MEDICATION_LIST_UNVERIFIED)


def test_empty_existing_field_gets_only_the_verification_statement():
    assert apply({"Objective": ""}) == {"Objective": history.MEDICATION_LIST_UNVERIFIED}


def test_prospective_medication_heading_is_never_changed_or_used_as_history():
    fields = {"Medication plan": "No medication changes planned.", "Objective": ""}
    result = apply(fields, plan_fields=("Medication plan",))
    assert result["Medication plan"] == fields["Medication plan"]
    assert result["Objective"] == history.MEDICATION_LIST_UNVERIFIED


@pytest.mark.parametrize("fields", [{"Summary": "A summary."}, {"Plan": "A plan."}])
def test_no_recognized_destination_does_not_create_or_repurpose_a_field(fields):
    assert apply(fields) == fields


@pytest.mark.parametrize("heading", ["Objective / Assessment", "Objective signature"])
def test_objective_word_does_not_repurpose_excluded_fields(heading):
    fields = {heading: "Original field content."}
    assert apply(fields) == fields
