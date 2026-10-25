"""Finite synthetic bot checks; neither a note repair nor a safety certificate.

Use the actual classified entries passed to the model. The capture oracle remains
unchanged. SOAP has no medication-list field, so only that field-specific absence
requirement is omitted; medication history and false-empty-list checks still run.
All unfamiliar paraphrases and clinical reasoning require independent review.
"""

import importlib.util
import re
from pathlib import Path

_SOURCE = Path(__file__).resolve().parents[1] / "capture-retention/oracle.py"
_SPEC = importlib.util.spec_from_file_location("retention_oracle_for_bot", _SOURCE)
_CAPTURE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_CAPTURE)


def normalize(value):
    value = re.sub(r"\bthirty\b", "30", value, flags=re.I)
    return re.sub(r"\bdeny\b", "denies", value, flags=re.I)


def evaluate(
    case,
    fields,
    *,
    patient_only=True,
    require_plan_reason=True,
    source_stated_reasons=(),
):
    if not isinstance(fields, dict) or not all(
        isinstance(v, str) for v in fields.values()
    ):
        return {
            "passed": False,
            "failures": ["invalid-field-map"],
            "independent_content_review_required": True,
        }
    result = _CAPTURE.evaluate(
        case, {k: normalize(v) for k, v in fields.items()}, patient_only=patient_only
    )
    failures = list(result["failures"])
    omitted = []
    if not any("medication" in k.casefold() for k in fields):
        failures = [v for v in failures if v != "medication-list-unknown"]
        omitted = ["medication-list-unknown: schema has no medication-list field"]
    text = "\n".join(fields.values()).casefold().replace("e.g.", "eg")
    plan = "\n".join(v for k, v in fields.items() if "plan" in k.casefold()).casefold()
    sentences = re.split(r"[\n.;]+", text)

    def require(name, condition):
        if not condition:
            failures.append(name)

    # These exclusions and exam claims have no source evidence in R2/R3. A
    # tentative differential is distinct from an asserted exclusion/test result.
    if case["id"] in {"R2", "R3"}:
        require(
            "no-unsupported-diagnostic-exclusion",
            not re.search(
                r"no evidence[^.\n]{0,110}(?:fracture|dislocation|neurological compromise)|"
                r"(?:fracture|dislocation|neurological compromise)[^.\n]{0,45}(?:ruled out|excluded)",
                text,
            ),
        )
        require(
            "no-manufactured-normal-exam",
            not any(
                re.search(
                    r"(?:exam(?:ination)?|strength|sensation|range of motion)[^.\n]{0,35}\b(?:normal|intact|full)\b",
                    s,
                )
                and not re.search(
                    r"not (?:performed|assessed|examined)|no (?:exam|physical)", s
                )
                for s in sentences
            ),
        )
        require(
            "recommendation-not-booking",
            not re.search(
                r"\b(?:scheduled|booked|arranged|confirmed)\b[^.\n]{0,55}(?:follow.up|appointment|visit)|"
                r"(?:follow.up|appointment)[^.\n]{0,55}\b(?:scheduled|booked|arranged|confirmed)\b",
                plan,
            ),
        )
        for line in re.split(r"[\n;]+", plan):
            # The literal absence sentence cannot cancel an invented purpose in
            # the same bullet. Supplied accepted reasons must be source-reviewed.
            reasons = re.findall(
                r"\b(?:to (?:reduce|prevent|monitor|assess|improve|allow|avoid|promote|facilitate|support|reassess|manage)|because|for (?:symptom|pain|recovery|safety))\b[^.\n;]*",
                line,
            )
            require(
                "no-inferred-plan-rationale",
                all(
                    any(
                        reason in allowed.casefold()
                        for allowed in source_stated_reasons
                    )
                    for reason in reasons
                ),
            )
        if require_plan_reason and plan.strip():
            require(
                "plan-rationale-stated-or-unavailable",
                bool(source_stated_reasons)
                or "reason not stated in transcript" in plan
                or "reasons not stated in transcript" in plan,
            )
    if case["id"] == "R2":
        require(
            "correction-not-new-patient-task",
            not re.search(
                r"\buse (?:the )?corrected (?:count|episode)|"
                r"\brecord (?:exactly )?2 episodes|"
                r"\bdiary[^.\n]{0,60}(?:2 episodes|30 seconds)",
                plan,
            ),
        )
    return {
        "passed": not failures,
        "failures": list(dict.fromkeys(failures)),
        "schema_scoped_omissions": omitted,
        "independent_content_review_required": True,
        "claim_limit": "Finite synthetic retention/known-error checks only; no global clinical, role, citation or plan correctness proof.",
    }
