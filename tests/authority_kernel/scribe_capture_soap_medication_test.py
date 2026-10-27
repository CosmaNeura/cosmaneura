"""SOAP source-selection limitations; no model or live-record substitutions."""

import json

import pytest

from app.services import scribe_capture_history_grounding as history
from app.services import scribe_capture_plan_grounding as plans

SOAP = ("Subjective", "Objective", "Assessment", "Plan")
LIMITATION = "Current medication list is unverified; review the source statements."


def response(fields, sources, *, selected=(), speaker="Unknown", patient_only=True):
    grounding = history.ReportedHistoryGrounding.for_capture(
        fields,
        ("Plan",),
        tuple(plans.PlanSource.speech(speaker, text) for text in sources),
        patient_only=patient_only,
    )
    refs = {
        field: (
            {
                "current_list": [],
                "change_history": list(selected),
                "source_context": [],
            }
            if field in grounding.medication_fields
            else {"source_refs": list(selected)}
        )
        for field in grounding.fields
    }
    raw = json.dumps(
        {
            "fields": {
                field: "The current medication list is unknown. Takes no medications."
                for field in fields
            },
            "plan_source_refs": {"Plan": []},
            history.KEY: refs,
        }
    )
    return grounding, raw


def test_soap_keeps_medication_uncertainty_when_generated_objective_is_replaced():
    text = "I have not started, stopped, or changed any medicine."
    grounding, raw = response(SOAP, [text], selected=[1])
    remaining, rendered = grounding.resolve(raw, max_bytes=10000)
    assert rendered == {
        "Objective": LIMITATION
        + "\n\nSelected transcript source context (questions are not findings):\nTranscript — Unidentified speaker:\n"
        + text
    }
    assert "Takes no medications" not in rendered["Objective"]
    # The caller overlays only the grounded field; no envelope/other-field edit.
    expected = json.loads(raw)
    expected.pop(history.KEY)
    assert json.loads(remaining) == expected
    fields = {**expected["fields"], **rendered}
    assert tuple(fields) == SOAP
    assert all(
        fields[key] == expected["fields"][key] for key in SOAP if key != "Objective"
    )


def test_empty_soap_selection_remains_review_required_not_an_empty_medication_list():
    grounding, raw = response(SOAP, ["Unidentified source requiring review."])
    _, rendered = grounding.resolve(raw, max_bytes=10000)
    assert rendered["Objective"] == LIMITATION + "\n\n" + history.REVIEW_REQUIRED
    assert "no medications" not in rendered["Objective"].lower()


def test_soap_named_past_and_future_source_words_keep_order_without_list_verification():
    texts = [
        "I take synthetic TestMed daily.",
        "I have not started, stopped, or changed any medicine.",
        "We are not prescribing a new medicine.",
    ]
    grounding, raw = response(SOAP, texts, selected=[3, 1, 2])
    _, rendered = grounding.resolve(raw, max_bytes=10000)
    assert (
        rendered["Objective"]
        == LIMITATION
        + "\n\nSelected transcript source context (questions are not findings):\n"
        + "\n\n".join("Transcript — Unidentified speaker:\n" + text for text in texts)
    )


def test_only_first_eligible_objective_receives_fallback_limitation():
    fields = (
        "Subjective",
        "ROS",
        "O - OBJECTIVE",
        "Objective findings",
        "Assessment",
        "Plan",
    )
    grounding, raw = response(fields, ["Unidentified source."], selected=[1])
    _, rendered = grounding.resolve(raw, max_bytes=10000)
    assert rendered["O - OBJECTIVE"].startswith(LIMITATION + "\n\n")
    assert LIMITATION not in rendered["ROS"]
    assert LIMITATION not in rendered["Objective findings"]
    assert sum(value.count(LIMITATION) for value in rendered.values()) == 1


def test_dedicated_medication_field_preserves_existing_bytes_without_objective_duplicate():
    fields = (*SOAP, "Medications")
    text = "I have not taken any new medicine."
    grounding, raw = response(fields, [text], selected=[1])
    _, rendered = grounding.resolve(raw, max_bytes=10000)
    source = "Transcript — Unidentified speaker:\n" + text
    assert (
        rendered["Objective"]
        == "Selected transcript source context (questions are not findings):\n" + source
    )
    assert (
        rendered["Medications"]
        == LIMITATION + "\n\nSelected medication source context:\n" + source
    )


def test_no_objective_does_not_place_medication_limitation_in_other_history_fields():
    fields = ("Subjective", "ROS", "Allergies", "Assessment", "Plan")
    grounding, raw = response(fields, ["Unidentified source."], selected=[1])
    _, rendered = grounding.resolve(raw, max_bytes=10000)
    assert set(rendered) == {"ROS", "Allergies"}
    assert all(
        value
        == "Selected transcript source context (questions are not findings):\nTranscript — Unidentified speaker:\nUnidentified source."
        for value in rendered.values()
    )


@pytest.mark.parametrize(
    "speaker,patient_only",
    [("Doctor", True), ("Patient", True), ("Other", True), ("Unknown", False)],
)
def test_non_enabled_paths_return_identical_input(speaker, patient_only):
    grounding, raw = response(
        SOAP, ["A report."], speaker=speaker, patient_only=patient_only
    )
    assert not grounding.fields
    assert grounding.resolve(raw, max_bytes=10000) == (raw, {})
