"""Fresh pure bridge qualification after local temporary source loss."""

import copy
import json
import runpy
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
E = runpy.run_path(str(ROOT / "scripts/staging_scribe_initialization_executor.py"))
B = runpy.run_path(
    str(ROOT / "tests/infrastructure/test_staging_scribe_public_initialization.py")
)
BOUND = runpy.run_path(str(ROOT / "scripts/staging_azure_cli_bound.py"))


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    before, plan, ctx = B["sample"].__wrapped__()
    states, calls = copy.deepcopy(before), []
    build = {"release": {"runtime_public": plan["controls"]}}
    configuration = {"SESSION_SECRET_KEY": B["PRIVATE"]}

    class Clock:
        @staticmethod
        def now(_):
            return B["NOW"]

    monkeypatch.setitem(E["step"].__globals__, "datetime", Clock)
    monkeypatch.setitem(
        E["step"].__globals__, "observe", lambda **_: copy.deepcopy(states)
    )

    def update(operation, name, prefix, *args):
        assert operation == E["OPERATION"] and prefix == ["containerapp", "update"]
        calls.append((name, list(args)))
        controls = dict(item.split("=", 1) for item in args[5:])
        states[name] = E["PURE"]["transformed"](
            states[name], plan["controls"] | controls, args[1], name + "--" + args[3]
        )

    monkeypatch.setitem(E["RUNTIME"], "az", update)
    monkeypatch.setitem(E["RUNTIME"], "ready", lambda *args: None)
    journal = E["Journal"](tmp_path / "journal.json", plan, "f" * 64)
    entry = E["PURE"]["validate_transition"](plan, states, ctx, B["NOW"])
    journal.write(
        journal.binding
        | {"entry": entry, "completed": [], "intent": None, "closed_completed": []}
    )
    ctx = ctx | {"schema_revision": plan["bindings"]["schema_after"]}
    yield states, plan, ctx, journal, calls, configuration, build
    journal.close()


def test_actual_steps_keep_private_raw_state_and_phase_order(bridge):
    states, plan, ctx, journal, calls, configuration, build = bridge
    for name in E["PURE"]["PHASES"]:
        E["step"](plan, ctx, journal, configuration, build)
        assert calls[-1][0] == name
    assert E["step"](plan, ctx, journal, configuration, build)["complete"]
    assert len(calls) == 3
    for name, args in calls:
        assert not any(
            flag in args
            for flag in ["--args", "--command", "--secrets", "--remove-env-vars"]
        )
        keys = {item.split("=", 1)[0] for item in args[5:]}
        assert keys == (E["PURE"]["FIELDS"] if name == B["API"] else {B["LEDGER"]})
        assert states[name]["secret_values"]["session-secret-key"] == B["PRIVATE"]
    raw = journal.path.read_text()
    assert (
        B["PRIVATE"] not in raw
        and "secret_values" not in raw
        and "private_sha256" in raw
    )


@pytest.mark.parametrize("after_write", [False, True])
def test_uncertain_initialization_never_retries_and_can_close(
    bridge, monkeypatch, after_write
):
    _, plan, ctx, journal, _, configuration, build = bridge
    normal = E["RUNTIME"]["az"]

    def fail(*args):
        if after_write:
            normal(*args)
        raise OSError("synthetic unknown response")

    monkeypatch.setitem(E["RUNTIME"], "az", fail)
    with pytest.raises(OSError):
        E["step"](plan, ctx, journal, configuration, build)
    with pytest.raises(ValueError):
        E["step"](plan, ctx, journal, configuration, build)
    monkeypatch.setitem(E["RUNTIME"], "az", normal)
    for _ in E["PURE"]["PHASES"]:
        E["step"](plan, ctx, journal, configuration, build, recover=True)
    result = E["step"](plan, ctx, journal, configuration, build, recover=True)
    assert result["ledger_ready"] and result["raw_template_rollback_allowed"] is False


@pytest.mark.parametrize("after_write", [False, True])
def test_uncertain_closed_target_is_never_resubmitted(bridge, monkeypatch, after_write):
    _, plan, ctx, journal, calls, configuration, build = bridge
    normal = E["RUNTIME"]["az"]

    def fail(*args):
        if after_write:
            normal(*args)
        raise OSError("synthetic unknown closed response")

    monkeypatch.setitem(E["RUNTIME"], "az", fail)
    with pytest.raises(OSError):
        E["step"](plan, ctx, journal, configuration, build, recover=True)
    monkeypatch.setitem(E["RUNTIME"], "az", normal)
    count = len(calls)
    if after_write:
        assert E["step"](plan, ctx, journal, configuration, build, recover=True)[
            "acknowledged"
        ]
    else:
        with pytest.raises(ValueError):
            E["step"](plan, ctx, journal, configuration, build, recover=True)
    assert len(calls) == count


@pytest.mark.parametrize(
    "mutation",
    [
        "private",
        "raw_extra",
        "target_without_receipt",
        "foreign_journal",
        "resolved_secret",
    ],
)
def test_drift_or_unrecorded_target_refuses_before_write(bridge, mutation):
    states, plan, ctx, journal, calls, configuration, build = bridge
    name = E["PURE"]["PHASES"][0]
    if mutation == "private":
        states[name]["secret_values"]["session-secret-key"] = "invented drift"
    elif mutation == "raw_extra":
        states[name]["template"]["containers"][0]["imageType"] = "invented drift"
    elif mutation == "target_without_receipt":
        states[name] = B["target"](states, plan, name)
    elif mutation == "foreign_journal":
        value = journal.read()
        value["plan_sha256"] = "sha256:" + "0" * 64
        journal.write(value)
    else:
        configuration["SESSION_SECRET_KEY"] = "invented drift"
    with pytest.raises(ValueError):
        E["step"](plan, ctx, journal, configuration, build)
    assert calls == []


@pytest.mark.parametrize(
    "key", ["phase_order", "before_sha256", "desired_sha256", "enabled"]
)
def test_entry_requires_entire_pinned_receipt(bridge, key):
    _, plan, ctx, journal, calls, configuration, build = bridge
    value = journal.read()
    value["entry"][key] = "invented false receipt"
    journal.write(value)
    with pytest.raises(ValueError):
        E["step"](plan, ctx, journal, configuration, build, recover=True)
    assert calls == []


@pytest.mark.parametrize("kind", ["completed", "closed_completed", "intent"])
def test_journal_refuses_unordered_or_forged_phases(bridge, kind):
    _, plan, ctx, journal, calls, configuration, build = bridge
    value = journal.read()
    name = E["PURE"]["PHASES"][1]
    value[kind] = (
        {"name": name, "closed": False, "target": plan["desired"][name]}
        if kind == "intent"
        else [{"name": name, **plan["desired"][name]}]
    )
    journal.write(value)
    with pytest.raises(ValueError):
        E["step"](plan, ctx, journal, configuration, build, recover=True)
    assert calls == []


def test_health_keeps_true_worker_and_absence_elsewhere(bridge):
    states, _, _, _, _, configuration, build = bridge
    key = "SESSION_PREP_HEALTH_ALERTS_ENABLED"
    build["release"]["runtime_public"][key] = "false"
    with pytest.raises(ValueError):
        E["resolved_consistency"](configuration, states, build)
    states["cosmaneura-staging-worker"]["template"]["containers"][0]["env"].append(
        {"name": key, "value": "true"}
    )
    build["release"]["runtime_public"][key] = "true"
    E["resolved_consistency"](configuration, states, build)
    assert key not in E["PURE"]["environment"](states[B["API"]])
    assert key not in E["PURE"]["environment"](states["cosmaneura-staging-scheduler"])


def test_fixed_bound_grammar_refuses_other_fields_and_resources(bridge):
    _, plan, _, _, _, _, _ = bridge
    name = B["API"]
    args = E["update_arguments"](name, plan["desired"][name], plan["controls"])
    prefix = [
        "containerapp",
        "update",
        "--name",
        name,
        "--resource-group",
        E["RUNTIME"]["GROUP"],
    ]
    ending = [
        "--subscription",
        E["RUNTIME"]["SUBSCRIPTION"],
        "--only-show-errors",
        "--output",
        "json",
    ]
    command = prefix + args + ending
    BOUND["validate_runtime_operation"](command, E["OPERATION"])
    for changed in [
        command + ["--args", "bad"],
        prefix + args + ["STRIPE_LIVE_MODE=true"] + ending,
        [v.replace(name, "cosmaneura-production-app") for v in command],
    ]:
        with pytest.raises(BOUND["BoundAzureRefused"]):
            BOUND["validate_runtime_operation"](changed, E["OPERATION"])


@pytest.mark.parametrize(
    "key",
    [
        "authenticated_source_checked",
        "local_bindings_passed",
        "build_started",
        "candidate_sha",
        "controller_sha",
        "approval_pr_number",
        "schema_head",
        "plan_sha256",
        "github_user_sha256",
        "source_archive_sha256",
    ],
)
def test_source_check_requires_every_actual_binding(monkeypatch, key):
    build = {
        "candidate_sha": "a" * 40,
        "controller_sha": "b" * 40,
        "candidate_pr": 1,
        "release": {"schema_after": "abc"},
        "github_user_sha256": "c" * 64,
        "source_archive_sha256": "d" * 64,
    }
    receipt = {
        "authenticated_source_checked": True,
        "local_bindings_passed": True,
        "build_started": False,
        "candidate_sha": build["candidate_sha"],
        "controller_sha": build["controller_sha"],
        "approval_pr_number": 1,
        "schema_head": "abc",
        "plan_sha256": "e" * 64,
        "github_user_sha256": build["github_user_sha256"],
        "source_archive_sha256": build["source_archive_sha256"],
    }
    monkeypatch.setitem(
        E["RUNTIME"], "command", lambda args: json.dumps(receipt).encode()
    )
    E["source_check"]("/private/tmp/invented.json", "e" * 64, build)
    receipt[key] = "invented stale receipt"
    with pytest.raises(ValueError):
        E["source_check"]("/private/tmp/invented.json", "e" * 64, build)


def test_observer_has_only_fixed_reads_and_no_private_output(
    bridge, monkeypatch, capsys
):
    states, _, _, _, _, _, _ = bridge
    calls = []

    def read(operation, name, prefix, *args):
        calls.append((operation, name, prefix, args))
        state = states[name]
        if prefix == ["containerapp", "show"]:
            return {
                "identity": state["identity"],
                "properties": {
                    "latestRevisionName": state["revision"],
                    "latestReadyRevisionName": state["revision"],
                    "template": state["template"],
                    "configuration": state["configuration"],
                },
            }
        assert prefix == ["containerapp", "secret", "list"] and args == (
            "--show-values",
        )
        return [
            {"name": key, "value": value}
            for key, value in state["secret_values"].items()
        ]

    original = E["observe"]
    monkeypatch.setitem(original.__globals__, "observe", original)
    monkeypatch.setitem(E["RUNTIME"], "az", read)
    monkeypatch.setitem(
        E["RUNTIME"],
        "revision",
        lambda *args: {"healthState": "Healthy", "provisioningState": "Provisioned"},
    )
    assert original() == states
    assert len(calls) == 6 and not capsys.readouterr().out
    for operation, name, prefix, args in calls:
        command = [
            *prefix,
            "--name",
            name,
            "--resource-group",
            E["RUNTIME"]["GROUP"],
            *args,
            "--subscription",
            E["RUNTIME"]["SUBSCRIPTION"],
            "--only-show-errors",
            "--output",
            "json",
        ]
        BOUND["validate_runtime_operation"](command, operation)


@pytest.mark.parametrize(
    "action,fail",
    [("initialize", False), ("closed", False), ("initialize", True), ("", False)],
)
def test_actual_shell_phase_wiring_and_default_path(tmp_path, action, fail):
    source = (ROOT / "infra-staging/deploy.sh").read_text()
    start = source.index(
        'if [[ -n "$STAGING_SCRIBE_INIT_PLAN" ]]; then\n\t[[ "$SCHEMA_AFTER"'
    )
    end = source.index("\n# Deploy and verify recovery workloads", start)
    calls = tmp_path / "calls"
    script = "set -euo pipefail\n" + "\n".join(
        [
            "STAGING_SCRIBE_INIT_PLAN="
            + shlex.quote("/private/tmp/invented.json" if action else ""),
            "STAGING_SCRIBE_INIT_ACTION=" + shlex.quote(action),
            "SCHEMA_AFTER=abc",
            "EXPECTED_SCHEMA_AFTER=abc",
            "GIT_SHA=" + "a" * 40,
            'BACKEND_CONTAINERS="cosmaneura-staging-worker cosmaneura-staging-scheduler"',
            "API_CONTAINER=cosmaneura-staging-app",
            "WORKER_CONTAINER=cosmaneura-staging-worker",
            "CALLS=" + shlex.quote(str(calls)),
            'verify_staging_release_source() { printf "source\\n" >> "$CALLS"; }',
            'staging_scribe_bridge() { printf "%s %s\\n" "$1" "$2" >> "$CALLS"; '
            + (
                '[[ "${container:-}" != "cosmaneura-staging-scheduler" ]];'
                if fail
                else "return 0;"
            )
            + " }",
            'wait_for_incompatible_worker_replicas_to_drain() { printf "drain\\n" >> "$CALLS"; }',
        ]
    )
    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            script + "\n" + source[start:end] + '\nprintf "legacy\\n" >> "$CALLS"\n',
        ],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        check=False,
        timeout=10,
    )
    rows = calls.read_text().splitlines()
    if not action:
        assert result.returncode == 0 and rows == ["legacy"]
    elif fail:
        assert result.returncode == 1 and rows == [
            "source",
            "step abc",
            "drain",
            "source",
            "step abc",
        ]
    elif action == "initialize":
        assert result.returncode == 0 and rows == [
            "source",
            "step abc",
            "drain",
            "source",
            "step abc",
            "source",
            "step abc",
            "finish abc",
        ]
    else:
        assert result.returncode == 0 and rows == ["source", "closed-step abc"] * 3 + [
            "closed-step abc"
        ]
    assert not result.stdout and not result.stderr


@pytest.mark.parametrize(
    "key,value",
    [
        ("STAGING_SCRIBE_INIT_PLAN", ""),
        ("EXPECTED_STAGING_SCRIBE_INIT_PLAN_SHA256", "bad"),
        ("STAGING_SCRIBE_INIT_JOURNAL", ""),
        ("STAGING_DEPLOYMENT_IDENTITY_MODE", "service-principal"),
        ("STAGING_PRESERVE_EFFECTIVE_CONFIGURATION", "false"),
        ("STAGING_PUBLICATION_RECEIPT", ""),
        ("STAGING_SCRIBE_INIT_ACTION", "arbitrary"),
    ],
)
def test_actual_shell_input_gate(key, value):
    source = (ROOT / "infra-staging/deploy.sh").read_text()
    source = source[
        source.index("GIT_SHA=") : source.index(
            '\nif [[ "$STAGING_DEPLOYMENT_IDENTITY_MODE" == "founder-user" &&'
        )
    ]
    env = {
        "PATH": "/usr/bin:/bin",
        "STAGING_SCRIBE_INIT_PLAN": "/private/tmp/invented.json",
        "EXPECTED_STAGING_SCRIBE_INIT_PLAN_SHA256": "a" * 64,
        "STAGING_SCRIBE_INIT_JOURNAL": "/private/tmp/invented-journal.json",
        "STAGING_DEPLOYMENT_IDENTITY_MODE": "founder-user",
        "STAGING_PRESERVE_EFFECTIVE_CONFIGURATION": "true",
        "STAGING_PUBLICATION_RECEIPT": "/private/tmp/invented-receipt.json",
        "STAGING_SCRIBE_INIT_ACTION": "initialize",
    }
    result = subprocess.run(
        ["/bin/bash", "-c", source + "\nexit 0\n"],
        env=env | {key: value},
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 1 and not result.stdout and not result.stderr


@pytest.mark.parametrize(
    "key",
    [
        None,
        "SCRIBE_CANCELLATION_LEDGER_READY",
        "SCRIBE_AUTOMATIC_ACTIVATION_ENABLED",
        "SCRIBE_NATIVE_PROVIDER_CONTEXT_ENABLED",
        "SCRIBE_ACTIVATION_PRACTICE_REFS",
    ],
)
def test_raw_recovery_retains_ledger_and_closed_access(key):
    old = runpy.run_path(
        str(ROOT / "tests/infrastructure/test_staging_runtime_operations.py")
    )
    plan, publication, current, retained = old["fixtures"]()
    controls = B["sample"].__wrapped__()[1]["recovery"]["controls"]
    for name in E["PURE"]["PHASES"]:
        current[name]["template"]["containers"][0]["env"] = [
            {"name": B["LEDGER"], "value": "true"}
        ]
        selected = controls if name == B["API"] else {B["LEDGER"]: "true"}
        retained[name]["template"]["containers"][0]["env"] = [
            {"name": k, "value": v} for k, v in selected.items()
        ]
    E["RUNTIME"]["recovery_preflight"](plan, publication, current, retained)
    if key:
        row = next(
            x
            for x in retained[B["API"]]["template"]["containers"][0]["env"]
            if x["name"] == key
        )
        row["value"] = "invented unsafe change"
        with pytest.raises(ValueError):
            E["RUNTIME"]["recovery_preflight"](plan, publication, current, retained)


@pytest.mark.parametrize(
    "schema,action,status",
    [("abc", "closed", 0), ("wrong", "closed", 1), ("before", "initialize", 0)],
)
def test_actual_closed_schema_probe_branch_never_expands(
    tmp_path, schema, action, status
):
    source = (ROOT / "infra-staging/deploy.sh").read_text()
    start = source.index(
        '\tif [[ -n "$STAGING_SCRIBE_INIT_PLAN" && "$STAGING_SCRIBE_INIT_ACTION" == "closed" ]]; then'
    )
    end = source.index("\n\temit_github_output schema_before", start)
    calls = tmp_path / "schema-calls"
    script = (
        "set -euo pipefail\nSTAGING_SCRIBE_INIT_PLAN=x\nSTAGING_SCRIBE_INIT_ACTION="
        + shlex.quote(action)
    )
    script += (
        "\nSCHEMA_BEFORE="
        + shlex.quote(schema)
        + "\nEXPECTED_SCHEMA_AFTER=abc\nCALLS="
        + shlex.quote(str(calls))
    )
    script += '\nemit_github_output() { printf "receipt\\n" >> "$CALLS"; }\ncleanup_migration_job() { printf "cleanup\\n" >> "$CALLS"; }\nprobe() {\n'
    script += source[start:end] + '\nprintf "expand\\n" >> "$CALLS";\n}\nprobe\n'
    result = subprocess.run(
        ["/bin/bash", "-c", script],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == status
    rows = calls.read_text().splitlines() if calls.exists() else []
    assert rows == (
        ["receipt", "cleanup"]
        if action == "closed" and status == 0
        else ["expand"]
        if action == "initialize"
        else []
    )
