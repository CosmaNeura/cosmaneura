"""Finite, pinned synthetic omission/context checks; never runtime note repair."""

import hashlib
import json
from pathlib import Path

CHECKS = json.loads(Path(__file__).with_name("selection-checks.json").read_text())


def evaluate_selection(case, selections, *, patient_only=True):
    failures = []
    rules = CHECKS["cases"].get(case["id"])
    fingerprint = hashlib.sha256(
        json.dumps(case["entries"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if rules is None or rules["entriesSHA256"] != fingerprint:
        return {
            "passed": False,
            "failures": ["unrecognized-source"],
            "semanticReviewRequired": True,
        }
    valid = isinstance(selections, dict) and all(
        isinstance(field, str)
        and isinstance(refs, list)
        and all(type(i) is int and 1 <= i <= len(case["entries"]) for i in refs)
        and len(set(refs)) == len(refs)
        for field, refs in selections.items()
    )
    if not valid:
        return {
            "passed": False,
            "failures": ["invalid-selection"],
            "semanticReviewRequired": True,
        }
    selected = set().union(*map(set, selections.values()))
    for fact, alternatives in rules["requiredAny"].items():
        if not selected.intersection(alternatives):
            failures.append("omitted-source:" + fact)
    for refs in selections.values():
        local = set(refs)
        for trigger, context in rules["ifAnyThenAny"]:
            if local.intersection(trigger) and not local.intersection(context):
                failures.append("missing-context:" + ",".join(map(str, trigger)))
    if patient_only:
        for field, refs in selections.items():
            if field.casefold() == "subjective" and any(
                case["entries"][i - 1]["speaker"] != "Patient" for i in refs
            ):
                failures.append("patient-only-placement")
    return {
        "passed": not failures,
        "failures": sorted(set(failures)),
        "semanticReviewRequired": True,
        "claim": "Only listed exact-fixture omissions/context and Patient-only placement; section usefulness, all other dependencies, attribution and clinical interpretation remain unproved.",
    }
