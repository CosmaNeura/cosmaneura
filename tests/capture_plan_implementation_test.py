"""Supplied selections protect extraction boundaries, not model completeness."""

import json

import pytest

from app.services import scribe_capture_history_grounding as history
from app.services import scribe_capture_plan_grounding as plans


@pytest.mark.parametrize(
    ("texts", "refs", "expected"),
    [
        (
            [
                "Keep a record of the symptom.",
                "I will write down its start and duration. Use the corrected spelling in the note.",
                "Only do that on days when it occurs; do not include names.",
            ],
            [3, 1, 2],
            [0, 1, 2],
        ),
        (
            [
                "Try the exercise each morning if comfortable.",
                "I will stop if it causes pain; the note should use my corrected name.",
                "Actually, defer that exercise until we have reviewed it together.",
            ],
            [3, 2, 1],
            [0, 1, 2],
        ),
        (
            [
                "I already record symptoms each evening.",
                "Please change the spelling in the summary.",
                "I understand.",
                "We will stop the recording now.",
            ],
            [],
            [],
        ),
    ],
)
def test_supplied_plan_keeps_split_conditions_and_cancellation_without_auto_promotion(
    texts, refs, expected
):
    sources = tuple(plans.PlanSource.speech("Unknown", text) for text in texts)
    grounding = plans.PlanGrounding(("Plan",), sources)
    raw, rendered = grounding.resolve(
        json.dumps({"fields": {"Plan": ""}, "plan_source_refs": {"Plan": refs}}),
        max_bytes=262144,
    )
    assert json.loads(raw) == {"Plan": ""}
    assert rendered == {
        "Plan": "\n\n".join(
            "Transcript — Unidentified speaker:\n" + texts[i] for i in expected
        )
        or plans.REVIEW_REQUIRED
    }


def test_one_mixed_entry_can_be_explicitly_selected_in_history_and_plan():
    text = "I rested yesterday, and I will avoid heavy lifting until our next review. Correct the name in the note."
    sources = (plans.PlanSource.speech("Unknown", text),)
    h = history.ReportedHistoryGrounding.for_capture(
        ("Objective", "Plan"), ("Plan",), sources, patient_only=True
    )
    value = {
        "fields": {"Objective": "", "Plan": ""},
        "plan_source_refs": {"Plan": [1]},
        history.KEY: {"Objective": {"source_refs": [1]}},
    }
    remaining, reported = h.resolve(json.dumps(value), max_bytes=262144)
    _, prospective = plans.PlanGrounding(("Plan",), sources).resolve(
        remaining, max_bytes=262144
    )
    assert text in reported["Objective"]
    assert prospective == {"Plan": "Transcript — Unidentified speaker:\n" + text}
    # This is allowed overlap chosen explicitly by the model. It is not a rule
    # that promotes history refs into Plan or trims away later qualifications.
