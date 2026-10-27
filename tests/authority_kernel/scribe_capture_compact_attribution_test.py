"""Compact source framing cannot trim negation, qualifiers or speaker context."""

import json

import pytest

from app.services import scribe_capture_history_grounding as history
from app.services import scribe_capture_plan_grounding as plans


@pytest.mark.parametrize(
    "text",
    [
        "Do not take aspirin.",
        "Take aspirin. I am pregnant.",
        "Take ibuprofen. My kidney function is poor.",
        "Start this drug. I am breastfeeding.",
        "Proceed. My diagnosis is hemophilia.",
        "Take this only if cleared.\n\nDo not start it today.",
        "I use café text and ‘quoted’ words; the qualifier remains here.",
    ],
)
def test_compact_history_keeps_every_source_byte_and_qualifier(text):
    g = history.ReportedHistoryGrounding.for_capture(
        ("ROS",),
        (),
        (plans.PlanSource.speech("Unknown", text),),
        patient_only=True,
    )
    raw = json.dumps(
        {
            "fields": {"ROS": "Invented replacement."},
            history.KEY: {"ROS": {"source_refs": [1]}},
        }
    )
    _, fields = g.resolve(raw, max_bytes=10000)
    assert fields["ROS"] == history.CONTEXT_HEADING + "- Unidentified speaker: " + text
    assert fields["ROS"].count(text) == 1
    assert "Invented replacement" not in fields["ROS"]


def test_compact_history_keeps_separate_people_and_typed_scratch_in_source_order():
    texts = [
        "I am Morgan. I had a headache yesterday and none today.",
        "I am Taylor. My right shoulder pain is two out of ten.",
        "Separate clinician note: no examination was performed.",
    ]
    sources = (
        plans.PlanSource.speech("Unknown", texts[0]),
        plans.PlanSource.speech("Unknown", texts[1]),
        plans.PlanSource.scratch(texts[2]),
    )
    g = history.ReportedHistoryGrounding.for_capture(
        ("ROS",), (), sources, patient_only=True
    )
    raw = json.dumps(
        {
            "fields": {"ROS": ""},
            history.KEY: {"ROS": {"source_refs": [3, 2, 1]}},
        }
    )
    _, fields = g.resolve(raw, max_bytes=10000)
    value = fields["ROS"]
    assert [value.index(text) for text in texts] == sorted(
        value.index(text) for text in texts
    )
    assert value.count("- Unidentified speaker: ") == 2
    assert "\n- Clinician's saved scratch notes: " + texts[2] in value
    assert all(value.count(text) == 1 for text in texts)
    assert "- Patient:" not in value and "- Clinician:" not in value
