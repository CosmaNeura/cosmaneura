"""Adversarial source-reference and saved-shape contracts; no model substitutes."""

import json

import pytest

from app.services import scribe_capture_history_grounding as history
from app.services import scribe_capture_notes as notes
from app.services import scribe_capture_plan_grounding as plans

FIELDS = (
    "Patient",
    "S - SUBJECTIVE",
    "O - OBJECTIVE",
    "Medications",
    "ROS",
    "A - ASSESSMENT",
    "P - PLAN",
)


def contract(texts, *, plan_fields=("P - PLAN",)):
    sources = tuple(plans.PlanSource.speech("Unknown", text) for text in texts)
    return history.ReportedHistoryGrounding.for_capture(
        FIELDS, plan_fields, sources, patient_only=True
    )


def response(
    grounding, *, objective=(), current=(), changes=(), extra=None, has_plans=True
):
    refs: dict[str, object] = {field: [] for field in grounding.fields}
    refs["O - OBJECTIVE"] = list(objective)
    refs["Medications"] = {
        "current_list": list(current),
        "change_history": list(changes),
    }
    result = {
        "fields": {
            field: 'The patient reports pain 9/10 and denies fever. "Invented quote."'
            for field in FIELDS
        },
        history.KEY: refs,
    }
    if has_plans:
        result["plan_source_refs"] = {"P - PLAN": []}
    if extra:
        result.update(extra)
    return json.dumps(result)


def test_fabricated_model_words_roles_numbers_and_split_quotes_are_not_published():
    source = "I have no chest pain, no shortness of breath, and no fever."
    g = contract([source])
    _, rendered = g.resolve(response(g, objective=[1]), max_bytes=10000)
    assert (
        rendered["O - OBJECTIVE"]
        == "Reported transcript history:\nTranscript — Unidentified speaker:\n" + source
    )
    assert "9/10" not in rendered["O - OBJECTIVE"]
    assert "the patient" not in rendered["O - OBJECTIVE"].lower()
    assert "Invented quote" not in rendered["O - OBJECTIVE"]
    assert "I have no fever." not in rendered["O - OBJECTIVE"]


def test_source_order_preserves_corrections_prior_current_time_and_uncertainty():
    texts = [
        "My current pain is two out of ten.",
        "I said four earlier, but two is correct.",
        "I had a mild headache yesterday, but none today.",
        "The duration might be two or four minutes; I am not sure.",
    ]
    g = contract(texts)
    _, rendered = g.resolve(response(g, objective=[4, 2, 1, 3]), max_bytes=10000)
    value = rendered["O - OBJECTIVE"]
    assert all(text in value for text in texts)
    assert [value.index(text) for text in texts] == sorted(
        value.index(text) for text in texts
    )


def test_unanswered_question_is_never_turned_into_a_denial():
    question = "Have you had a fall or pain traveling down your arm?"
    g = contract([question])
    _, rendered = g.resolve(response(g, objective=[1]), max_bytes=10000)
    assert question in rendered["O - OBJECTIVE"]
    assert "no fall" not in rendered["O - OBJECTIVE"].lower()
    # Clinical selection remains an actual-model/content-review concern.
    assert "unanswered questions" in g.prompt(has_plans=True)


def test_empty_selection_is_review_limitation_not_normal_or_absent_history():
    g = contract(["A source requiring review."])
    _, rendered = g.resolve(response(g), max_bytes=10000)
    assert rendered["O - OBJECTIVE"] == history.REVIEW_REQUIRED
    assert (
        "Current medication list not specified in selected source entries"
        in rendered["Medications"]
    )
    assert "no medications" not in rendered["Medications"].lower()


def test_current_list_and_retrospective_change_history_stay_distinct():
    texts = [
        "I take synthetic TestMed daily.",
        "I have not started, stopped, or changed any medicine.",
        "No medication change is planned.",
    ]
    g = contract(texts)
    _, rendered = g.resolve(response(g, changes=[2]), max_bytes=10000)
    assert (
        texts[1] in rendered["Medications"] and texts[2] not in rendered["Medications"]
    )
    assert "Current medication list not specified" in rendered["Medications"]
    _, rendered = g.resolve(response(g, current=[1], changes=[2]), max_bytes=10000)
    assert texts[0] in rendered["Medications"] and texts[1] in rendered["Medications"]
    assert "not specified" not in rendered["Medications"]


def test_medication_group_heading_never_recasts_a_selected_future_decision_as_past():
    texts = [
        "I take synthetic TestMed daily.",
        "I have not taken any new medicine.",
        "We are not prescribing a new medicine.",
    ]
    g = contract(texts)
    _, rendered = g.resolve(response(g, current=[1], changes=[2, 3]), max_bytes=10000)
    medication = rendered["Medications"]
    assert all(text in medication for text in texts)
    assert "Selected medication source statements:" in medication
    assert "Reported medication change history:" not in medication
    assert "was prescribed" not in medication
    _, rendered = g.resolve(response(g, changes=[2, 3]), max_bytes=10000)
    assert "Current medication list not specified" in rendered["Medications"]
    assert all(text in rendered["Medications"] for text in texts[1:])


@pytest.mark.parametrize("indexes", [[True], [1.0], [0], [-1], [2], [1, 1], "1", None])
def test_invalid_source_index_contract_fails_closed(indexes):
    g = contract(["Source."])
    value = json.loads(response(g))
    value[history.KEY]["O - OBJECTIVE"] = indexes
    with pytest.raises(history.HistoryGroundingInvalid):
        g.resolve(json.dumps(value), max_bytes=10000)


@pytest.mark.parametrize(
    "mutation",
    [
        "foreign_field",
        "subjective",
        "plan",
        "extra_envelope",
        "duplicate_medication",
        "extra_medication_key",
        "missing_field",
    ],
)
def test_foreign_or_ambiguous_reference_shapes_fail_closed(mutation):
    g = contract(["Source."])
    value = json.loads(response(g, has_plans=False))
    refs = value[history.KEY]
    if mutation in {"foreign_field", "subjective", "plan"}:
        refs[
            {
                "foreign_field": "Foreign",
                "subjective": "S - SUBJECTIVE",
                "plan": "P - PLAN",
            }[mutation]
        ] = [1]
    elif mutation == "extra_envelope":
        value["unexpected"] = True
    elif mutation == "duplicate_medication":
        refs["Medications"] = {"current_list": [1], "change_history": [1]}
    elif mutation == "extra_medication_key":
        refs["Medications"]["new_plan"] = [1]
    else:
        refs.pop("ROS")
    with pytest.raises(history.HistoryGroundingInvalid):
        g.resolve(json.dumps(value), max_bytes=10000)


def test_duplicate_json_properties_are_rejected():
    g = contract(["Source."])
    raw = response(g).replace(
        '"O - OBJECTIVE": []', '"O - OBJECTIVE": [], "O - OBJECTIVE": [1]'
    )
    with pytest.raises(history.HistoryGroundingInvalid):
        g.resolve(raw, max_bytes=10000)


@pytest.mark.parametrize("speaker", ["Patient", "Doctor", "Other"])
def test_known_roles_and_non_patient_only_preferences_keep_existing_path(speaker):
    known = (plans.PlanSource.speech(speaker, "My own report."),)
    assert not history.ReportedHistoryGrounding.for_capture(
        FIELDS, (), known, patient_only=True
    ).fields
    g = contract(["Unidentified report."])
    assert not history.ReportedHistoryGrounding.for_capture(
        FIELDS, (), g.sources, patient_only=False
    ).fields
    forged = history.ReportedHistoryGrounding(g.fields, g.medication_fields, known)
    with pytest.raises(history.HistoryGroundingInvalid):
        forged.resolve(response(forged, objective=[1]), max_bytes=10000)


def test_typed_scratch_source_retains_its_distinct_attribution():
    g = contract(["Unidentified report."])
    sources = g.sources + (
        plans.PlanSource.scratch("Clinician's separately saved observation."),
    )
    g = history.ReportedHistoryGrounding.for_capture(
        FIELDS, ("P - PLAN",), sources, patient_only=True
    )
    _, rendered = g.resolve(response(g, objective=[1, 2]), max_bytes=10000)
    assert (
        "Clinician's saved scratch notes:\nClinician's separately saved observation."
        in rendered["O - OBJECTIVE"]
    )


@pytest.mark.parametrize("has_plans", [True, False])
def test_strict_saved_shape_and_existing_plan_resolver_are_preserved(has_plans):
    g = contract(
        ["Reported history.", "We will follow up in seven days."],
        plan_fields=("P - PLAN",) if has_plans else (),
    )
    schema = notes.capture_response_schema(
        FIELDS, ("P - PLAN",) if has_plans else (), g
    )
    assert set(schema["properties"]["fields"]["properties"]) == set(FIELDS)
    assert history.KEY in schema["required"]
    assert not {"S - SUBJECTIVE", "P - PLAN", "Patient", "A - ASSESSMENT"} & set(
        g.fields
    )
    value = json.loads(response(g, objective=[1], has_plans=has_plans))
    if has_plans:
        value["plan_source_refs"]["P - PLAN"] = [2]
    remaining, history_fields = g.resolve(json.dumps(value), max_bytes=10000)
    flat, plan_fields = plans.PlanGrounding(
        ("P - PLAN",) if has_plans else (), g.sources
    ).resolve(remaining, max_bytes=10000)
    fields = json.loads(flat)
    fields.update(plan_fields)
    fields.update(history_fields)
    assert set(fields) == set(FIELDS)
    if has_plans:
        assert (
            fields["P - PLAN"]
            == "Transcript — Unidentified speaker:\nWe will follow up in seven days."
        )
