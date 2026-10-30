"""Synthetic-only runtime preservation checks; no app imports or provider calls."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/staging_runtime_configuration.py"
DEPLOY = ROOT / "infra-staging/deploy.sh"
API = "cosmaneura-staging-app"
WORKER = "cosmaneura-staging-worker"
SCHEDULER = "cosmaneura-staging-scheduler"
APPS = (WORKER, SCHEDULER, API)
SENSITIVE_KEYS = (
    "ENCRYPTION_KEY",
    "SESSION_SECRET_KEY",
    "DATABASE_URL",
    "SOVEREIGN_RECORD_GRANT_SECRET",
)
PRESERVED_FLAG_SCOPES = {
    "SCRIBE_COMBINED_SESSION_PREPARATION_ENABLED": {API},
    "SCRIBE_NATIVE_REQUEST_TIMING": {API},
    "SCRIBE_WORKFLOW_IDENTITY_REUSE_ENABLED": {API},
    "ZOOM_DURABLE_CAPTURE_BINDINGS_ENABLED": {API, WORKER},
    "SESSION_PREP_HEALTH_ALERTS_ENABLED": {WORKER},
}
REFUSAL = "staging_runtime_configuration_refused\n"


@pytest.mark.parametrize(
    ("mode", "fingerprint", "accepted"),
    [
        ("false", "", True),
        ("true", "sha256:" + "a" * 64, True),
        ("true", "", False),
        ("false", "sha256:" + "a" * 64, False),
        ("invalid", "", False),
    ],
)
def test_preservation_inputs_are_an_explicit_complete_pair(mode, fingerprint, accepted):
    source = DEPLOY.read_text()
    start = source.index('case "$STAGING_PRESERVE_EFFECTIVE_CONFIGURATION" in')
    guard = source[start : source.index("\nesac", start) + len("\nesac")]
    result = subprocess.run(
        ["bash", "-c", guard],
        env={
            **_environment(),
            "STAGING_PRESERVE_EFFECTIVE_CONFIGURATION": mode,
            "EXPECTED_STAGING_EFFECTIVE_CONFIGURATION_SHA256": fingerprint,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) == accepted


def test_resolver_runs_once_and_does_not_emit_arbitrary_secret_errors(tmp_path):
    source = DEPLOY.read_text()
    start = source.index("CONFIGURATION_STATUS=0\n")
    block = source[start : source.index("if ! printf '%s'", start)]
    calls = tmp_path / "resolver-calls"
    program = f"""
REPOSITORY_ROOT={shlex.quote(str(ROOT))}
just() {{
    printf 'called\\n' >> {shlex.quote(str(calls))}
    printf 'export AZURE_OPENAI_API_KEY=synthetic-new-secret-never-log\\n' >&2
    return 1
}}
{block}
"""
    result = subprocess.run(
        ["bash", "-c", program],
        env=_environment(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert calls.read_text() == "called\n"
    assert result.stdout == "Failed to load staging deployment configuration.\n"
    assert result.stderr == ""
    assert "synthetic-new-secret" not in result.stdout


def _function(source: str, name: str) -> str:
    start = source.index(f"\n{name}() {{") + 1
    return source[start : source.index("\n}", start) + 2]


def _contract_shell() -> str:
    source = DEPLOY.read_text()
    # Execute the deployer's actual arrays and contract builder, so adding a
    # new owned key automatically makes it part of these regression fixtures.
    start = source.index("\nCOMMON_SECRET_VARS=(") + 1
    end = source.index("\nverify_effective_configuration_fingerprint()", start)
    return f"""
API_CONTAINER={API}
WORKER_CONTAINER={WORKER}
SCHEDULER_CONTAINER={SCHEDULER}
CRITICAL_CONTAINERS={shlex.quote(" ".join(APPS))}
API_WORKLOAD_CONTAINER_NAME=cosmaneura-staging-app-web
WORKER_WORKLOAD_CONTAINER_NAME=cosmaneura-staging-app-worker
SCHEDULER_WORKLOAD_CONTAINER_NAME=cosmaneura-staging-app-scheduler
ENABLE_SOVEREIGN_RECORD_GRANTS=true
STRIPE_INTEGRATION_ENABLED=true
STAGING_API_COMMAND=synthetic-api-command
STAGING_WORKER_COMMAND=synthetic-worker-command
SCHEDULER_COMMAND=synthetic-scheduler-command
{source[start:end]}
"""


def _environment() -> dict[str, str]:
    # Do not inherit credentials, application configuration, or provider hooks.
    return {"PATH": os.environ.get("PATH", os.defpath), "LC_ALL": "C"}


def _configuration_evidence_fixture(integration, billing, key_kind):
    flags = (
        "SCRIBE_HANDOFF_ENABLED SCRIBE_WORKFLOW_ENABLED SESSION_PREP_SYNTHETIC_ENABLED "
        "AUTO_MIGRATE_ON_STARTUP STRIPE_INTEGRATION_ENABLED STRIPE_BILLING_ENABLED "
        "STRIPE_LIVE_MODE DURABLE_WEBHOOK_INBOX_ENABLED ZOOM_WATCHDOG_ENABLED "
        "AGENT_API_ENABLED AUTHORITY_KERNEL_ENABLED SENTRY_STAGING_ENABLED"
    ).split()
    configuration = {name: "false" for name in flags}
    configuration.update(
        PYTHON_ENV="staging",
        API_BASE_URL="https://staging-app.cosmaneura.com",
        ZOOM_REDIRECT_URI="https://staging-app.cosmaneura.com/external/v1/zoom-oauth-callback",
        ZOOM_SUCCESS_REDIRECT="https://staging-app.cosmaneura.com/settings",
        COOKIE_DOMAIN="staging-app.cosmaneura.com",
        RECALL_API_BASE="https://us-west-2.recall.ai/api/v1",
        ALLOWED_HOST_LIST="staging-app.cosmaneura.com,staging-chat.cosmaneura.com",
        INTAKE_CHAT_DOMAIN="staging-chat.cosmaneura.com",
        AUTHORITY_CLERK_ISSUER="https://resolved-emu-53.clerk.accounts.dev",
        DATABASE_URL=(
            "postgresql://synthetic:synthetic@"
            "cosmaneura-staging-postgres.postgres.database.azure.com:5432/"
            "staging?sslmode=verify-full"
        ),
        CLERK_DEPLOYMENT_PUBLISHABLE_KEY="pk_test_synthetic",
        CLERK_PRIVATE_KEY="sk_test_synthetic",
        ZOOM_CLIENT_ID="synthetic-zoom-client",
        ZOOM_CLIENT_SECRET="synthetic-zoom-secret",
        ZOOM_SECRET_TOKEN="synthetic-zoom-token",
        RECALL_API_KEY="synthetic-recall-api",
        RECALL_WEBHOOK_SECRET="synthetic-recall-webhook",
        SCRIBE_WORKFLOW_ENABLED="true",
        AGENT_API_ENABLED="true",
        AUTHORITY_KERNEL_ENABLED="true",
        STRIPE_INTEGRATION_ENABLED=integration,
        STRIPE_BILLING_ENABLED=billing,
        STRIPE_ACCOUNT_ID="acct_synthetic" if integration == "true" else key_kind,
        STRIPE_SECRET_KEY=key_kind + "synthetic" if integration == "true" else key_kind,
        STRIPE_WEBHOOK_SECRET="whsec_synthetic" if integration == "true" else key_kind,
    )
    fields = dict(
        pair.split(":")
        for pair in (
            "clerk_publishable:CLERK_DEPLOYMENT_PUBLISHABLE_KEY "
            "clerk_private:CLERK_PRIVATE_KEY zoom_client:ZOOM_CLIENT_ID "
            "zoom_secret:ZOOM_CLIENT_SECRET zoom_token:ZOOM_SECRET_TOKEN "
            "recall_api:RECALL_API_KEY recall_webhook:RECALL_WEBHOOK_SECRET "
            "stripe_account:STRIPE_ACCOUNT_ID stripe_key:STRIPE_SECRET_KEY "
            "stripe_webhook:STRIPE_WEBHOOK_SECRET"
        ).split()
    )
    fingerprints = {
        label + "_sha256": hashlib.sha256(configuration[name].encode()).hexdigest()
        if configuration[name]
        else None
        for label, name in fields.items()
    }
    expected = "sha256:" + hashlib.sha256(
        json.dumps(fingerprints, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return configuration, expected


def _configuration_evidence_result(configuration, fingerprint, preservation, release=""):
    source = DEPLOY.read_text()
    start = source.index("\nbuild_configuration_evidence() {") + 1
    # The embedded Python contains its own unindented closing braces.
    end = source.index("\n}\n\nif ! CONFIGURATION_EVIDENCE_JSON", start) + 2
    actual_function = source[start:end]
    return subprocess.run(
        ["bash", "-c", actual_function + "\nbuild_configuration_evidence"],
        capture_output=True,
        text=True,
        check=False,
        env={
            **_environment(),
            "PYTHON_BIN": sys.executable,
            "ENV_JSON": json.dumps(configuration),
            "EXPECTED_STAGING_IDENTITY_FINGERPRINTS_SHA256": fingerprint,
            "STAGING_PRESERVE_EFFECTIVE_CONFIGURATION": preservation,
            "RELEASE_V2_JSON": release,
        },
    )


@pytest.mark.parametrize(
    ("integration", "billing", "key_kind"),
    [
        ("false", "false", None),
        ("false", "false", ""),
        ("true", "false", "sk_test_"),
        ("true", "false", "rk_test_"),
        ("true", "true", "sk_test_"),
        ("true", "true", "rk_test_"),
    ],
)
def test_full_preservation_retains_enabled_scribe_and_existing_test_billing(
    integration, billing, key_kind
):
    configuration, fingerprint = _configuration_evidence_fixture(
        integration, billing, key_kind
    )
    result = _configuration_evidence_result(configuration, fingerprint, "true")
    assert result.returncode == 0
    evidence = json.loads(result.stdout)
    assert evidence["flags"]["SCRIBE_WORKFLOW_ENABLED"] == "true"
    assert evidence["flags"]["STRIPE_INTEGRATION_ENABLED"] == integration
    assert evidence["flags"]["STRIPE_BILLING_ENABLED"] == billing
    assert evidence["identity_fingerprint_set_sha256"] == fingerprint
    assert evidence["deployment_blockers"] == []
    assert "synthetic-recall-api" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("preservation", "release", "scribe", "accepted"),
    [
        ("false", "", "false", True),
        ("false", "", "true", False),
        ("true", "", "false", False),
        ("false", "v2", "true", True),
        ("false", "v2", "false", False),
    ],
)
def test_configuration_guard_retains_legacy_and_v2_scribe_boundaries(
    preservation, release, scribe, accepted
):
    configuration, fingerprint = _configuration_evidence_fixture(
        "true", "false", "sk_test_"
    )
    configuration["SCRIBE_WORKFLOW_ENABLED"] = scribe
    result = _configuration_evidence_result(
        configuration, fingerprint, preservation, release
    )
    assert (result.returncode == 0) == accepted
    if not accepted:
        assert result.stderr == "Staging configuration evidence validation failed.\n"


def _cli(mode: str, payload: dict | str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(SCRIPT), mode],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        env=_environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


@pytest.fixture(scope="module")
def contracts() -> list[dict]:
    result = subprocess.run(
        ["bash", "-c", _contract_shell() + "\nruntime_contracts_json\n"],
        env=_environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture
def configuration(contracts: list[dict]) -> dict[str, str]:
    result = {}
    for contract in contracts:
        for name in contract["secret_names"]:
            result[name] = f"synthetic-never-log-{name.lower()}"
        for name in contract["public_names"]:
            result[name] = f"synthetic-public-{name.lower()}"
    result["DATABASE_URL"] = (
        "postgresql://synthetic:synthetic-never-log-password@database.invalid/synthetic"
    )
    return result


def _fingerprint(configuration: dict, contracts: list[dict]) -> str:
    result = _cli(
        "--fingerprint", {"configuration": configuration, "contracts": contracts}
    )
    assert result.returncode == 0, result.stderr
    assert re.fullmatch(r"sha256:[0-9a-f]{64}\n", result.stdout)
    return result.stdout.strip()


def _workload_payload(configuration: dict, contract: dict) -> dict:
    name = contract["name"]
    revision = name + "--synthetic-before"
    refs = {
        key: key.lower().replace("_", "-")
        for key in contract["secret_names"]
        if configuration.get(key)
    }
    state = {
        "name": name,
        "latestRevision": revision,
        "latestReadyRevision": revision,
        "readyRevisionCommand": [],
        "readyRevisionArgs": contract["args"],
        "publicConfig": {
            key: configuration[key]
            for key in contract["public_names"]
            if configuration.get(key)
        },
        "secretRefs": refs,
        "legacyPublicEnvNames": [],
    }
    return {
        "configuration": copy.deepcopy(configuration),
        "contract": copy.deepcopy(contract),
        "state": state,
        "expected_revision": revision,
        "secrets": [
            {"name": reference, "value": configuration[key]}
            for key, reference in refs.items()
        ],
    }


def _no_secret_output(result: subprocess.CompletedProcess[str], configuration: dict):
    output = result.stdout + result.stderr
    assert "synthetic-never-log" not in output
    for name in SENSITIVE_KEYS:
        assert configuration[name] not in output


def test_contract_inventory_covers_all_workloads_and_sensitive_keys(contracts):
    by_name = {item["name"]: item for item in contracts}
    assert set(by_name) == set(APPS)
    for name, contract in by_name.items():
        assert set(SENSITIVE_KEYS[:3]) <= set(contract["secret_names"])
        assert ("SOVEREIGN_RECORD_GRANT_SECRET" in contract["secret_names"]) == (
            name == API
        )
    assert "STRIPE_SECRET_KEY" in by_name[WORKER]["secret_names"]
    assert "STRIPE_SECRET_KEY" not in by_name[SCHEDULER]["secret_names"]
    for flag in ("SCRIBE_ENCOUNTER_CREATION_ENABLED", "COMPANION_SESSION_PREP_ENABLED"):
        assert flag in by_name[API]["public_names"]
        assert flag not in by_name[WORKER]["public_names"]
        assert flag not in by_name[SCHEDULER]["public_names"]


def test_actual_deployer_preserves_current_flags_only_on_their_existing_workloads(
    contracts,
):
    for flag, owners in PRESERVED_FLAG_SCOPES.items():
        actual = {
            contract["name"]
            for contract in contracts
            if flag in contract["public_names"]
        }
        assert actual == owners


@pytest.mark.parametrize("flag", sorted(PRESERVED_FLAG_SCOPES))
def test_preserved_flag_drift_changes_fingerprint_and_refuses_live_state(
    configuration, contracts, flag
):
    configuration.update(dict.fromkeys(PRESERVED_FLAG_SCOPES, "true"))
    baseline = _fingerprint(configuration, contracts)
    changed = dict(configuration, **{flag: "false"})
    assert _fingerprint(changed, contracts) != baseline
    for contract in contracts:
        payload = _workload_payload(configuration, contract)
        if contract["name"] in PRESERVED_FLAG_SCOPES[flag]:
            assert payload["state"]["publicConfig"][flag] == "true"
            result = _cli("--check-workload", payload)
            assert result.returncode == 0, result.stderr
            payload["state"]["publicConfig"][flag] = "false"
        else:
            assert flag not in payload["state"]["publicConfig"]
            payload["state"]["publicConfig"][flag] = "true"
        result = _cli("--check-workload", payload)
        assert result.returncode != 0
        assert result.stderr == REFUSAL
        _no_secret_output(result, configuration)


@pytest.mark.parametrize("key", SENSITIVE_KEYS)
def test_full_fingerprint_changes_when_effective_configuration_changes(
    configuration, contracts, key
):
    baseline = _fingerprint(configuration, contracts)
    changed = dict(configuration, **{key: configuration[key] + "-changed"})
    assert _fingerprint(changed, contracts) != baseline


@pytest.mark.parametrize("workload", APPS)
def test_full_fingerprint_includes_each_workloads_command(
    configuration, contracts, workload
):
    changed = copy.deepcopy(contracts)
    next(item for item in changed if item["name"] == workload)["args"] = [
        "synthetic-changed-command"
    ]
    assert _fingerprint(configuration, changed) != _fingerprint(
        configuration, contracts
    )


@pytest.mark.parametrize("workload", APPS)
def test_missing_or_duplicate_workload_cannot_produce_a_fingerprint(
    configuration, contracts, workload
):
    remaining = [item for item in contracts if item["name"] != workload]
    for changed in (remaining, [*remaining, remaining[0]]):
        result = _cli(
            "--fingerprint", {"configuration": configuration, "contracts": changed}
        )
        assert (result.returncode, result.stdout, result.stderr) == (1, "", REFUSAL)


def test_full_fingerprint_is_order_independent_and_excludes_unowned_keys(
    configuration, contracts
):
    reordered = dict(reversed(list(configuration.items())))
    reordered["UNOWNED_SYNTHETIC_INPUT"] = "ignored-input"
    assert _fingerprint(reordered, list(reversed(contracts))) == _fingerprint(
        configuration, contracts
    )


@pytest.mark.parametrize("workload", APPS)
def test_exact_workload_values_produce_only_value_free_evidence(
    configuration, contracts, workload
):
    contract = next(item for item in contracts if item["name"] == workload)
    result = _cli("--check-workload", _workload_payload(configuration, contract))
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence.keys() == {"name", "preserved", "runtime_sha256"}
    assert evidence["name"] == workload and evidence["preserved"] is True
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", evidence["runtime_sha256"])
    _no_secret_output(result, configuration)


@pytest.mark.parametrize(
    "failure",
    (
        "missing-secret",
        "empty-secret",
        "unresolved-secret",
        "duplicate-secret",
        "missing-reference",
        "empty-reference",
        "wrong-reference",
        "removed-input",
        "empty-input",
        "changed-secret",
        "changed-public",
        "wrong-revision",
        "wrong-command",
    ),
)
def test_missing_ambiguous_removed_or_changed_configuration_refuses_generically(
    configuration, contracts, failure
):
    payload = _workload_payload(configuration, contracts[0])
    secret = next(
        item for item in payload["secrets"] if item["name"] == "encryption-key"
    )
    if failure == "missing-secret":
        payload["secrets"].remove(secret)
    elif failure == "empty-secret":
        secret["value"] = ""
    elif failure == "unresolved-secret":
        secret.pop("value")
        secret["keyVaultUrl"] = "https://synthetic.invalid/never-resolved"
    elif failure == "duplicate-secret":
        payload["secrets"].append(dict(secret))
    elif failure == "missing-reference":
        payload["state"]["secretRefs"].pop("ENCRYPTION_KEY")
    elif failure == "empty-reference":
        payload["state"]["secretRefs"]["ENCRYPTION_KEY"] = ""
    elif failure == "wrong-reference":
        payload["state"]["secretRefs"]["ENCRYPTION_KEY"] = "unresolved-reference"
    elif failure == "removed-input":
        payload["configuration"].pop("ENCRYPTION_KEY")
    elif failure == "empty-input":
        payload["configuration"]["ENCRYPTION_KEY"] = ""
    elif failure == "changed-secret":
        secret["value"] += "-changed"
    elif failure == "changed-public":
        payload["state"]["publicConfig"]["SESSION_PREP_PROVIDER_ENABLED"] += "-changed"
    elif failure == "wrong-revision":
        payload["expected_revision"] += "-changed"
    elif failure == "wrong-command":
        payload["state"]["readyRevisionCommand"] = ["synthetic-other-command"]
    result = _cli("--check-workload", payload)
    assert (result.returncode, result.stdout, result.stderr) == (1, "", REFUSAL)
    _no_secret_output(result, configuration)


def test_duplicate_json_keys_are_refused_without_emitting_values(
    configuration, contracts
):
    payload = json.dumps({"configuration": configuration, "contracts": contracts})
    payload = payload.replace(
        '"configuration": {',
        '"configuration": {"ENCRYPTION_KEY":"synthetic-never-log-duplicate",',
        1,
    )
    result = _cli("--fingerprint", payload)
    assert (result.returncode, result.stdout, result.stderr) == (1, "", REFUSAL)
    _no_secret_output(result, configuration)


def _azure_fixture(configuration: dict, contracts: list[dict]) -> tuple[dict, list]:
    fixtures, states = {}, []
    for contract in contracts:
        payload = _workload_payload(configuration, contract)
        state = payload["state"]
        name = state["name"]
        workload = {
            key: state[key] for key in ("name", "latestRevision", "latestReadyRevision")
        }
        workload.update(
            activeRevisionsMode="Single",
            traffic=[{"latestRevision": True, "weight": 100}] if name == API else [],
        )
        environment = [
            {"name": key, "secretRef": value, "value": None}
            for key, value in state["secretRefs"].items()
        ] + [
            {"name": key, "value": value, "secretRef": None}
            for key, value in state["publicConfig"].items()
        ]
        role = "web" if name == API else name.removeprefix("cosmaneura-staging-")
        fixtures[name] = {
            "workload": workload,
            "revision": {
                "containers": [
                    {
                        "name": "cosmaneura-staging-app-" + role,
                        "image": "registry.invalid/synthetic@sha256:" + "a" * 64,
                        "command": [],
                        "args": contract["args"],
                        "env": environment,
                    }
                ],
                "health": "Healthy",
                "running": "Running",
                "provisioning": "Provisioned",
            },
            "secrets": payload["secrets"],
        }
        states.append(state)
    return fixtures, states


FAKE_AZURE = r"""
import json
import sys
from pathlib import Path

fixture_path, log_path, *args = sys.argv[1:]
with Path(log_path).open("a") as log:
    log.write(json.dumps(args) + "\n")
fixtures = json.loads(Path(fixture_path).read_text())
name = args[args.index("-n") + 1] if "-n" in args else None
if args[:2] == ["containerapp", "show"]:
    result = fixtures[name]["workload"]
elif args[:3] == ["containerapp", "revision", "show"]:
    result = fixtures[name]["revision"]
elif args[:3] == ["containerapp", "secret", "list"]:
    if fixtures[name].get("secret_read_failure"):
        raise SystemExit("synthetic_azure_read_refused")
    result = fixtures[name]["secrets"]
elif args[:2] == ["containerapp", "update"] or args[:3] in (
    ["containerapp", "secret", "set"], ["containerapp", "secret", "remove"],
    ["containerapp", "job", "create"],
):
    result = {}
else:
    raise SystemExit("unexpected_synthetic_azure_call")
print(json.dumps(result))
"""


def _shell_harness(
    tmp_path,
    configuration,
    contracts,
    *,
    fixture=None,
    action="deploy",
    fingerprint=None,
):
    source = DEPLOY.read_text()
    baseline_fixture, states = _azure_fixture(configuration, contracts)
    fixture_path, log_path = tmp_path / "azure.json", tmp_path / "azure-calls.jsonl"
    fixture_path.write_text(
        json.dumps(baseline_fixture if fixture is None else fixture)
    )
    fake_path = tmp_path / "fake_azure.py"
    fake_path.write_text(FAKE_AZURE)
    if fingerprint is None:
        fingerprint = _fingerprint(configuration, contracts)
    names = (
        "verify_effective_configuration_fingerprint",
        "collect_workload_state",
        "verify_workload_state_ready",
        "verify_preserved_runtime",
        "initiate_deployment",
        "remove_unowned_billing_secrets",
        "remove_disabled_record_grant_secret",
        "remove_disallowed_recall_secret",
    )
    script = (
        _contract_shell() + "\n" + "\n".join(_function(source, name) for name in names)
    )
    bindings = {
        "PYTHON_BIN": sys.executable,
        "REPOSITORY_ROOT": str(ROOT),
        "ENV_JSON": json.dumps(configuration),
        "RUNTIME_CONTRACTS_JSON": json.dumps(contracts),
        "WORKLOADS_BEFORE_JSON": json.dumps(states),
        "EXPECTED_STAGING_EFFECTIVE_CONFIGURATION_SHA256": fingerprint,
        "STAGING_PRESERVE_EFFECTIVE_CONFIGURATION": "true",
        "RESOURCE_GROUP": "synthetic-group",
        "STAGING_IMAGE_REFERENCE": "registry.invalid/synthetic@sha256:" + "b" * 64,
        "REVISION_STRING": "synthetic-next",
        "GIT_SHA": "a" * 40,
        "GRANT_SECRET_ENV_NAME": "SOVEREIGN_RECORD_GRANT_SECRET",
        "GRANT_SECRET_RESOURCE_NAME": "sovereign-record-grant-secret",
        "RECALL_SECRET_ENV_NAME": "RECALL_WEBHOOK_SECRET",
        "RECALL_SECRET_RESOURCE_NAME": "recall-webhook-secret",
    }
    script += "\n" + "\n".join(
        f"{key}={shlex.quote(value)}" for key, value in bindings.items()
    )
    script += f"""
az() {{
    "$PYTHON_BIN" -I -S -B {shlex.quote(str(fake_path))} {shlex.quote(str(fixture_path))} {shlex.quote(str(log_path))} "$@"
}}
verify_founder_release_source() {{ return 0; }}
verify_azure_deployment_identity() {{ return 0; }}
run_migrations() {{ az containerapp job create --name synthetic-migration; }}
"""
    if action == "deploy":
        # Execute the real top-level gate through the migration call. An omitted
        # or weakened guard must become a recorded mutation in this harness.
        start = source.index("# Run migrations before deploying new containers")
        end = source.index("# Deploy and verify recovery workloads", start)
        script += source[start:end]
        script += "\nfor container in $CRITICAL_CONTAINERS; do\n"
        script += 'initiate_deployment "$container" "$GIT_SHA" || exit $?\ndone\n'
    elif action == "initiate":
        script += '\ninitiate_deployment "$WORKER_CONTAINER" "$GIT_SHA" || exit $?\n'
    elif action == "cleanup":
        script += (
            "\nENABLE_SOVEREIGN_RECORD_GRANTS=false\nSTRIPE_INTEGRATION_ENABLED=false\n"
        )
    script += "\nfor container in $CRITICAL_CONTAINERS; do\n"
    for name in names[-3:]:
        script += f'{name} "$container" || exit $?\n'
    script += "done\n"
    result = subprocess.run(
        ["bash", "-c", script],
        env=_environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=40,
    )
    calls = (
        [json.loads(line) for line in log_path.read_text().splitlines()]
        if log_path.exists()
        else []
    )
    return result, calls


def _mutations(calls: list[list[str]]) -> list[list[str]]:
    return [
        call
        for call in calls
        if call[:2] == ["containerapp", "update"]
        or call[:3]
        in (
            ["containerapp", "secret", "set"],
            ["containerapp", "secret", "remove"],
            ["containerapp", "job", "create"],
        )
    ]


@pytest.mark.parametrize("key", SENSITIVE_KEYS)
def test_reviewed_fingerprint_mismatch_stops_before_any_azure_call(
    tmp_path, configuration, contracts, key
):
    reviewed = _fingerprint(configuration, contracts)
    configuration[key] += "-changed-after-review"
    result, calls = _shell_harness(
        tmp_path, configuration, contracts, fingerprint=reviewed
    )
    assert result.returncode != 0
    assert "reviewed full runtime fingerprint" in result.stderr
    assert calls == []
    _no_secret_output(result, configuration)


@pytest.mark.parametrize(
    "key", ["EMAIL_FROM_ADDRESS", "ENCRYPTION_KEY", "DATABASE_URL"]
)
@pytest.mark.parametrize("unpreservable", ["\n", "\x00"])
def test_shell_unrepresentable_values_refuse_before_any_mutation(
    tmp_path, configuration, contracts, key, unpreservable
):
    configuration[key] += unpreservable
    # The reviewed baseline is captured from Azure's actual values. Before
    # this guard, the helper accepted that fingerprint but Bash stripped these
    # bytes when extracting values for migration/app arguments.
    current = {}
    for contract in contracts:
        state = _workload_payload(configuration, contract)["state"]
        current[contract["name"]] = {
            "environment": {
                name: configuration[name]
                for name in contract["secret_names"] + contract["public_names"]
                if configuration.get(name)
            },
            "args": state["readyRevisionArgs"],
            "command": state["readyRevisionCommand"],
        }
    reviewed = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(current, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    result, calls = _shell_harness(
        tmp_path, configuration, contracts, fingerprint=reviewed
    )
    assert result.returncode != 0
    assert calls == []
    assert REFUSAL.strip() in result.stderr
    _no_secret_output(result, configuration)


@pytest.mark.parametrize("workload", APPS)
def test_workload_only_secret_drift_blocks_migration_and_updates(
    tmp_path, configuration, contracts, workload
):
    fixture, _ = _azure_fixture(configuration, contracts)
    next(
        item
        for item in fixture[workload]["secrets"]
        if item["name"] == "encryption-key"
    )["value"] += "-drift"
    result, calls = _shell_harness(tmp_path, configuration, contracts, fixture=fixture)
    assert result.returncode != 0
    assert f"Staging runtime preservation failed for {workload}" in result.stderr
    assert _mutations(calls) == []
    _no_secret_output(result, configuration)


@pytest.mark.parametrize(
    "failure",
    (
        "duplicate-env",
        "missing-env",
        "missing-secret",
        "empty-secret",
        "unresolved-secret",
        "azure-read-failure",
        "removed-input",
        "empty-input",
    ),
)
def test_real_collection_and_secret_resolution_fail_closed(
    tmp_path, configuration, contracts, failure
):
    fixture, _ = _azure_fixture(configuration, contracts)
    original_configuration = dict(configuration)
    workload = fixture[SCHEDULER]
    if failure == "duplicate-env":
        environment = workload["revision"]["containers"][0]["env"]
        environment.append(dict(environment[0]))
    elif failure == "missing-env":
        workload["revision"]["containers"][0]["env"].pop(0)
    elif failure == "missing-secret":
        workload["secrets"].pop(0)
    elif failure == "empty-secret":
        workload["secrets"][0]["value"] = ""
    elif failure == "unresolved-secret":
        workload["secrets"][0].pop("value")
    elif failure == "removed-input":
        configuration.pop("ENCRYPTION_KEY")
    elif failure == "empty-input":
        configuration["ENCRYPTION_KEY"] = ""
    else:
        workload["secret_read_failure"] = True
    result, calls = _shell_harness(tmp_path, configuration, contracts, fixture=fixture)
    assert result.returncode != 0
    assert _mutations(calls) == []
    _no_secret_output(result, original_configuration)


def test_initiation_rechecks_selected_workload_before_mutation(
    tmp_path, configuration, contracts
):
    fixture, _ = _azure_fixture(configuration, contracts)
    fixture[WORKER]["secrets"][0]["value"] += "-changed-before-update"
    result, calls = _shell_harness(
        tmp_path, configuration, contracts, fixture=fixture, action="initiate"
    )
    assert result.returncode != 0
    assert _mutations(calls) == []


def test_preserved_deployment_updates_images_without_secret_writes_or_deletions(
    tmp_path, configuration, contracts
):
    result, calls = _shell_harness(tmp_path, configuration, contracts)
    assert result.returncode == 0, result.stdout + result.stderr
    mutations = _mutations(calls)
    assert [call[:3] for call in mutations] == [
        ["containerapp", "job", "create"],
        *[["containerapp", "update", "-n"] for _ in APPS],
    ]
    updates = [call for call in calls if call[:2] == ["containerapp", "update"]]
    assert [call[call.index("-n") + 1] for call in updates] == list(APPS)
    for call in updates:
        assert "ENCRYPTION_KEY=secretref:encryption-key" in call
        assert "SESSION_SECRET_KEY=secretref:session-secret-key" in call
        assert "DATABASE_URL=secretref:database-url" in call
        assert not any("synthetic-never-log" in argument for argument in call)
    _no_secret_output(result, configuration)


@pytest.mark.parametrize(
    "failure",
    (
        "unhealthy",
        "unprovisioned",
        "stopped",
        "multiple",
        "partial-traffic",
        "wrong-route",
    ),
)
def test_unready_workload_or_wrong_traffic_blocks_mutation(
    tmp_path, configuration, contracts, failure
):
    fixture, _ = _azure_fixture(configuration, contracts)
    app = fixture[API]
    if failure == "unhealthy":
        fixture[WORKER]["revision"]["health"] = "Unhealthy"
    elif failure == "unprovisioned":
        fixture[SCHEDULER]["revision"]["provisioning"] = "Provisioning"
    elif failure == "stopped":
        app["revision"]["running"] = "Stopped"
    elif failure == "multiple":
        app["workload"]["activeRevisionsMode"] = "Multiple"
    elif failure == "partial-traffic":
        app["workload"]["traffic"][0]["weight"] = 50
    else:
        app["workload"]["traffic"] = [
            {"revisionName": "synthetic-old-revision", "weight": 100}
        ]
    result, calls = _shell_harness(tmp_path, configuration, contracts, fixture=fixture)
    assert result.returncode != 0
    assert "not uniformly ready" in result.stdout + result.stderr
    assert _mutations(calls) == []
    _no_secret_output(result, configuration)


def test_preservation_cleanup_is_read_only_even_with_disabled_capabilities(
    tmp_path, configuration, contracts
):
    result, calls = _shell_harness(tmp_path, configuration, contracts, action="cleanup")
    assert result.returncode == 0
    assert calls == []
