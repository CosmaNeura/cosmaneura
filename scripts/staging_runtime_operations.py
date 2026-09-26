#!/usr/bin/env python3
"""Checked Core revision recovery and unchanged-image Scribe warm capacity.

Recovery copies each retained revision's template, preserving its environment,
command and scale settings. It never downgrades the additive database schema.
Invoke from the clean merged controller with hash-bound release receipts.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import runpy
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUBSCRIPTION = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"
GROUP = "cosmaneura-staging"
SCRIBE = "cosmaneura-staging-scribe-api"
AZ = "/opt/homebrew/bin/az"
AZ_PYTHON = "/opt/homebrew/Cellar/azure-cli/2.89.1/libexec/bin/python"
CONTRACT = runpy.run_path(str(ROOT / "scripts/staging_release_contract.py"))
require = CONTRACT["require"]


def command(argv, *, input=None):
    result = subprocess.run(argv, input=input, capture_output=True, timeout=360)
    require(result.returncode == 0)
    return result.stdout


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def az(operation, name, prefix, *arguments):
    token = command(
        [
            AZ,
            "account",
            "get-access-token",
            "--subscription",
            SUBSCRIPTION,
            "--resource",
            "https://management.azure.com/",
            "--output",
            "json",
            "--only-show-errors",
        ]
    )
    # Token goes only over stdin -> fd 3; the bound CLI validates and uses this
    # exact user credential instead of reacquiring from a mutable shared cache.
    raw = command(
        [
            "/bin/bash",
            "-c",
            'exec "$@" 3<&0',
            "bound-azure",
            AZ_PYTHON,
            "-I",
            "-B",
            str(ROOT / "scripts/staging_azure_cli_bound.py"),
            "--runtime-operation",
            operation,
            *prefix,
            "--name",
            name,
            "--resource-group",
            GROUP,
            *arguments,
            "--subscription",
            SUBSCRIPTION,
            "--only-show-errors",
            "--output",
            "json",
        ],
        input=token,
    )
    return json.loads(raw)


def app(operation, name):
    return az(operation, name, ["containerapp", "show"])["properties"]


def revision(operation, name, revision_name):
    return az(
        operation,
        name,
        ["containerapp", "revision", "show"],
        "--revision",
        revision_name,
    )["properties"]


def image(template):
    require(len(template["containers"]) == 1)
    return template["containers"][0]["image"]


def ready(operation, name, expected_revision, expected_image):
    for _ in range(60):
        value = app(operation, name)
        if (
            value["latestRevisionName"]
            == value["latestReadyRevisionName"]
            == expected_revision
        ):
            observed = revision(operation, name, expected_revision)
            require(image(observed["template"]) == expected_image)
            require(
                observed["healthState"] == "Healthy"
                and observed["provisioningState"] == "Provisioned"
            )
            return value
        time.sleep(5)
    raise ValueError("staging_runtime_revision_not_ready")


def verify_controller(plan):
    require(
        command(["/usr/bin/git", "-C", str(ROOT), "rev-parse", "HEAD"]).decode().strip()
        == plan["controller_sha"]
    )
    require(command(["/usr/bin/git", "-C", str(ROOT), "status", "--porcelain"]) == b"")


def recovery_preflight(plan, publication, observed, retained):
    recovery = plan["release"]["recovery"]
    CONTRACT["validate_recovery"](recovery)
    require(set(observed) == set(retained) == set(recovery))
    for name, expected in recovery.items():
        current = observed[name]
        require(current["configuration"]["activeRevisionsMode"] == "Single")
        # A failed candidate may be latest while the previous revision remains
        # ready. Recovery must remain available in that partial-deploy state.
        require(
            current["readyImage"] in {publication["image_reference"], expected["image"]}
        )
        require(
            image(current["template"])
            in {publication["image_reference"], expected["image"]}
        )
        require(image(retained[name]["template"]) == expected["image"])
        require(retained[name]["provisioningState"] == "Provisioned")


def recover(plan, publication, execute):
    operation = "recover-core"
    recovery = plan["release"]["recovery"]
    observed = {name: app(operation, name) for name in recovery}
    for name, value in observed.items():
        value["readyImage"] = image(
            revision(operation, name, value["latestReadyRevisionName"])["template"]
        )
    retained = {
        name: revision(operation, name, value["revision"])
        for name, value in recovery.items()
    }
    recovery_preflight(plan, publication, observed, retained)
    result = {
        "operation": operation,
        "executed": execute,
        "schema_downgraded": False,
        "workloads": [],
    }
    for name in (
        "cosmaneura-staging-scheduler",
        "cosmaneura-staging-worker",
        "cosmaneura-staging-app",
    ):
        expected = recovery[name]
        row = {
            "name": name,
            "source_revision": expected["revision"],
            "image": expected["image"],
        }
        if execute:
            # Refuse if another release changed this app since preflight.
            require(
                app(operation, name)["latestRevisionName"]
                == observed[name]["latestRevisionName"]
            )
            suffix = "recover-" + uuid.uuid4().hex[:12]
            az(
                operation,
                name,
                ["containerapp", "revision", "copy"],
                "--from-revision",
                expected["revision"],
                "--revision-suffix",
                suffix,
            )
            final = ready(operation, name, name + "--" + suffix, expected["image"])
            row["ready_revision"] = final["latestReadyRevisionName"]
        result["workloads"].append(row)
    return result


def stable_template(template):
    value = copy.deepcopy(template)
    value.pop("revisionSuffix", None)
    value["scale"].pop("minReplicas", None)
    return value


def warm(plan, baseline, execute):
    require(set(baseline) == {"name", "revision", "image", "template_sha256"})
    require(baseline["name"] == SCRIBE)
    require(
        re.fullmatch(
            r"cosmaneurastagingregistry\.azurecr\.io/scribe-beta/api@sha256:[0-9a-f]{64}",
            baseline["image"],
        )
    )
    current = app("warm-scribe", SCRIBE)
    require(
        current["latestRevisionName"]
        == current["latestReadyRevisionName"]
        == baseline["revision"]
    )
    require(current["configuration"]["activeRevisionsMode"] == "Single")
    require(image(current["template"]) == baseline["image"])
    require(current["template"]["scale"]["maxReplicas"] == 1)
    require(digest(stable_template(current["template"])) == baseline["template_sha256"])
    result = {
        "operation": "warm-scribe",
        "executed": execute,
        "image": baseline["image"],
        "prior_revision": baseline["revision"],
    }
    if execute:
        updated = az(
            "warm-scribe",
            SCRIBE,
            ["containerapp", "update"],
            "--min-replicas",
            "1",
            "--max-replicas",
            "1",
        )["properties"]
        final = ready(
            "warm-scribe", SCRIBE, updated["latestRevisionName"], baseline["image"]
        )
        require(
            digest(stable_template(final["template"])) == baseline["template_sha256"]
        )
        require(final["template"]["scale"]["minReplicas"] == 1)
        result["ready_revision"] = final["latestReadyRevisionName"]
    return result


def main(argv):
    require(
        len(argv) in {5, 7}
        and argv[0] in {"recover-check", "recover", "warm-check", "warm"}
    )
    action, plan_path, plan_hash, receipt_path, receipt_hash, *extra = argv
    plan = CONTRACT["bound_json"](plan_path, plan_hash)
    publication = CONTRACT["bound_json"](receipt_path, receipt_hash)
    CONTRACT["deployment_inputs"](plan, plan_hash, publication)
    publication = CONTRACT["resolve_publication"](plan, plan_hash, publication)
    verify_controller(plan)
    if action.startswith("recover"):
        require(not extra)
        result = recover(plan, publication, action == "recover")
    else:
        require(len(extra) == 2)
        result = warm(plan, CONTRACT["bound_json"](*extra), action == "warm")
    # Recovery intentionally remains usable after the build plan expires. It
    # only restores its exact retained revisions, with current-state checks.
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        raise SystemExit("staging_runtime_operation_refused") from None
