"""Verification metadata is retained without rewriting clinical source facts."""

import pytest

from app.services import scribe_capture_history_grounding as history


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
        "Objective": "A reported home reading.",
        "Medications": "A reported list.",
    }
    result = apply(fields)
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
