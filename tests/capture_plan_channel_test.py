"""One substantive prospective channel; supplied selections are not model evidence."""

import json

import pytest

from app.services import scribe_capture_history_grounding as history
from app.services import scribe_capture_notes as notes
from app.services import scribe_capture_plan_grounding as plans


def resolve(value, *, fields=("Plan",), sources=None):
    g = plans.PlanGrounding(
        fields,
        sources or (plans.PlanSource.speech("Unknown", "Follow up in ten days."),),
    )
    return g.resolve(json.dumps(value), max_bytes=262144)


@pytest.mark.parametrize(
    "value",
    ["Follow up in ten days.", " ", "\n", "Not mentioned.", None, 0, False, [], {}],
)
def test_nonempty_or_nonstring_plan_cannot_compete_with_valid_refs(value):
    with pytest.raises(plans.PlanGroundingInvalid):
        resolve({"fields": {"Plan": value}, "plan_source_refs": {"Plan": [1]}})


def test_missing_plan_key_cannot_be_filled_silently_from_refs():
    with pytest.raises(plans.PlanGroundingInvalid):
        resolve({"fields": {}, "plan_source_refs": {"Plan": [1]}})


def test_empty_placeholder_and_refs_keep_complete_qualifier_and_editorial_clause():
    source = "Record when symptoms occur and how long they last; do not record names. Use the corrected count in the note."
    sources = (plans.PlanSource.speech("Unknown", source),)
    raw, rendered = resolve(
        {
            "fields": {"Assessment": "Review needed.", "Plan": ""},
            "plan_source_refs": {"Plan": [1]},
        },
        sources=sources,
    )
    assert json.loads(raw) == {"Assessment": "Review needed.", "Plan": ""}
    assert rendered == {"Plan": "- Unidentified speaker: " + source}
    # Full extraction deliberately retains mixed-entry chatter/qualifiers. This
    # does not claim that exact wording is a polished or complete clinical note.


def test_unselected_plan_stays_review_required_even_when_history_contains_it():
    sources = (
        plans.PlanSource.speech(
            "Unknown", "Record when symptoms occur and how long they last."
        ),
    )
    g = history.ReportedHistoryGrounding.for_capture(
        ("Objective", "Plan"), ("Plan",), sources, patient_only=True
    )
    value = {
        "fields": {"Objective": "", "Plan": ""},
        "plan_source_refs": {"Plan": []},
        history.KEY: {"Objective": {"source_refs": [1]}},
    }
    remaining, reported = g.resolve(json.dumps(value), max_bytes=262144)
    _, prospective = resolve(json.loads(remaining), sources=sources)
    assert sources[0].text in reported["Objective"]
    assert prospective == {"Plan": plans.REVIEW_REQUIRED}


def test_each_prospective_custom_heading_is_empty_only_but_other_fields_remain_strings():
    fields = {
        "Patient": "",
        "Objective": "",
        "P - PLAN": "",
        "Follow-ups": "",
        "Return precautions": "",
        "Firma": "",
    }
    prospective = plans.prospective_fields(fields)
    schema = notes.capture_response_schema(tuple(fields), prospective)
    actual = schema["properties"]["fields"]
    assert (
        actual["required"] == list(fields) and actual["additionalProperties"] is False
    )
    assert actual["properties"] == {
        "Patient": {"type": "string"},
        "Objective": {"type": "string"},
        "P - PLAN": {"type": "string", "enum": [""]},
        "Follow-ups": {"type": "string", "enum": [""]},
        "Return precautions": {"type": "string", "enum": [""]},
        "Firma": {"type": "string"},
    }
    value = {
        "fields": dict.fromkeys(fields, ""),
        "plan_source_refs": dict.fromkeys(prospective, []),
    }
    value["fields"]["Follow-ups"] = "Come back soon."
    with pytest.raises(plans.PlanGroundingInvalid):
        resolve(value, fields=prospective)


def test_template_without_prospective_fields_keeps_flat_contract_and_raw_value():
    schema = notes.capture_response_schema(("History", "Signature"), ())
    assert schema == {
        "type": "object",
        "properties": {"History": {"type": "string"}, "Signature": {"type": "string"}},
        "required": ["History", "Signature"],
        "additionalProperties": False,
    }
    raw = '{"History":"Saved narrative","Signature":"Provider"}'
    assert plans.PlanGrounding((), ()).resolve(raw, max_bytes=262144) == (raw, {})


def test_history_envelope_does_not_remove_prospective_empty_only_schema():
    sources = (plans.PlanSource.speech("Unknown", "No medicine changes."),)
    h = history.ReportedHistoryGrounding.for_capture(
        ("Objective", "Plan"), ("Plan",), sources, patient_only=True
    )
    schema = notes.capture_response_schema(("Objective", "Plan"), ("Plan",), h)
    assert set(schema["required"]) == {"fields", "plan_source_refs", history.KEY}
    assert schema["properties"]["fields"]["properties"]["Plan"] == {
        "type": "string",
        "enum": [""],
    }
    assert schema["properties"]["fields"]["properties"]["Objective"] == {
        "type": "string"
    }


def test_prompt_explains_single_authority_without_claiming_selection_completeness():
    prompt = plans.PlanGrounding(("Plan",), ()).prompt()
    assert 'exactly empty ("")' in prompt
    assert "do not draft a second prose plan" in prompt
    assert "history field does not populate a prospective field" in prompt
    assert "complete negation, condition, refusal, cancellation" in prompt
    assert "ignore generated prose" not in prompt
