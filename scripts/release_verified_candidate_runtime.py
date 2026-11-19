"""Record, verify, and roll back production Container App digests/revisions.

Used by Release verified candidate after publish. Migrations executed by
deploy.sh are forward-only: image rollback restores prior digests/revisions
but does not reverse Alembic migrations. Operators must treat schema changes
as a separate recovery concern.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DIGEST_REF = re.compile(
    r"^cosmaneuraprodregistry\.azurecr\.io/cosmaneura/cosmaneura@sha256:[0-9a-f]{64}$"
)
APPS = (
    "cosmaneura-prod-app",
    "cosmaneura-prod-worker",
    "cosmaneura-prod-scheduler",
)
RESOURCE_GROUP = "cosmaneura-prod"
HEALTHCHECK_URL = "https://app.cosmaneura.com/healthcheck"
MIGRATION_POLICY = (
    "Image rollback restores prior Container App digests and revisions only. "
    "Database migrations applied by deploy.sh are forward-only and are not "
    "reversed by this rollback. If schema compatibility is broken, stop and "
    "use a reviewed migration recovery path."
)


def refuse(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def az_json(args: list[str]) -> Any:
    raw = subprocess.check_output(
        ["az", *args, "--output", "json"],
        text=True,
    )
    return json.loads(raw)


def record_baseline(path: Path) -> dict[str, Any]:
    apps: dict[str, Any] = {}
    for name in APPS:
        show = az_json(
            [
                "containerapp",
                "show",
                "-n",
                name,
                "-g",
                RESOURCE_GROUP,
            ]
        )
        properties = show.get("properties") or {}
        template = properties.get("template") or {}
        containers = template.get("containers") or []
        if not containers:
            refuse(f"No containers on {name}.")
        image = containers[0].get("image")
        ready = properties.get("latestReadyRevisionName")
        latest = properties.get("latestRevisionName")
        apps[name] = {
            "image": image,
            "latestReadyRevisionName": ready,
            "latestRevisionName": latest,
        }
    payload = {
        "resource_group": RESOURCE_GROUP,
        "apps": apps,
        "migration_policy": MIGRATION_POLICY,
        "healthcheck_url": HEALTHCHECK_URL,
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return payload


def rollback(baseline_path: Path) -> None:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    print("ROLLBACK: restoring recorded digests/revisions.", file=sys.stderr)
    print(MIGRATION_POLICY, file=sys.stderr)
    for name, state in baseline.get("apps", {}).items():
        image = state.get("image")
        if not image:
            refuse(f"Baseline missing image for {name}.")
        print(f"az containerapp update -n {name} -g {RESOURCE_GROUP} -i {image}")
        subprocess.check_call(
            [
                "az",
                "containerapp",
                "update",
                "-n",
                name,
                "-g",
                RESOURCE_GROUP,
                "-i",
                image,
                "--only-show-errors",
            ]
        )


def verify_ready(
    *,
    expected_image: str,
    baseline_path: Path,
    evidence_path: Path,
) -> None:
    if not DIGEST_REF.fullmatch(expected_image):
        refuse("Expected image must be an immutable production digest ref.")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    evidence: dict[str, Any] = {
        "expected_image": expected_image,
        "healthcheck_url": HEALTHCHECK_URL,
        "migration_policy": MIGRATION_POLICY,
        "baseline": baseline,
        "apps": {},
    }
    try:
        with urllib.request.urlopen(HEALTHCHECK_URL, timeout=30) as response:
            status = getattr(response, "status", None) or response.getcode()
            body = response.read(256)
    except (urllib.error.URLError, TimeoutError) as exc:
        evidence["healthcheck"] = {"result": "failed", "error": type(exc).__name__}
        evidence_path.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps(evidence, indent=2, sort_keys=True))
        print("Healthcheck failed; rolling back.", file=sys.stderr)
        rollback(baseline_path)
        refuse("app.cosmaneura.com/healthcheck failed.")
    evidence["healthcheck"] = {
        "result": "passed" if status == 200 else "failed",
        "http_status": status,
        "body_prefix_sha256_len": len(body),
    }
    if status != 200:
        evidence_path.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps(evidence, indent=2, sort_keys=True))
        rollback(baseline_path)
        refuse("Healthcheck returned a non-200 status.")

    for name in APPS:
        show = az_json(["containerapp", "show", "-n", name, "-g", RESOURCE_GROUP])
        properties = show.get("properties") or {}
        ready_name = properties.get("latestReadyRevisionName")
        if not ready_name:
            evidence["apps"][name] = {"error": "missing_ready_revision"}
            evidence_path.write_text(
                json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            rollback(baseline_path)
            refuse(f"{name} has no ready revision.")
        revision = az_json(
            [
                "containerapp",
                "revision",
                "show",
                "-n",
                name,
                "-g",
                RESOURCE_GROUP,
                "--revision",
                ready_name,
            ]
        )
        image = (
            ((revision.get("properties") or {}).get("template") or {})
            .get("containers", [{}])[0]
            .get("image")
        )
        evidence["apps"][name] = {
            "latestReadyRevisionName": ready_name,
            "image": image,
        }
        if image != expected_image:
            evidence_path.write_text(
                json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            print(json.dumps(evidence, indent=2, sort_keys=True))
            rollback(baseline_path)
            refuse(f"{name} ready revision digest mismatch.")

    evidence["result"] = "passed"
    evidence_path.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(evidence, indent=2, sort_keys=True))
    print(MIGRATION_POLICY)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("record-baseline", "verify-ready", "rollback"))
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--expected-image")
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    if args.mode == "record-baseline":
        record_baseline(args.baseline)
        return 0
    if args.mode == "rollback":
        rollback(args.baseline)
        return 0
    if not args.expected_image or args.evidence is None:
        refuse("verify-ready requires --expected-image and --evidence.")
    verify_ready(
        expected_image=args.expected_image,
        baseline_path=args.baseline,
        evidence_path=args.evidence,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
