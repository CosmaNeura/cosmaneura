"""Coverage/placement checks cannot validate the model's clinical classifications."""

from copy import deepcopy

import pytest

from app.services import zoom_bot_source_dispositions as p

from tests.job.zoom_bot_grounding_test import row

FIELDS = {
    "Subjective": "History",
    "Objective": "Examination",
    "Assessment": "Impression",
    "Plan": "Care",
}


@pytest.fixture
def selected():
    return [
        row(1, "clinical_question", "Objective", [2], "partial"),
        row(2, "reported_history", "Subjective"),
        row(3, "nonclinical", None),
    ]


def resolve(value, *, speakers=("Doctor", "Patient", "Doctor"), patient_only=True):
    return p.resolve(value, FIELDS, list(speakers), patient_only=patient_only)


def test_partial_question_and_reply_stay_together_without_changing_patient_only(
    selected,
):
    refs, context = resolve(selected)
    assert refs == {
        "Subjective": [2],
        "Objective": [1, 2],
        "Assessment": [],
        "Plan": [],
    }
    assert context["Objective"] == {1, 2}
    assert not context["Subjective"]


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "duplicate_index",
        "boolean_index",
        "foreign_index",
        "extra_key",
        "missing_key",
        "duplicate_purpose",
        "bad_field",
        "bad_scope",
        "wrong_type",
    ],
)
def test_exact_one_disposition_per_source_without_duplicate_exclusion(selected, change):
    if change == "missing":
        selected.pop()
    elif change == "duplicate_index":
        selected[2]["source_index"] = 2
    elif change == "boolean_index":
        selected[0]["source_index"] = True
    elif change == "foreign_index":
        selected[0]["source_index"] = 9
    elif change == "extra_key":
        selected[0]["text"] = "No other injury."
    elif change == "missing_key":
        selected[0].pop("purpose")
    elif change == "duplicate_purpose":
        selected[2]["purpose"] = "duplicate"
    elif change == "bad_field":
        selected[1]["field"] = "Invented"
    elif change == "bad_scope":
        selected[0]["answer_scope"] = "probably answered"
    else:
        selected = {"source_dispositions": selected}
    with pytest.raises(p.DispositionInvalid):
        resolve(selected)


@pytest.mark.parametrize(
    "refs", [[0], [4], [True], [2, 2], ["2"], None, [2] * 101, [1], [{}]]
)
def test_context_refs_are_bounded_exact_nonself_sources(selected, refs):
    selected[0]["context_refs"] = refs
    with pytest.raises(p.DispositionInvalid):
        resolve(selected)


@pytest.mark.parametrize(
    "change",
    [
        "partial_omitted",
        "unanswered_omitted",
        "partial_no_reply",
        "complete_no_reply",
        "complete_included",
        "reply_excluded",
        "reply_question",
    ],
)
def test_question_scope_cannot_discard_partial_context_or_claim_hidden_reply(
    selected, change
):
    q = selected[0]
    if change == "partial_omitted":
        q["field"] = None
    elif change == "unanswered_omitted":
        q.update(field=None, answer_scope="unanswered", context_refs=[])
    elif change == "partial_no_reply":
        q["context_refs"] = []
    elif change == "complete_no_reply":
        q.update(field=None, answer_scope="fully_answered", context_refs=[])
    elif change == "complete_included":
        q["answer_scope"] = "fully_answered"
    elif change == "reply_excluded":
        selected[1] = row(2, "nonclinical", None)
    else:
        selected[1] = row(2, "clinical_question", "Objective", scope="unanswered")
    with pytest.raises(p.DispositionInvalid):
        resolve(selected)


def test_fully_answered_question_requires_a_retained_reply_and_unanswered_stays_visible(
    selected,
):
    selected[0].update(field=None, answer_scope="fully_answered")
    refs, _ = resolve(selected)
    assert refs["Subjective"] == [2] and not refs["Objective"]
    selected[0].update(field="Objective", answer_scope="unanswered", context_refs=[])
    assert resolve(selected)[0]["Objective"] == [1]


@pytest.mark.parametrize(
    "kind",
    [
        "reported_history",
        "observed_data",
        "care_plan",
        "nonclinical",
        "clinical_question",
        "contextual_correction",
    ],
)
def test_assessment_cannot_be_filled_by_recap_question_or_note_instruction(
    selected, kind
):
    selected[1] = row(
        2,
        kind,
        "Assessment",
        [3] if kind == "contextual_correction" else [],
        "unanswered" if kind == "clinical_question" else "not_question",
    )
    with pytest.raises(p.DispositionInvalid):
        resolve(selected)


def test_assessment_accepts_only_explicitly_classified_impression(selected):
    selected[2] = row(3, "clinical_impression", "Assessment")
    assert resolve(selected)[0]["Assessment"] == [3]


@pytest.mark.parametrize("speaker", ["Doctor", "Unknown", "Other", "Morgan"])
def test_patient_only_is_enforced_for_primary_and_context_sources(selected, speaker):
    with pytest.raises(p.DispositionInvalid):
        resolve(selected, speakers=("Doctor", speaker, "Doctor"))
    selected[2] = row(3, "reported_history", "Objective")
    selected[1]["context_refs"] = [3]
    with pytest.raises(p.DispositionInvalid):
        resolve(selected, speakers=("Doctor", "Patient", speaker))


def test_patient_and_doctor_setting_does_not_upgrade_unknown_attribution(selected):
    assert resolve(
        selected, speakers=("Doctor", "Doctor", "Doctor"), patient_only=False
    )[0]["Subjective"] == [2]


@pytest.mark.parametrize(
    "kind",
    ["reported_history", "observed_data", "clinical_impression", "clinical_question"],
)
def test_history_or_question_cannot_be_promoted_to_prospective_plan(selected, kind):
    selected[1] = row(
        2,
        kind,
        "Plan",
        scope="unanswered" if kind == "clinical_question" else "not_question",
    )
    with pytest.raises(p.DispositionInvalid):
        resolve(selected)


def test_plan_correction_requires_care_anchor_and_retains_history_as_context(
    selected,
):
    selected = [
        row(1, "care_plan", "Plan"),
        row(2, "contextual_correction", "Plan", [1]),
        row(3, "nonclinical", None),
    ]
    assert resolve(selected)[0]["Plan"] == [1, 2]
    selected[0] = row(1, "reported_history", "Objective")
    with pytest.raises(p.DispositionInvalid):
        resolve(selected)
    selected[2] = row(3, "care_plan", "Plan")
    selected[1]["context_refs"] = [1, 3]
    refs, context = resolve(selected)
    assert refs["Plan"] == [1, 2, 3] and context["Plan"] == {1, 3}
    assert "no finding, diagnosis or care decision is inferred" in p.CONTEXT_REVIEW
    selected[1]["context_refs"] = []
    with pytest.raises(p.DispositionInvalid):
        resolve(selected)


def test_plan_correction_cannot_borrow_care_anchor_from_another_field():
    fields = {**FIELDS, "Follow-up": "Next review"}
    selected = [
        row(1, "care_plan", "Follow-up"),
        row(2, "contextual_correction", "Plan", [1]),
        row(3, "nonclinical", None),
    ]
    with pytest.raises(p.DispositionInvalid):
        p.resolve(selected, fields, ["Doctor", "Patient", "Doctor"], patient_only=True)


def test_shape_does_not_claim_semantic_qualification_or_rewrite_input(selected):
    original = deepcopy(selected)
    resolve(selected)
    assert selected == original
    # A wrong nonclinical classification remains possible; exact-fixture
    # clinical/omission checks and independent reading must reject it.
    all_excluded = [row(i, "nonclinical", None) for i in range(1, 4)]
    assert all(not refs for refs in resolve(all_excluded)[0].values())


@pytest.mark.parametrize("name", ["R2", "R3"])
def test_complete_realistic_source_dispositions_keep_finite_fact_and_question_context(
    name,
):
    from tests.job.zoom_bot_selection_oracle_test import CASES, ORACLE

    case = CASES[name]
    rows = {i: row(i, "nonclinical", None) for i in range(1, len(case["entries"]) + 1)}

    def place(indexes, kind, field):
        for index in indexes:
            rows[index] = row(index, kind, field)

    if name == "R2":
        place([2, 11, 27, 36], "reported_history", "Objective")
        place(
            [5, 7, 9, 14, 15, 16, 18, 22, 23, 24, 26], "reported_history", "Subjective"
        )
        place([28, 29, 30, 32, 33, 37, 38], "care_plan", "Plan")
        for index, anchors in {6: [5], 8: [7], 17: [16]}.items():
            rows[index] = row(index, "contextual_correction", "Subjective", anchors)
        rows[33]["context_refs"] = [7, 22, 32]
        questions = {4: [5, 7], 12: [14, 15], 19: [22], 20: [23, 24]}
    else:
        place([2, 11, 41], "reported_history", "Objective")
        place(
            [5, 6, 8, 10, 15, 16, 17, 18, 25, 27, 28, 30, 39],
            "reported_history",
            "Subjective",
        )
        place([31, 32, 33, 34, 36], "care_plan", "Plan")
        for index, anchors in {7: [6], 26: [25], 37: [6]}.items():
            rows[index] = row(index, "contextual_correction", "Subjective", anchors)
        rows[30]["context_refs"] = [6]
        rows[35] = row(35, "contextual_correction", "Plan", [34, 36])
        rows[13] = row(13, "clinical_question", "Objective", [15], "partial")
        questions = {4: [6, 7], 12: [16, 17], 24: [25, 27]}
    for index, anchors in questions.items():
        rows[index] = row(index, "clinical_question", None, anchors, "fully_answered")
    sources = list(rows.values())
    speakers = [e["speaker"] for e in case["entries"]]
    refs, context = p.resolve(sources, FIELDS, speakers, patient_only=True)
    checked = ORACLE.evaluate_selection(case, refs)
    assert checked["passed"] and checked["semanticReviewRequired"]
    assert not refs["Assessment"]
    assert 40 not in set().union(*map(set, refs.values()))
    assert len(set().union(*map(set, refs.values()))) < len(sources)
    if name == "R3":
        assert {13, 15} <= context["Objective"]
        # Mechanically valid but clinically false answer-scope classification
        # still fails the unchanged oracle; no weaker "shape PASS" acceptance.
        rows[13].update(field=None, answer_scope="fully_answered")
        bad_refs, _ = p.resolve(
            list(rows.values()), FIELDS, speakers, patient_only=True
        )
        assert (
            "omitted-source:unanswered-injury-question"
            in ORACLE.evaluate_selection(case, bad_refs)["failures"]
        )
        rows[40] = row(40, "nonclinical", "Assessment")
        with pytest.raises(p.DispositionInvalid):
            p.resolve(list(rows.values()), FIELDS, speakers, patient_only=True)
