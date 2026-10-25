"""Regrade a complete primary batch without editing any archived receipt."""

import argparse
import hashlib
import json
from pathlib import Path

from oracle import evaluate


def regrade(directory):
    inputs_path = directory / "inputs.json"
    inputs = json.loads(inputs_path.read_text())
    if inputs["identity"].get("role_variant") != "observed" or inputs["identity"].get(
        "ros_variant"
    ):
        raise ValueError(
            "This command accepts the unchanged primary role/schema matrix only"
        )
    cases = {case["id"]: case for case in inputs["fixture"]["cases"][:2]}
    rows = []
    for case_id in ("R2", "R3"):
        for variant in ("baseline", "candidate"):
            for repetition in range(1, 4):
                name = f"{case_id}-{variant}-{repetition}"
                path = directory / (name + ".json")
                receipt = json.loads(path.read_text())
                if receipt["identity"] != inputs["identity"]:
                    raise ValueError("Receipt/input identity differs")
                result = (
                    evaluate(cases[case_id], receipt["resolved_fields"])
                    if "resolved_fields" in receipt
                    else {"passed": False, "failures": ["model-or-contract-failed"]}
                )
                rows.append(
                    {
                        "name": name,
                        "receipt_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "original_oracle": receipt.get("oracle"),
                        "current_oracle": result,
                    }
                )
    return {
        "scope": "offline regrade of all 12 preserved actual-model primary results",
        "inputs_sha256": hashlib.sha256(inputs_path.read_bytes()).hexdigest(),
        "oracle_sha256": hashlib.sha256(
            Path(__file__).with_name("oracle.py").read_bytes()
        ).hexdigest(),
        "results": rows,
        "candidate_oracle_passed": all(
            row["current_oracle"]["passed"]
            for row in rows
            if "-candidate-" in row["name"]
        ),
        "original_receipts_unchanged": True,
        "independent_content_review_required": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = regrade(args.run)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
    print(
        json.dumps(
            {
                "candidate_oracle_passed": result["candidate_oracle_passed"],
                "results": len(result["results"]),
            }
        )
    )
    raise SystemExit(0 if result["candidate_oracle_passed"] else 1)
