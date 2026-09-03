from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "deploy_revision_suffix.sh"
SHA = "72dee94186283eabb736e787575b4c2a5e1ce32e"
DIGEST = "sha256:" + "a" * 64
RECOVERY_DIGEST = "sha256:" + "b" * 64
STAGING_CLIENT_ID = "45b940bf-2f4f-4b10-bcc3-4679d8855a6f"
STAGING_TENANT_ID = "d51754d0-bba5-4810-820e-afac542844d6"
STAGING_SUBSCRIPTION_ID = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"
CONTAINERS = (
    "cosmaneura-staging-worker",
    "cosmaneura-staging-scheduler",
    "cosmaneura-staging-app",
)


def _shell_function(source: str, name: str) -> str:
    start = source.index(f"{name}() {{")
    end = source.index("\n}", start)
    return source[start : end + 2]


def _generate(
    *,
    run_id: str | None = "32790258415",
    attempt: str | None = "1",
    sha: str = SHA,
    containers: tuple[str, ...] = CONTAINERS,
    overrides: bool = False,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    for name in (
        "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT",
        "DEPLOY_RUN_ID",
        "DEPLOY_RUN_ATTEMPT",
    ):
        environment.pop(name, None)

    prefix = "DEPLOY" if overrides else "GITHUB"
    if run_id is not None:
        environment[f"{prefix}_RUN_ID"] = run_id
    if attempt is not None:
        environment[f"{prefix}_RUN_ATTEMPT"] = attempt

    script = """
source "$1"
shift
suffix=$(generate_deploy_revision_suffix "$1") || exit
shift
validate_deploy_revision_suffix "$suffix" "$@" || exit
printf '%s\n' "$suffix"
"""
    return subprocess.run(
        ["bash", "-c", script, "test", str(HELPER), sha, *containers],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def test_same_attempt_is_stable_and_retry_attempt_is_distinct() -> None:
    first = _generate(attempt="1")
    repeated = _generate(attempt="1")
    retry = _generate(attempt="2")

    assert first.returncode == repeated.returncode == retry.returncode == 0
    assert first.stdout == repeated.stdout
    assert first.stdout != retry.stdout
    assert first.stdout.strip().endswith(SHA[:7])


def test_explicit_controlled_invocation_uses_deploy_overrides() -> None:
    result = _generate(run_id="42", attempt="3", overrides=True)

    assert result.returncode == 0
    assert result.stdout.strip() == f"r42a3-{SHA[:7]}"


@pytest.mark.parametrize(
    ("run_id", "attempt", "sha"),
    [
        (None, "1", SHA),
        ("32790258415", None, SHA),
        ("0", "1", SHA),
        ("not-a-number", "1", SHA),
        ("32790258415", "0", SHA),
        ("32790258415", "attempt-2", SHA),
        ("32790258415", "1", "short-sha"),
        ("32790258415", "1", SHA.upper()),
    ],
)
def test_invalid_revision_inputs_fail_closed(
    run_id: str | None,
    attempt: str | None,
    sha: str,
) -> None:
    result = _generate(run_id=run_id, attempt=attempt, sha=sha)

    assert result.returncode != 0
    assert result.stdout == ""


def test_suffix_and_longest_staging_revision_respect_azure_contract() -> None:
    result = _generate()

    assert result.returncode == 0
    suffix = result.stdout.strip()
    assert re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", suffix)
    assert len(f"cosmaneura-staging-scheduler--{suffix}") <= 54


def test_revision_name_overflow_fails_closed() -> None:
    result = _generate(run_id="9" * 40, attempt="1")

    assert result.returncode != 0
    assert "54-character limit" in result.stderr


def test_validator_rejects_consecutive_hyphens() -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; validate_deploy_revision_suffix bad--suffix app',
            "test",
            str(HELPER),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "consecutive hyphens" in result.stderr


def _run_deployment_check(
    deploy_path: str,
    image_variable: str,
    *,
    latest_revision: str,
) -> subprocess.CompletedProcess[str]:
    check_function = _shell_function(
        (ROOT / deploy_path).read_text(encoding="utf-8"),
        "check_deployment",
    )
    suffix = f"r32790258415a2-{SHA[:7]}"
    expected_image = (
        f"registry.example/cosmaneura@{DIGEST}"
        if deploy_path == "infra-staging/deploy.sh"
        else f"registry.example/cosmaneura:{SHA}"
    )
    script = f"""
RESOURCE_GROUP=test-resource-group
REVISION_STRING={suffix}
{image_variable}=registry.example/cosmaneura
STAGING_IMAGE_REFERENCE=registry.example/cosmaneura@{DIGEST}
SCHEDULER_CONTAINER=test-scheduler
sleep() {{ :; }}
az() {{
    if [[ "$*" == *"properties.latestRevisionName"* ]]; then
        printf '%s\n' "$TEST_LATEST_REVISION"
    elif [[ "$1 $2" == "containerapp show" ]]; then
        return 0
    elif [[ "$1 $2 $3" == "containerapp revision show" ]]; then
        printf 'revision-show-called\n' >&2
        printf '{{"image":"{expected_image}","health":"Healthy","running":"Running","provisioning":"Provisioned"}}\n'
    else
        printf 'unexpected az call: %s\n' "$*" >&2
        return 97
    fi
}}
{check_function}
check_deployment test-app {SHA} 1
"""
    environment = os.environ.copy()
    environment["TEST_LATEST_REVISION"] = latest_revision
    return subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    ("deploy_path", "image_variable"),
    [
        ("infra/deploy.sh", "PRODUCTION_IMAGE"),
        ("infra-staging/deploy.sh", "STAGING_IMAGE"),
    ],
)
def test_health_check_accepts_only_this_attempts_exact_revision(
    deploy_path: str,
    image_variable: str,
) -> None:
    suffix = f"r32790258415a2-{SHA[:7]}"
    result = _run_deployment_check(
        deploy_path,
        image_variable,
        latest_revision=f"test-app--{suffix}",
    )

    assert result.returncode == 0


@pytest.mark.parametrize(
    ("deploy_path", "image_variable"),
    [
        ("infra/deploy.sh", "PRODUCTION_IMAGE"),
        ("infra-staging/deploy.sh", "STAGING_IMAGE"),
    ],
)
def test_prior_attempts_healthy_same_image_revision_cannot_pass(
    deploy_path: str,
    image_variable: str,
) -> None:
    result = _run_deployment_check(
        deploy_path,
        image_variable,
        latest_revision=f"test-app--r32790258415a1-{SHA[:7]}",
    )

    assert result.returncode != 0
    assert "not this deployment attempt yet" in result.stdout


@pytest.mark.parametrize(
    "deploy_path",
    ["infra/deploy.sh", "infra-staging/deploy.sh"],
)
def test_deploy_validates_attempt_scoped_suffix_before_any_azure_call(
    deploy_path: str,
) -> None:
    deploy = (ROOT / deploy_path).read_text(encoding="utf-8")

    assert 'source "$SCRIPT_DIR/../scripts/deploy_revision_suffix.sh"' in deploy
    generated = deploy.index("generate_deploy_revision_suffix")
    validated = deploy.index("validate_deploy_revision_suffix", generated)
    first_azure = deploy.index("az account show")
    update = deploy.index('az containerapp update -n "$container_name"')

    assert generated < validated < first_azure < update
    assert '--revision-suffix "$REVISION_STRING"' in deploy
    assert "gen_string_for_sha" not in deploy


def test_staging_immutable_candidate_and_evidence_boundaries() -> None:
    deploy = (ROOT / "infra-staging/deploy.sh").read_text(encoding="utf-8")
    drop = deploy.index("unset OP_SERVICE_ACCOUNT_TOKEN RECALL_WEBHOOK_SECRET")
    azure = deploy.index("az account show")

    assert (
        drop
        < deploy.index("git rev-parse")
        < deploy.index('git show "$GIT_SHA:Procfile"')
        < azure
    )
    assert '"$SCRIPT_DIR/../Procfile"' not in deploy
    assert 'STAGING_IMAGE_REFERENCE="$STAGING_IMAGE@$STAGING_IMAGE_DIGEST"' in deploy
    assert "STAGING_TAG=" not in deploy
    state = _shell_function(deploy, "collect_workload_state")
    assert "length == 3" in state and "latestReadyRevision" in state
    assert "trap capture_failed_workload_state EXIT" in deploy
    assert deploy.index("emit_github_output configuration_evidence") < azure
    assert deploy.index("deployment_blockers | length == 0") < azure
    assert deploy.index("emit_github_output workloads_before") < deploy.index(
        "\nrun_migrations\n"
    )
    assert deploy.index("emit_github_output schema_before") < deploy.index(
        "--args /app/scripts/billing_migration_preflight.py"
    )
    assert "identity_fingerprint_set_sha256" in deploy
    assert "staging_identity_fingerprint_set_mismatch" in deploy
    assert "database_sslmode_not_verify_full" in deploy
    assert "EXPECTED_STAGING_IDENTITY_FINGERPRINTS_SHA256" in deploy
    assert 'if [[ "$ENABLE_PROVIDER_V7" != "true" ]]' in deploy
    assert 'if [[ "$ENABLE_SOVEREIGN_RECORD_GRANTS" != "true" ]]' in deploy
    for pinned_id in (
        "45b940bf-2f4f-4b10-bcc3-4679d8855a6f",
        "d51754d0-bba5-4810-820e-afac542844d6",
        "bab2a709-1fcb-4c87-a9fc-802f7477ec13",
    ):
        assert pinned_id in deploy
    assert '"$STAGING_REPOSITORY@$EXPECTED_RECOVERY_DIGEST"' in deploy
    assert '"$RECOVERY_MANIFEST_DIGEST" != "$EXPECTED_RECOVERY_DIGEST"' in deploy
    assert deploy.index("Configured Azure identity inputs") < deploy.index(
        '"$STAGING_REPOSITORY:$GIT_SHA"'
    )
    assert deploy.index('"$STAGING_REPOSITORY@$EXPECTED_RECOVERY_DIGEST"') < (
        deploy.index("az acr credential show")
    )
    migration = _shell_function(deploy, "run_migrations")
    assert migration.index('wait_for_migration_job_execution "$execution_name"') < (
        migration.index('capture_schema_revision "Post-migration"')
    )
    assert "migration_status=$?" in migration
    assert "observed post-attempt schema was recorded" in migration
    assert '"$STAGING_IMAGE":*)' not in _shell_function(
        deploy, "resolve_staging_digest"
    )

    production = (ROOT / "infra/deploy.sh").read_text(encoding="utf-8")
    start = deploy.index("\nPUBLIC_VARS=(")
    public_vars = deploy[start : deploy.index("\n)", start)]
    verifier = _shell_function(deploy, "verify_candidate_workload_state")

    for flag in ("SCRIBE_WORKFLOW_ENABLED", "SESSION_PREP_SYNTHETIC_ENABLED"):
        assert f'{flag}: "false"' in deploy and flag in public_vars
        assert flag not in production
    assert ".publicConfig[$item.key]" in verifier
    assert ".environmentContractExact == true" in verifier


def test_staging_workload_capture_propagates_azure_failure() -> None:
    result = _workload_capture(mode="safe", azure_failure=True)
    assert result.returncode != 0


def _workload_capture(
    *,
    app_environment: list[dict[str, str | None]] | None = None,
    app_extra_container: bool = False,
    app_args: list[str] | None = None,
    mode: str = "exact",
    azure_failure: bool = False,
) -> subprocess.CompletedProcess[str]:
    deploy = (ROOT / "infra-staging/deploy.sh").read_text(encoding="utf-8")
    default_environment: list[dict[str, str | None]] = [
        {"name": "DATABASE_URL", "value": None, "secretRef": "database-url"},
        {"name": "PYTHON_ENV", "value": "staging", "secretRef": None},
    ]
    if app_environment is None:
        app_environment = default_environment

    def revision(name: str, args: list[str]) -> str:
        containers = [
            {
                "name": name,
                "image": f"registry.example/cosmaneura@{DIGEST}",
                "command": None,
                "args": args,
                "env": app_environment if name == "app" else default_environment,
            }
        ]
        if name == "app" and app_extra_container:
            containers.append({"name": "sidecar", "image": "other", "env": []})
        return json.dumps(
            {
                "containers": containers,
                "health": "Healthy",
                "running": "Running",
                "provisioning": "Provisioned",
            }
        )

    environment = {
        **os.environ,
        "APP_REVISION": revision("app", app_args or []),
        "WORKER_REVISION": revision("worker", ["worker command"]),
        "SCHEDULER_REVISION": revision("scheduler", ["scheduler command"]),
        "FAIL_AZURE": "true" if azure_failure else "false",
    }
    functions = "\n".join(
        _shell_function(deploy, name)
        for name in (
            "record_grants_enabled_for",
            "billing_workload_for",
            "billing_integration_enabled_for",
            "configure_workload_contract",
            "collect_workload_state",
        )
    )
    script = f"""
RESOURCE_GROUP=test
CRITICAL_CONTAINERS='worker scheduler app'
API_CONTAINER=app
WORKER_CONTAINER=worker
SCHEDULER_CONTAINER=scheduler
STAGING_IMAGE=registry.example/cosmaneura
STAGING_IMAGE_REFERENCE=registry.example/cosmaneura@{DIGEST}
STAGING_WORKER_COMMAND='worker command'
SCHEDULER_COMMAND='scheduler command'
ENABLE_SOVEREIGN_RECORD_GRANTS=false
STRIPE_INTEGRATION_ENABLED=false
COMMON_SECRET_VARS=(DATABASE_URL)
API_ONLY_SECRET_VARS=()
BILLING_SECRET_VARS=()
BILLING_API_ONLY_SECRET_VARS=()
BILLING_PUBLIC_VARS=()
PUBLIC_VARS=(PYTHON_ENV)
ENV_JSON='{{"DATABASE_URL":"credential-must-not-appear","PYTHON_ENV":"staging"}}'
az() {{
    local original="$*"
    [[ "$FAIL_AZURE" == true ]] && return 42
    local name=''
    while [[ $# -gt 0 ]]; do
        [[ "$1" == "-n" ]] && name="$2"
        shift
    done
    if [[ "$*" == *unused* ]]; then return 97; fi
    case "$name" in
        app) local revision="$APP_REVISION" ;;
        worker) local revision="$WORKER_REVISION" ;;
        scheduler) local revision="$SCHEDULER_REVISION" ;;
    esac
    if [[ "${{FUNCNAME[0]}}" == impossible ]]; then return 97; fi
    if [[ "$revision" == *'"containers"'* && "$name" != '' ]]; then
        if [[ "$original" == *"revision show"* ]]; then printf '%s\n' "$revision"; return; fi
    fi
    local traffic=null
    [[ "$name" == app ]] && traffic='[{{"latestRevision":true,"weight":100}}]'
    printf '{{"name":"%s","latestRevision":"%s-r1","latestReadyRevision":"%s-r1","activeRevisionsMode":"Single","traffic":%s}}\n' "$name" "$name" "$name" "$traffic"
}}
{functions}
collect_workload_state {mode}
"""
    return subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def test_workload_evidence_accepts_only_exact_single_container_contract() -> None:
    result = _workload_capture()
    assert result.returncode == 0, result.stderr
    assert "credential-must-not-appear" not in result.stdout + result.stderr
    state = json.loads(result.stdout)
    assert all(item["environmentContractExact"] for item in state)
    assert all(item["secretRefs"] == {"DATABASE_URL": "database-url"} for item in state)


def test_workload_evidence_accepts_azure_normalized_empty_secret_value() -> None:
    result = _workload_capture(
        app_environment=[
            {"name": "DATABASE_URL", "value": "", "secretRef": "database-url"},
            {"name": "PYTHON_ENV", "value": "staging", "secretRef": None},
        ]
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("extra_container", "environment", "app_args"),
    [
        (True, None, None),
        (False, [{"name": "UNKNOWN", "value": "hidden", "secretRef": None}], None),
        (
            False,
            [{"name": "DATABASE_URL", "value": "hidden", "secretRef": None}],
            None,
        ),
        (
            False,
            [
                {"name": "DATABASE_URL", "value": None, "secretRef": "database-url"},
                {"name": "PYTHON_ENV", "value": "development", "secretRef": None},
            ],
            None,
        ),
        (False, None, ["unexpected-api-command"]),
    ],
)
def test_workload_evidence_rejects_sidecars_unknown_or_unsafe_bindings_and_api_args(
    extra_container: bool,
    environment: list[dict[str, str | None]] | None,
    app_args: list[str] | None,
) -> None:
    result = _workload_capture(
        app_extra_container=extra_container,
        app_environment=environment,
        app_args=app_args,
    )
    assert result.returncode != 0
    assert "hidden" not in result.stdout + result.stderr


def test_recovery_digest_rejects_mutable_workload_tags_without_registry_lookup() -> (
    None
):
    deploy = (ROOT / "infra-staging/deploy.sh").read_text(encoding="utf-8")
    script = f"""
STAGING_IMAGE=registry.example/cosmaneura
az() {{ echo called >&2; return 97; }}
{_shell_function(deploy, "resolve_staging_digest")}
resolve_staging_digest registry.example/cosmaneura:{SHA}
"""
    result = subprocess.run(
        ["bash", "-c", script], check=False, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "called" not in result.stderr


def _staging_config(sslmode: str = "verify-full") -> dict[str, str]:
    return {
        "PYTHON_ENV": "staging",
        "DATABASE_URL": "postgresql://user:password@cosmaneura-staging-postgres.postgres.database.azure.com:5432/staging?sslmode="
        + sslmode,
        "API_BASE_URL": "https://staging-app.cosmaneura.com",
        "ZOOM_REDIRECT_URI": "https://staging-app.cosmaneura.com/external/v1/zoom-oauth-callback",
        "ZOOM_SUCCESS_REDIRECT": "https://staging-app.cosmaneura.com/settings",
        "COOKIE_DOMAIN": "staging-app.cosmaneura.com",
        "RECALL_API_BASE": "https://us-west-2.recall.ai/api/v1",
        "ALLOWED_HOST_LIST": "staging-app.cosmaneura.com,staging-chat.cosmaneura.com",
        "INTAKE_CHAT_DOMAIN": "staging-chat.cosmaneura.com",
        "AUTHORITY_CLERK_ISSUER": "https://resolved-emu-53.clerk.accounts.dev",
        "CLERK_DEPLOYMENT_PUBLISHABLE_KEY": "pk_test_clerk",
        "CLERK_PRIVATE_KEY": "sk_test_clerk",
        "ZOOM_CLIENT_ID": "zoom-client",
        "ZOOM_CLIENT_SECRET": "zoom-secret",
        "ZOOM_SECRET_TOKEN": "zoom-token",
        "RECALL_API_KEY": "recall-api",
        "RECALL_WEBHOOK_SECRET": "recall-webhook",
        "SOVEREIGN_RECORD_GRANT_SECRET": "synthetic-record-grant-secret",
        "STRIPE_SECRET_KEY": "",
        "STRIPE_WEBHOOK_SECRET": "",
        "STRIPE_ACCOUNT_ID": "",
        "STRIPE_PRICE_ID": "",
    }


def _fingerprint_set(config: dict[str, str]) -> str:
    fields = {
        "clerk_publishable": "CLERK_DEPLOYMENT_PUBLISHABLE_KEY",
        "clerk_private": "CLERK_PRIVATE_KEY",
        "zoom_client": "ZOOM_CLIENT_ID",
        "zoom_secret": "ZOOM_CLIENT_SECRET",
        "zoom_token": "ZOOM_SECRET_TOKEN",
        "recall_api": "RECALL_API_KEY",
        "recall_webhook": "RECALL_WEBHOOK_SECRET",
        "stripe_account": "STRIPE_ACCOUNT_ID",
        "stripe_key": "STRIPE_SECRET_KEY",
        "stripe_webhook": "STRIPE_WEBHOOK_SECRET",
    }
    fingerprints = {
        f"{label}_sha256": (
            hashlib.sha256(config[name].encode()).hexdigest()
            if config.get(name)
            else None
        )
        for label, name in fields.items()
    }
    canonical = json.dumps(fingerprints, separators=(",", ":"), sort_keys=True)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


@pytest.mark.parametrize(
    ("sslmode", "fingerprint_matches", "grant_secret", "expected_message"),
    [
        (
            "require",
            True,
            "synthetic-record-grant-secret",
            "unresolved deployment blockers",
        ),
        (
            "verify-full",
            False,
            "synthetic-record-grant-secret",
            "unresolved deployment blockers",
        ),
        ("verify-full", True, "", "enabled without a configured key"),
    ],
)
def test_configuration_blockers_stop_before_any_azure_call(
    tmp_path: Path,
    sslmode: str,
    fingerprint_matches: bool,
    grant_secret: str,
    expected_message: str,
) -> None:
    config = _staging_config(sslmode)
    config["SOVEREIGN_RECORD_GRANT_SECRET"] = grant_secret
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    az_log = tmp_path / "az.log"
    commands = {
        "just": "#!/bin/sh\nprintf '%s' \"$FAKE_CONFIG\"\n",
        "python": "#!/bin/sh\ncat >/dev/null\nexit 0\n",
        "git": (
            "#!/bin/sh\n"
            'case "$1" in\n'
            "rev-parse) printf '%s\\n' \"$FAKE_SHA\" ;;\n"
            "show) printf 'worker: worker command\\nscheduler: scheduler command\\n' ;;\n"
            "esac\n"
        ),
        "yq": "#!/bin/sh\ncase \"$2\" in .worker) echo 'worker command' ;; *) echo 'scheduler command' ;; esac\n",
        "az": '#!/bin/sh\nprintf \'%s\\n\' "$*" >>"$FAKE_AZ_LOG"\nexit 99\n',
    }
    for name, source in commands.items():
        path = fake_bin / name
        path.write_text(source)
        path.chmod(0o755)
    expected = _fingerprint_set(config) if fingerprint_matches else "sha256:" + "f" * 64
    result = subprocess.run(
        ["bash", str(ROOT / "infra-staging/deploy.sh"), SHA, DIGEST],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_CONFIG": json.dumps(config),
            "FAKE_SHA": SHA,
            "FAKE_AZ_LOG": str(az_log),
            "EXPECTED_SCHEMA_BEFORE": "a" * 12,
            "EXPECTED_SCHEMA_AFTER": "b" * 12,
            "EXPECTED_RECOVERY_DIGEST": DIGEST,
            "EXPECTED_STAGING_IDENTITY_FINGERPRINTS_SHA256": expected,
            "ENABLE_PROVIDER_V7": "true",
            "ENABLE_SOVEREIGN_RECORD_GRANTS": "true",
        },
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert expected_message in result.stdout
    assert all(
        value not in result.stdout + result.stderr
        for value in ("zoom-secret", "recall-webhook", "sk_test_clerk")
    )
    assert not az_log.exists()


def _run_staging_registry_preflight(
    tmp_path: Path,
    *,
    client_id: str = STAGING_CLIENT_ID,
    tenant_id: str = STAGING_TENANT_ID,
    subscription_id: str = STAGING_SUBSCRIPTION_ID,
    account_json: dict[str, str] | None = None,
    published_digest: str = DIGEST,
    recovery_digest: str = RECOVERY_DIGEST,
    recovery_status: int = 0,
) -> tuple[subprocess.CompletedProcess[str], str]:
    deploy = (ROOT / "infra-staging/deploy.sh").read_text(encoding="utf-8")
    start = deploy.index("if ! AZURE_ACCOUNT_JSON=")
    end = deploy.index("if ! ACR_USERNAME=", start)
    script = "set -euo pipefail\nemit_github_output() { :; }\n" + deploy[start:end]

    fake_bin = tmp_path / "registry-bin"
    fake_bin.mkdir()
    az_log = tmp_path / "registry-az.log"
    fake_az = fake_bin / "az"
    fake_az.write_text(
        """#!/bin/sh
printf '%s\\n' "$*" >>"$FAKE_AZ_LOG"
if [ "$1" = "account" ] && [ "$2" = "show" ]; then
  printf '%s' "$FAKE_ACCOUNT_JSON"
  exit 0
fi
case "$*" in
  *"$FAKE_REPOSITORY:$FAKE_SHA"*)
    printf '%s\\n' "$FAKE_PUBLISHED_DIGEST"
    exit 0
    ;;
  *"$FAKE_REPOSITORY@$FAKE_EXPECTED_RECOVERY_DIGEST"*)
    if [ "$FAKE_RECOVERY_STATUS" -ne 0 ]; then
      exit "$FAKE_RECOVERY_STATUS"
    fi
    printf '%s\\n' "$FAKE_RECOVERY_DIGEST"
    exit 0
    ;;
esac
exit 99
"""
    )
    fake_az.chmod(0o755)
    observed_account = account_json or {
        "subscriptionId": STAGING_SUBSCRIPTION_ID,
        "tenantId": STAGING_TENANT_ID,
        "userName": STAGING_CLIENT_ID,
        "userType": "servicePrincipal",
    }
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "AZURE_CLIENT_ID": client_id,
        "AZURE_TENANT_ID": tenant_id,
        "AZURE_SUBSCRIPTION_ID": subscription_id,
        "FAKE_ACCOUNT_JSON": json.dumps(observed_account),
        "FAKE_AZ_LOG": str(az_log),
        "FAKE_EXPECTED_RECOVERY_DIGEST": RECOVERY_DIGEST,
        "FAKE_PUBLISHED_DIGEST": published_digest,
        "FAKE_RECOVERY_DIGEST": recovery_digest,
        "FAKE_RECOVERY_STATUS": str(recovery_status),
        "FAKE_REPOSITORY": "cosmaneura/cosmaneura",
        "FAKE_SHA": SHA,
        "GIT_SHA": SHA,
        "STAGING_IMAGE": "cosmaneurastagingregistry.azurecr.io/cosmaneura/cosmaneura",
        "STAGING_IMAGE_DIGEST": DIGEST,
        "STAGING_REPOSITORY": "cosmaneura/cosmaneura",
        "EXPECTED_RECOVERY_DIGEST": RECOVERY_DIGEST,
        "PINNED_STAGING_CLIENT_ID": STAGING_CLIENT_ID,
        "PINNED_STAGING_TENANT_ID": STAGING_TENANT_ID,
        "PINNED_STAGING_SUBSCRIPTION_ID": STAGING_SUBSCRIPTION_ID,
    }
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    return result, az_log.read_text() if az_log.exists() else ""


@pytest.mark.parametrize(
    ("client_id", "tenant_id", "subscription_id"),
    [
        (
            "00000000-0000-0000-0000-000000000001",
            STAGING_TENANT_ID,
            STAGING_SUBSCRIPTION_ID,
        ),
        (
            STAGING_CLIENT_ID,
            "00000000-0000-0000-0000-000000000002",
            STAGING_SUBSCRIPTION_ID,
        ),
        (
            STAGING_CLIENT_ID,
            STAGING_TENANT_ID,
            "00000000-0000-0000-0000-000000000003",
        ),
    ],
)
def test_staging_registry_preflight_rejects_unpinned_identity_inputs(
    tmp_path: Path, client_id: str, tenant_id: str, subscription_id: str
) -> None:
    result, az_calls = _run_staging_registry_preflight(
        tmp_path,
        client_id=client_id,
        tenant_id=tenant_id,
        subscription_id=subscription_id,
    )

    assert result.returncode != 0
    assert "Configured Azure identity inputs are not the pinned staging identity" in (
        result.stdout
    )
    assert "acr manifest" not in az_calls


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("userName", "00000000-0000-0000-0000-000000000004"),
        ("userType", "user"),
        ("tenantId", "00000000-0000-0000-0000-000000000005"),
        ("subscriptionId", "00000000-0000-0000-0000-000000000006"),
    ],
)
def test_staging_registry_preflight_rejects_wrong_active_identity(
    tmp_path: Path, field: str, wrong_value: str
) -> None:
    account = {
        "subscriptionId": STAGING_SUBSCRIPTION_ID,
        "tenantId": STAGING_TENANT_ID,
        "userName": STAGING_CLIENT_ID,
        "userType": "servicePrincipal",
    }
    account[field] = wrong_value
    result, az_calls = _run_staging_registry_preflight(tmp_path, account_json=account)

    assert result.returncode != 0
    assert "Active Azure identity is not the exact staging deployment identity" in (
        result.stdout
    )
    assert "acr manifest" not in az_calls


@pytest.mark.parametrize(
    ("recovery_status", "observed_digest", "expected_message"),
    [
        (1, RECOVERY_DIGEST, "reviewed recovery image is not available"),
        (0, "sha256:" + "c" * 64, "recovery manifest does not match"),
    ],
)
def test_staging_registry_preflight_rejects_unavailable_or_wrong_recovery_manifest(
    tmp_path: Path,
    recovery_status: int,
    observed_digest: str,
    expected_message: str,
) -> None:
    result, az_calls = _run_staging_registry_preflight(
        tmp_path,
        recovery_status=recovery_status,
        recovery_digest=observed_digest,
    )

    assert result.returncode != 0
    assert expected_message in result.stdout
    assert f"cosmaneura/cosmaneura@{RECOVERY_DIGEST}" in az_calls


def test_staging_registry_preflight_accepts_exact_identity_and_manifests(
    tmp_path: Path,
) -> None:
    result, az_calls = _run_staging_registry_preflight(tmp_path)

    assert result.returncode == 0, result.stderr
    assert f"cosmaneura/cosmaneura:{SHA}" in az_calls
    assert f"cosmaneura/cosmaneura@{RECOVERY_DIGEST}" in az_calls
