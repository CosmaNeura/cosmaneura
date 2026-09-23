"""Actual recovery validation; invented resources and no cloud operations."""

import copy
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
m = runpy.run_path(str(ROOT / "scripts/staging_runtime_operations.py"))
bound = runpy.run_path(str(ROOT / "scripts/staging_azure_cli_bound.py"))


def fixtures():
    names = m["CONTRACT"]["APPS"]
    old = m["CONTRACT"]["REGISTRY"] + "/scribe-beta/core@sha256:" + "a" * 64
    new = m["CONTRACT"]["IMAGE"] + "@sha256:" + "b" * 64
    recovery = {name: {"image": old, "revision": name + "--old"} for name in names}
    observed = {
        name: {
            "configuration": {"activeRevisionsMode": "Single"},
            "latestReadyRevisionName": name + "--new",
            "latestRevisionName": name + "--new",
            "readyImage": new,
            "template": {"containers": [{"image": new}]},
        }
        for name in names
    }
    retained = {
        name: {
            "provisioningState": "Provisioned",
            "template": {"containers": [{"image": old}]},
        }
        for name in names
    }
    return (
        {"release": {"recovery": recovery}},
        {"image_reference": new},
        observed,
        retained,
    )


def test_recovery_allows_the_exact_old_web_repository_and_rejects_drift():
    plan, receipt, current, retained = fixtures()
    m["recovery_preflight"](plan, receipt, current, retained)
    name = "cosmaneura-staging-app"
    current[name]["latestReadyRevisionName"] = name + "--old"
    current[name]["readyImage"] = plan["release"]["recovery"][name]["image"]
    m["recovery_preflight"](plan, receipt, current, retained)
    for target, key, value in [
        (current[name], "readyImage", "unrelated"),
        (current[name]["configuration"], "activeRevisionsMode", "Multiple"),
        (current[name]["template"]["containers"][0], "image", "unrelated"),
        (retained[name]["template"]["containers"][0], "image", "unrelated"),
    ]:
        original = target[key]
        target[key] = value
        with pytest.raises(ValueError):
            m["recovery_preflight"](plan, receipt, current, retained)
        target[key] = original


def test_bound_runtime_commands_reject_cross_app_revision_and_extra_changes():
    name = "cosmaneura-staging-app"
    suffix = ["--name", name, "--resource-group", m["GROUP"]]
    ending = [
        "--subscription",
        m["SUBSCRIPTION"],
        "--only-show-errors",
        "--output",
        "json",
    ]
    command = [
        "containerapp",
        "revision",
        "copy",
        *suffix,
        "--from-revision",
        name + "--old",
        "--revision-suffix",
        "recover-" + "a" * 12,
        *ending,
    ]
    validate = bound["validate_runtime_operation"]
    validate(command, "recover-core")
    for changed in [
        command + ["--image", "unreviewed"],
        [v.replace(name + "--old", "cosmaneura-staging-worker--old") for v in command],
        [v.replace(name, "cosmaneura-production-app") for v in command],
    ]:
        with pytest.raises(bound["BoundAzureRefused"]):
            validate(changed, "recover-core")
    warm = [
        "containerapp",
        "update",
        "--name",
        m["SCRIBE"],
        "--resource-group",
        m["GROUP"],
        "--min-replicas",
        "1",
        "--max-replicas",
        "1",
        *ending,
    ]
    validate(warm, "warm-scribe")
    for extra in (["--image", "anything"], ["--set-env-vars", "FLAG=true"]):
        with pytest.raises(bound["BoundAzureRefused"]):
            validate(warm + extra, "warm-scribe")


def test_warming_comparison_excludes_only_minimum_and_generated_suffix():
    original = {
        "revisionSuffix": "old",
        "scale": {"minReplicas": 0, "maxReplicas": 1},
        "containers": [{"image": "unchanged", "env": [{"name": "X", "value": "Y"}]}],
    }
    changed = copy.deepcopy(original)
    changed["revisionSuffix"] = "new"
    changed["scale"]["minReplicas"] = 1
    assert m["stable_template"](original) == m["stable_template"](changed)
    changed["containers"][0]["env"][0]["value"] = "changed"
    assert m["stable_template"](original) != m["stable_template"](changed)
