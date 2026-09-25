"""Run the actual retention shell/CLI boundaries against synthetic Azure state."""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "infra-staging/deploy.sh"
BOUND = ROOT / "scripts/staging_azure_cli_bound.py"
WORKER = "cosmaneura-staging-worker"
SCHEDULER = "cosmaneura-staging-scheduler"
API = "cosmaneura-staging-app"
APPS = (WORKER, SCHEDULER, API)
GROUP = "cosmaneura-staging"
SUBSCRIPTION = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"
REGISTRY = "cosmaneurastagingregistry.azurecr.io/cosmaneura/cosmaneura"
NEW_IMAGE = REGISTRY + "@sha256:" + "d" * 64
RETENTION_FLAG = "--max-inactive-revisions"


def _function(source: str, name: str) -> str:
    start = source.index("\n" + name + "() {") + 1
    return source[start : source.index("\n}", start) + 2]


def _recovery() -> dict:
    return {
        name: {
            "revision": name + "--synthetic-retained",
            "image": REGISTRY + "@sha256:" + digest * 64,
        }
        for name, digest in zip(APPS, "abc", strict=True)
    }


FAKE_AZURE = r"""
import json,sys
from pathlib import Path
fixture_path,trace_path,*args=sys.argv[1:]
with Path(trace_path).open("a") as stream:
    stream.write(json.dumps(args)+"\n")
if args[0]=="trace": raise SystemExit(0)
def value(flag): return args[args.index(flag)+1]
name=value("-n")
fixture=json.loads(Path(fixture_path).read_text())[name]
assert value("-g")=="cosmaneura-staging"
if args[:2]==["containerapp","update"]:
    print("{}")
elif args[:2]==["containerapp","show"]:
    assert value("--query")=="properties.configuration.maxInactiveRevisions"
    assert value("-o")=="tsv"
    if fixture.get("retention_read_failure"): raise SystemExit(7)
    print(fixture["retention"])
elif args[:3]==["containerapp","revision","show"]:
    assert value("--revision")==fixture["revision"]
    assert value("--query")=="{image:properties.template.containers[0].image,provisioning:properties.provisioningState}"
    assert value("-o")=="json"
    if fixture.get("missing_revision"): raise SystemExit(3)
    if fixture.get("malformed_response"):
        print("{")
    else:
        print(json.dumps({"image":fixture["image"],"provisioning":fixture["provisioning"]}))
else:
    raise SystemExit("unexpected_synthetic_azure_operation")
"""


def _shell(tmp_path, *, action="initiate", app=API, v2=True, overrides=None):
    source = DEPLOY.read_text()
    recovery = _recovery()
    fixtures = {
        name: {**row, "retention": 10, "provisioning": "Provisioned"}
        for name, row in recovery.items()
    }
    for name, changes in (overrides or {}).items():
        fixtures[name].update(changes)
    fixture_path, trace_path = tmp_path / "azure.json", tmp_path / "calls.jsonl"
    fixture_path.write_text(json.dumps(fixtures))
    fake = tmp_path / "fake_azure.py"
    fake.write_text(FAKE_AZURE)
    bindings = {
        "PYTHON_BIN": sys.executable,
        "RESOURCE_GROUP": GROUP,
        "API_CONTAINER": API,
        "WORKER_CONTAINER": WORKER,
        "SCHEDULER_CONTAINER": SCHEDULER,
        "BACKEND_CONTAINERS": " ".join(APPS[:2]),
        "CRITICAL_CONTAINERS": " ".join(APPS),
        "RELEASE_V2_JSON": json.dumps({"recovery": recovery}) if v2 else "",
        "STAGING_IMAGE_REFERENCE": NEW_IMAGE,
        "REVISION_STRING": "synthetic-next",
        "GIT_SHA": "e" * 40,
        "STAGING_PRESERVE_EFFECTIVE_CONFIGURATION": "true",
        "ENABLE_SOVEREIGN_RECORD_GRANTS": "false",
        "STRIPE_INTEGRATION_ENABLED": "false",
        "ENV_JSON": '{"PYTHON_ENV":"test"}',
        "GRANT_SECRET_ENV_NAME": "SOVEREIGN_RECORD_GRANT_SECRET",
        "RECALL_SECRET_ENV_NAME": "RECALL_WEBHOOK_SECRET",
        "STAGING_API_COMMAND": "synthetic-api",
        "STAGING_WORKER_COMMAND": "synthetic-worker",
        "SCHEDULER_COMMAND": "synthetic-scheduler",
    }
    script = "\n".join(f"{key}={shlex.quote(value)}" for key, value in bindings.items())
    script += """
COMMON_SECRET_VARS=()
API_ONLY_SECRET_VARS=()
BILLING_SECRET_VARS=()
BILLING_API_ONLY_SECRET_VARS=()
BILLING_PUBLIC_VARS=()
LEGACY_PUBLIC_ENV_VARS=()
PUBLIC_VARS=(PYTHON_ENV)
verify_preserved_runtime() { return 0; }
"""
    script += "\n".join(
        _function(source, name)
        for name in (
            "record_grants_enabled_for",
            "billing_workload_for",
            "billing_integration_enabled_for",
            "configure_workload_contract",
            "initiate_deployment",
            "verify_retained_recovery_revision",
        )
    )
    script += f"""
az() {{
    "$PYTHON_BIN" -I -S -B {shlex.quote(str(fake))} {shlex.quote(str(fixture_path))} {shlex.quote(str(trace_path))} "$@"
}}
check_deployment() {{ az trace check "$1"; }}
wait_for_incompatible_worker_replicas_to_drain() {{ az trace drain "$1"; }}
"""
    if action == "rollout":
        start = source.index("# Deploy and verify recovery workloads")
        end = source.index("\nfor container in $CRITICAL_CONTAINERS; do", start)
        script += source[start:end]
        script += "\naz trace rollout-completed\n"
    else:
        function = (
            "initiate_deployment"
            if action == "initiate"
            else "verify_retained_recovery_revision"
        )
        script += f'\n{function} {shlex.quote(app)} "$GIT_SHA"\n'
    result = subprocess.run(
        ["bash", "-c", script],
        env={"PATH": os.environ.get("PATH", os.defpath), "LC_ALL": "C"},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    calls = (
        [json.loads(line) for line in trace_path.read_text().splitlines()]
        if trace_path.exists()
        else []
    )
    return result, calls


@pytest.fixture(scope="module")
def bound():
    spec = importlib.util.spec_from_file_location("retention_bound_under_test", BOUND)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("app", APPS)
def test_v2_retention_is_in_the_single_image_env_update_and_v1_is_unchanged(
    tmp_path, app, bound
):
    commands = []
    for v2 in (False, True):
        directory = tmp_path / str(v2)
        directory.mkdir()
        result, calls = _shell(directory, app=app, v2=v2)
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(calls) == 1 and calls[0][:2] == ["containerapp", "update"]
        command = calls[0]
        assert command[command.index("-n") + 1] == app
        assert command[command.index("-i") + 1] == NEW_IMAGE
        assert "PYTHON_ENV=test" in command and "--set-env-vars" in command
        bound.validate_argv(command + ["--subscription", SUBSCRIPTION])
        commands.append(command)
    legacy, current = commands
    assert RETENTION_FLAG not in legacy
    assert current.count(RETENTION_FLAG) == 1
    position = current.index(RETENTION_FLAG)
    assert current[position + 1] == "10"
    assert current[:position] + current[position + 2 :] == legacy


@pytest.mark.parametrize("app", APPS)
def test_v2_verifies_exact_retained_revision_and_digest(tmp_path, app):
    result, calls = _shell(tmp_path, action="verify", app=app)
    assert result.returncode == 0, result.stdout + result.stderr
    assert [call[:2] for call in calls] == [
        ["containerapp", "show"],
        ["containerapp", "revision"],
    ]
    assert all(call[call.index("-n") + 1] == app for call in calls)
    assert calls[1][calls[1].index("--revision") + 1] == _recovery()[app]["revision"]


@pytest.mark.parametrize(
    "changes",
    [
        {"retention": 0},
        {"retention": 9},
        {"retention": 11},
        {"retention": ""},
        {"retention_read_failure": True},
        {"missing_revision": True},
        {"image": NEW_IMAGE},
        {"image": _recovery()[WORKER]["image"]},
        {"provisioning": "Failed"},
        {"provisioning": "Provisioning"},
        {"malformed_response": True},
    ],
)
def test_v2_refuses_lost_or_mismatched_recovery(tmp_path, changes):
    result, calls = _shell(tmp_path, action="verify", overrides={API: changes})
    assert result.returncode != 0
    assert calls and all(call[:2] != ["containerapp", "update"] for call in calls)
    if "retention" in changes or "retention_read_failure" in changes:
        assert len(calls) == 1, "Bad retention must fail before reading a revision"


def test_v1_retention_verification_does_not_read_or_modify_azure(tmp_path):
    result, calls = _shell(tmp_path, action="verify", v2=False)
    assert result.returncode == 0 and calls == []


@pytest.mark.parametrize("failed_app", [None, *APPS])
def test_real_rollout_checks_retention_after_each_ready_check_and_stops_on_loss(
    tmp_path, failed_app
):
    overrides = {failed_app: {"missing_revision": True}} if failed_app else None
    result, calls = _shell(tmp_path, action="rollout", overrides=overrides)
    expected = APPS if failed_app is None else APPS[: APPS.index(failed_app) + 1]
    updates = [call for call in calls if call[:2] == ["containerapp", "update"]]
    assert [call[call.index("-n") + 1] for call in updates] == list(expected)
    for app in expected:
        ready = calls.index(["trace", "check", app])
        retention = next(
            i
            for i, call in enumerate(calls)
            if call[:2] == ["containerapp", "show"]
            and call[call.index("-n") + 1] == app
        )
        revision = next(
            i
            for i, call in enumerate(calls)
            if call[:3] == ["containerapp", "revision", "show"]
            and call[call.index("-n") + 1] == app
        )
        assert ready < retention < revision
    assert (result.returncode == 0) == (failed_app is None)
    assert (["trace", "rollout-completed"] in calls) == (failed_app is None)


def _update(app=API, option=None):
    return [
        "containerapp",
        "update",
        "--name",
        app,
        "--resource-group",
        GROUP,
        "--image",
        NEW_IMAGE,
        *(option or []),
        "--subscription",
        SUBSCRIPTION,
    ]


@pytest.mark.parametrize(
    "option",
    [
        [RETENTION_FLAG, "0"],
        [RETENTION_FLAG, "9"],
        [RETENTION_FLAG, "11"],
        [RETENTION_FLAG, "010"],
        [RETENTION_FLAG, "10.0"],
        [RETENTION_FLAG, "+10"],
        [RETENTION_FLAG, "10", RETENTION_FLAG, "10"],
        [RETENTION_FLAG],
        ["--max-inactive-revisions=10"],
        ["--max-inactive-revision", "10"],
        ["--max-inactive-r", "10"],
        ["--MAX-INACTIVE-REVISIONS", "10"],
    ],
)
def test_bound_cli_refuses_nonexact_retention_option(bound, option):
    with pytest.raises(bound.BoundAzureRefused):
        bound.validate_argv(_update(option=option))


@pytest.mark.parametrize(
    "prefix",
    [
        ["containerapp", "show"],
        ["containerapp", "revision", "show"],
        ["containerapp", "secret", "set"],
        ["containerapp", "job", "create"],
    ],
)
def test_bound_cli_refuses_retention_on_other_operations(bound, prefix):
    app = "cosmaneura-staging-migrate" if prefix[:2] == ["containerapp", "job"] else API
    command = [
        *prefix,
        "--name",
        app,
        "--resource-group",
        GROUP,
        "--subscription",
        SUBSCRIPTION,
    ]
    bound.validate_argv(command)
    with pytest.raises(bound.BoundAzureRefused):
        bound.validate_argv(command + [RETENTION_FLAG, "10"])


@pytest.mark.parametrize(
    "app",
    [
        "cosmaneura-staging-scribe-api",
        "cosmaneura-staging-scribe-native",
        "cosmaneura-sovereign-staging-app",
        "cosmaneura-staging-migrate",
        "cosmaneuraprod-app",
        "foreign-app",
    ],
)
def test_bound_cli_never_grants_retention_updates_to_non_core_targets(bound, app):
    with pytest.raises(bound.BoundAzureRefused):
        bound.validate_argv(_update(app, [RETENTION_FLAG, "10"]))
