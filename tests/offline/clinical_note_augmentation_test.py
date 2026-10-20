"""Exercise note augmentation without DB, identity, or model-service bootstrap."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


def load_augmentation():
    source = Path(__file__).resolve().parents[2] / "app/jobs/summarize.py"
    tree = ast.parse(source.read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "augment_clinical_note"
    )
    namespace = {"AIVisitTranscript": SimpleNamespace}
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"),
        namespace,
    )
    return namespace[function.name]


@pytest.mark.parametrize("duration", [None, 0, 30, 60, 150, 3600])
def test_recording_duration_never_becomes_a_care_time_claim(duration):
    augment = load_augmentation()
    note = {"Assessment": "Stable sleep.", "Treatment Plan": "Maintain sleep diary."}

    result = augment(note, SimpleNamespace(duration=duration))

    assert result == {
        "Assessment": "Stable sleep.",
        "Treatment Plan": "Maintain sleep diary.\n\nThis note was generated with AI assistance.",
    }


def test_note_without_plan_is_preserved():
    note = {"Subjective": "Synthetic source text."}
    assert load_augmentation()(note, SimpleNamespace(duration=120)) == note
