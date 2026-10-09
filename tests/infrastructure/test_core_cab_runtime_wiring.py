"""Secretless proof of Core CAB release inputs and actual workload projection."""

import copy
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = runpy.run_path(str(ROOT / "scripts/core_cab_runtime_config.py"))
DEFAULTS = MODULE["PUBLIC_DEFAULTS"]
POLICY = MODULE["POLICY_NAME"]
NAMES = set(DEFAULTS) | {POLICY}
ISSUER = "https://clerk.example.invalid"


def policy():
    return {
        "schemaVersion": "core.billing-source-policy.v1",
        "enabled": True,
        "clerkIssuer": ISSUER,
        "admissions": [
            {
                "admissionId": "synthetic-admission",
                "practiceId": "practice_1",
                "doctorId": "doctor_1",
                "appointmentId": "appointment_1",
                "patientId": "patient_1",
                "noteId": "visit_note_1",
                "contentSha256": "a" * 64,
                "dataClass": "synthetic",
                "reviewRef": "synthetic-review",
                "expiresAt": "2099-01-01T00:00:00Z",
            }
        ],
    }


def configured():
    values = {
        k: (
            "11111111-1111-1111-1111-111111111111"
            if k.endswith(("_ID", "_AUDIENCE"))
            else v
        )
        for k, v in DEFAULTS.items()
    }
    values.update(
        CORE_CAB_AUTHORIZE_ENABLED="true",
        CORE_CAB_BRIDGE_ENABLED="true",
        CORE_CAB_ORIGIN="https://cab.example.invalid",
        AUTHORITY_CLERK_ISSUER=ISSUER,
    )
    values[POLICY] = json.dumps(policy())
    return values


def test_defaults_and_explicit_two_way_configuration():
    MODULE["validate"]({})
    MODULE["validate"](dict(DEFAULTS))
    MODULE["validate"](configured())


@pytest.mark.parametrize(
    "flag", ["CORE_CAB_ASYNC_ENABLED", "CORE_CAB_CONTINUATION_ENABLED"]
)
@pytest.mark.parametrize("value", ["TRUE", "1", True, None])
def test_generation_flags_are_closed_booleans(flag, value):
    with pytest.raises(ValueError):
        MODULE["validate"](configured() | {flag: value})


@pytest.mark.parametrize(
    "async_enabled,continuation_enabled", [(False, False), (True, False), (True, True)]
)
def test_generation_flags_require_the_complete_existing_bridge(
    async_enabled, continuation_enabled
):
    values = configured() | {
        "CORE_CAB_ASYNC_ENABLED": str(async_enabled).lower(),
        "CORE_CAB_CONTINUATION_ENABLED": str(continuation_enabled).lower(),
    }
    MODULE["validate"](values)
    if async_enabled:
        for disabled in ("CORE_CAB_BRIDGE_ENABLED", "CORE_CAB_AUTHORIZE_ENABLED"):
            with pytest.raises(ValueError):
                MODULE["validate"](values | {disabled: "false"})


def test_continuation_cannot_activate_without_async():
    with pytest.raises(ValueError):
        MODULE["validate"](configured() | {"CORE_CAB_CONTINUATION_ENABLED": "true"})


@pytest.mark.parametrize(
    "name,value",
    [
        ("CORE_CAB_BRIDGE_ENABLED", "TRUE"),
        ("CORE_CAB_AUTHORIZE_ENABLED", "false"),
        ("CORE_CAB_AUTHORIZE_CALLER_ID", ""),
        ("CORE_CAB_AUDIENCE", "not-a-uuid"),
        ("CORE_CAB_MANAGED_IDENTITY_CLIENT_ID", ""),
        ("CORE_CAB_ORIGIN", "http://cab.example.invalid"),
        ("CORE_CAB_ORIGIN", "https://cab.example.invalid/path"),
        ("CORE_CAB_ORIGIN", "https://cab.example.invalid:8443"),
        ("CORE_CAB_ORIGIN", "https://user@cab.example.invalid"),
        ("AUTHORITY_CLERK_ISSUER", "https://other.example.invalid"),
    ],
)
def test_incomplete_or_cross_target_config_rejected(name, value):
    values = configured() | {name: value}
    with pytest.raises(ValueError):
        MODULE["validate"](values)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.update(enabled="true"),
        lambda p: p.update(extra="untrusted"),
        lambda p: p["admissions"][0].update(dataClass="operational"),
        lambda p: p["admissions"][0].update(expiresAt="2000-01-01T00:00:00Z"),
        lambda p: p["admissions"][0].update(expiresAt="2099-01-01"),
        lambda p: p["admissions"][0].update(contentSha256="incorrect"),
        lambda p: p["admissions"].append(copy.deepcopy(p["admissions"][0])),
        lambda p: p["admissions"][0].update(patientId="patient with spaces"),
    ],
)
def test_policy_remains_closed_exact_and_non_phi(mutation):
    source = policy()
    mutation(source)
    with pytest.raises(ValueError):
        MODULE["validate"](configured() | {POLICY: json.dumps(source)})


def test_failures_do_not_print_policy_and_duplicate_keys_reject():
    for raw in [
        '{"enabled":false,"enabled":true}',
        '{"sensitive-marker":"do-not-echo"}',
        "x" * 131073,
    ]:
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                str(ROOT / "scripts/core_cab_runtime_config.py"),
                "--stdin",
            ],
            input=json.dumps({POLICY: raw}),
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr == "Core CAB runtime configuration is invalid.\n"


@pytest.mark.parametrize("environment", ["production", "staging"])
@pytest.mark.parametrize("selected", [False, True])
def test_template_resolves_only_explicit_environment_local_policy(
    tmp_path, environment, selected
):
    source = (
        (ROOT / f".env.{environment}.backend")
        .read_text()
        .split("# Core CAB runtime:")[1]
    )
    source = "# Core CAB runtime:" + source
    if selected:
        source = source.replace(
            "export CORE_CAB_SOURCE_POLICY_ITEM_ID=\n",
            "export CORE_CAB_SOURCE_POLICY_ITEM_ID=reviewed-synthetic\n",
        )
    calls = tmp_path / "calls"
    script = (
        "set -e\n"
        + """
op() { printf '%s\n' "$*" >> "$CALLS"; printf '%s' "$FAKE_POLICY"; }
"""
        + source
        + "\n"
        + sys.executable
        + ' -c \'import json,os; print(json.dumps({k:v for k,v in os.environ.items() if k.startswith("CORE_CAB_") or k=="BILLING_SOURCE_POLICY_JSON"}))\''
    )
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        env={
            "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
            **configured(),
            "CALLS": str(calls),
            "FAKE_POLICY": json.dumps(policy()),
            "OP_PROD_VAULT_UID": "synthetic-prod",
            "OP_STAGING_VAULT_UID": "synthetic-staging",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert {k: observed[k] for k in DEFAULTS} == DEFAULTS
    assert observed[POLICY] == (json.dumps(policy()) if selected else "")
    if selected:
        vault = "synthetic-prod" if environment == "production" else "synthetic-staging"
        assert (
            calls.read_text() == f"read op://{vault}/reviewed-synthetic/policy-json\n"
        )
    else:
        assert not calls.exists()


def contract_script():
    source = (ROOT / "infra-staging/deploy.sh").read_text()
    return source[
        source.index("COMMON_SECRET_VARS=(") : source.index(
            "verify_effective_configuration_fingerprint()"
        )
    ]


def shell(functions, tail, *, configuration=None):
    env = {
        "PATH": os.environ["PATH"],
        "ENV_JSON": json.dumps(configuration or {}),
        "API_CONTAINER": "cosmaneura-staging-app",
        "WORKER_CONTAINER": "cosmaneura-staging-worker",
        "SCHEDULER_CONTAINER": "cosmaneura-staging-scheduler",
        "RESOURCE_GROUP": "synthetic-only",
        "CRITICAL_CONTAINERS": "cosmaneura-staging-app cosmaneura-staging-worker cosmaneura-staging-scheduler",
        "STAGING_API_COMMAND": "api",
        "STAGING_WORKER_COMMAND": "worker",
        "SCHEDULER_COMMAND": "scheduler",
        "ENABLE_SOVEREIGN_RECORD_GRANTS": "false",
        "STRIPE_INTEGRATION_ENABLED": "false",
    }
    return subprocess.run(
        ["bash", "-c", "set -e\n" + functions + "\n" + tail],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    ).stdout


def test_actual_staging_projection_and_fingerprint_are_api_only():
    contracts = json.loads(
        shell(contract_script(), "runtime_contracts_json", configuration=configured())
    )
    runtime = runpy.run_path(str(ROOT / "scripts/staging_runtime_configuration.py"))
    for contract in contracts:
        result = runtime["desired"](configured(), contract)["environment"]
        if contract["name"].endswith("-app"):
            assert NAMES <= result.keys()
            assert (
                POLICY in contract["secret_names"]
                and POLICY not in contract["public_names"]
            )
        else:
            assert not NAMES & result.keys()
    payload = {"configuration": configured(), "contracts": contracts}
    before = runtime["fingerprint"](payload)
    payload["configuration"][POLICY] = json.dumps(policy() | {"enabled": False})
    assert runtime["fingerprint"](payload) != before
    payload["configuration"][POLICY] = ""
    assert (
        POLICY
        not in runtime["desired"](payload["configuration"], contracts[0])["environment"]
    )


def test_generation_flags_change_only_the_api_effective_fingerprint():
    contracts = json.loads(
        shell(contract_script(), "runtime_contracts_json", configuration=configured())
    )
    runtime = runpy.run_path(str(ROOT / "scripts/staging_runtime_configuration.py"))
    before = runtime["fingerprint"](
        {"configuration": configured(), "contracts": contracts}
    )
    for flag in ("CORE_CAB_ASYNC_ENABLED", "CORE_CAB_CONTINUATION_ENABLED"):
        changed = configured() | {flag: "true"}
        assert (
            runtime["fingerprint"]({"configuration": changed, "contracts": contracts})
            != before
        )
        for contract in contracts:
            projected = runtime["desired"](changed, contract)["environment"]
            if contract["name"].endswith("-app"):
                assert projected[flag] == "true"
            else:
                assert flag not in contract["public_names"] and flag not in projected


@pytest.mark.parametrize(
    "api,has_policy,removed",
    [
        (True, True, False),
        (True, False, True),
        (False, True, True),
        (False, False, True),
    ],
)
def test_actual_staging_policy_secret_cleanup(api, has_policy, removed):
    source = (ROOT / "infra-staging/deploy.sh").read_text()
    function = source[
        source.index("remove_unowned_cab_policy_secret()") : source.index(
            "remove_unowned_billing_secrets()"
        )
    ]
    fake = 'az() { if [[ "$2" == "show" ]]; then echo 1; else printf "removed:%s\\n" "$*"; fi; }\n'
    target = "cosmaneura-staging-app" if api else "cosmaneura-staging-worker"
    result = shell(
        fake + function,
        "remove_unowned_cab_policy_secret " + target,
        configuration={POLICY: "{}" if has_policy else ""},
    )
    assert ("--secret-names billing-source-policy-json" in result) is removed


def test_release_paths_preserve_gates_and_keep_policy_out_of_migration_scope():
    source = (ROOT / "infra-staging/deploy.sh").read_text()
    assert source.index("core_cab_runtime_config.py") < source.index(
        "if ! RESOLVED_GIT_SHA="
    )
    migrate = source[
        source.index("run_migrations()") : source.index("initiate_deployment()")
    ]
    assert not any(name in migrate for name in NAMES)
    expected = (
        'remove_env_vars+=("$'
        + '{CAB_API_ONLY_SECRET_VARS[@]}" "$'
        + '{CAB_API_ONLY_PUBLIC_VARS[@]}")'
    )
    assert expected in source
    assert source.index('remove_unowned_cab_policy_secret "$container"') > source.index(
        'check_deployment "$API_CONTAINER"'
    )
    main = (ROOT / "infra/main.tf").read_text()
    api = main[
        main.index('resource "azurerm_container_app" "app"') : main.index(
            'resource "azurerm_container_app" "scheduler"'
        )
    ]
    rest = main[main.index('resource "azurerm_container_app" "scheduler"') :]
    assert (
        "local.core_cab_public_values" in api
        and api.count("local.core_cab_secret_values") == 2
    )
    assert "local.core_cab_" not in rest
    assert "CORE_CAB_" not in (ROOT / "just/direnv.just").read_text()


def test_preserved_configuration_never_deletes_policy_secret():
    source = (ROOT / "infra-staging/deploy.sh").read_text()
    function = source[
        source.index("remove_unowned_cab_policy_secret()") : source.index(
            "remove_unowned_billing_secrets()"
        )
    ]
    # Any provider call is a failure, including a read used to decide deletion.
    assert (
        shell(
            "az() { echo unexpected-provider-call; return 99; }\n" + function,
            "STAGING_PRESERVE_EFFECTIVE_CONFIGURATION=true\n"
            "remove_unowned_cab_policy_secret cosmaneura-staging-app\n"
            "remove_unowned_cab_policy_secret cosmaneura-staging-worker",
        )
        == ""
    )


def test_production_template_runs_complete_offline_validation():
    source = (
        "# Core CAB runtime:"
        + (ROOT / ".env.production.backend").read_text().split("# Core CAB runtime:")[1]
    )
    for name, value in configured().items():
        if name in DEFAULTS:
            line = next(
                line
                for line in source.splitlines()
                if line.startswith("export " + name + "=")
            )
            source = source.replace(line, "export " + name + "=" + value)
    source = source.replace(
        "https://cab.example.invalid", "https://cab.example.invalid:8443"
    )
    result = subprocess.run(
        [
            "bash",
            "-c",
            "set -e\nop() { exit 99; }\nrun_template() {\n"
            + source
            + "\n}\nrun_template",
        ],
        cwd=ROOT,
        env={
            "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == "Core CAB runtime configuration is invalid.\n"
    production = (ROOT / "infra/variables.tf").read_text()
    assert '"direnv_export", ".env.production.backend"' in production
