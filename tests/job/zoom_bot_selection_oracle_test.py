"""Known omission/pairing failures must survive source-word preservation."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "tools/zoom-note-fidelity"
SPEC = importlib.util.spec_from_file_location(
    "selection_oracle", ROOT / "selection_oracle.py"
)
ORACLE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ORACLE)
CASES = {
    c["id"]: c for c in json.loads((ROOT / "named-cases.json").read_text())["cases"]
}


def complete(case):
    return {
        "Subjective": [
            i for i, e in enumerate(case["entries"], 1) if e["speaker"] == "Patient"
        ],
        "Other source evidence": [
            i for i, e in enumerate(case["entries"], 1) if e["speaker"] != "Patient"
        ],
    }


@pytest.mark.parametrize("name", ["R2", "R3"])
def test_full_source_pass_is_not_a_semantic_certificate(name):
    result = ORACLE.evaluate_selection(CASES[name], complete(CASES[name]))
    assert result["passed"] and result["semanticReviewRequired"]


@pytest.mark.parametrize(
    "name,fact",
    [
        (name, fact)
        for name, rules in ORACLE.CHECKS["cases"].items()
        for fact in rules["requiredAny"]
    ],
)
def test_omitting_every_source_for_each_known_fact_is_detected(name, fact):
    refs = complete(CASES[name])
    omitted = ORACLE.CHECKS["cases"][name]["requiredAny"][fact]
    refs = {
        field: [i for i in indexes if i not in omitted]
        for field, indexes in refs.items()
    }
    assert (
        "omitted-source:" + fact
        in ORACLE.evaluate_selection(CASES[name], refs)["failures"]
    )


@pytest.mark.parametrize(
    "name,trigger,context",
    [
        (name, trigger, context)
        for name, rules in ORACLE.CHECKS["cases"].items()
        for trigger, context in rules["ifAnyThenAny"]
    ],
)
def test_selected_correction_or_reference_requires_its_known_context(
    name, trigger, context
):
    refs = {"Source evidence": list(trigger)}
    assert any(
        x.startswith("missing-context:")
        for x in ORACLE.evaluate_selection(CASES[name], refs)["failures"]
    )
    refs["Source evidence"] += context
    result = ORACLE.evaluate_selection(CASES[name], refs)
    assert "missing-context:" + ",".join(map(str, trigger)) not in result["failures"]


def test_source_change_refuses_old_index_expectations_and_nonpatient_subjective():
    case = copy.deepcopy(CASES["R3"])
    case["entries"][14]["content"] = "A changed answer."
    assert ORACLE.evaluate_selection(case, {"Subjective": [15]})["failures"] == [
        "unrecognized-source"
    ]
    assert (
        "patient-only-placement"
        in ORACLE.evaluate_selection(CASES["R3"], {"Subjective": [13]})["failures"]
    )


@pytest.mark.parametrize("bad", [[True], [0], [999], [1, 1], None])
def test_invalid_source_references_are_not_coverage(bad):
    assert ORACLE.evaluate_selection(CASES["R2"], {"Subjective": bad})["failures"] == [
        "invalid-selection"
    ]


def test_context_in_another_field_does_not_repair_an_isolated_old_claim():
    refs = {"Subjective": [16], "Assessment": [17]}
    failures = ORACLE.evaluate_selection(CASES["R2"], refs)["failures"]
    assert "missing-context:16" in failures and "missing-context:17" in failures
