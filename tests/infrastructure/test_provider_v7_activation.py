from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL = "ENABLE_PROVIDER_V7"
BACKEND_FLAGS = ("AUTHORITY_KERNEL_ENABLED", "AGENT_API_ENABLED")
FORCED_FALSE_FLAGS = (
    "SCRIBE_HANDOFF_ENABLED",
    "SCRIBE_WORKFLOW_ENABLED",
    "SESSION_PREP_SYNTHETIC_ENABLED",
    "STRIPE_LIVE_MODE",
    "DURABLE_WEBHOOK_INBOX_ENABLED",
    "ZOOM_WATCHDOG_ENABLED",
)
STRIPE_ROLLOUT_FLAGS = ("STRIPE_INTEGRATION_ENABLED", "STRIPE_BILLING_ENABLED")
FRONTEND_FLAG = "VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED"
PROVIDER_ORIGIN = (
    "https://cosmaneura-sovereign-staging-app.happygrass-736dc5a6."
    "northcentralus.azurecontainerapps.io"
)
PROVIDER_CLIENT_ID = "8cb2ecb5-3554-4405-be6f-5eda985c2f04"
PUBLIC_CLIENT_ID = "5074cac1-5555-43e9-a469-3e0b7e16608e"
FRONTEND_BUILD_KEYS = (
    "NODE_ENV",
    "OPENAPI_JSON_PATH",
    "VITE_CLERK_PUBLIC_KEY",
    "VITE_INTAKE_CHAT_DOMAIN",
    "VITE_POSTHOG_HOST",
    "VITE_POSTHOG_KEY",
    "VITE_PYTHON_URL",
    "VITE_SENTRY_DSN",
    FRONTEND_FLAG,
    "VITE_SOVEREIGN_PROVIDER_URL",
)


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _frontend_resolve_step(job: str, environment: str) -> str:
    start = job.index(f"Resolve {environment} frontend build configuration")
    end = job.index("uses: docker/login-action@", start)
    return job[start:end]


def _shell_array(source: str, name: str) -> str:
    start = source.index(f"\n{name}=(") + 1
    end = source.index("\n)", start)
    return source[start : end + 2]


def _hcl_variable(source: str, name: str) -> str:
    start = source.index(f'variable "{name}"')
    end = source.index("\n}\n", start)
    return source[start : end + 2]


def _deploy_control_prefix(configuration: dict[str, str] | None = None) -> str:
    rendered = {"SOVEREIGN_RECORD_GRANT_SECRET": "synthetic", **(configuration or {})}
    deploy = _read("infra-staging/deploy.sh")
    capture_start = deploy.index(f'{CONTROL}="${{{CONTROL}:-}}"')
    capture_end = deploy.index("# Resolve protected configuration", capture_start)
    reconciliation_start = deploy.index(
        "# Keep the provider-v7 runtime gates synchronized", capture_end
    )
    reconciliation_end = deploy.index("\nemit_github_output() {", reconciliation_start)
    return (
        deploy[capture_start:capture_end]
        + f"ENV_JSON='{json.dumps(rendered)}'\n"
        + deploy[reconciliation_start:reconciliation_end]
    )


def _run_deploy_control(
    value: str | None,
    record_grants: str | None = "true",
    configuration: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    # Execute the real validation and JSON reconciliation statements without
    # entering the protected configuration resolver.
    script = _deploy_control_prefix(configuration)
    script += '\nprintf "%s\\n" "$ENV_JSON"\n'
    environment = os.environ.copy()
    if record_grants is None:
        environment.pop("ENABLE_SOVEREIGN_RECORD_GRANTS", None)
    else:
        environment["ENABLE_SOVEREIGN_RECORD_GRANTS"] = record_grants
    if value is None:
        environment.pop(CONTROL, None)
    else:
        environment[CONTROL] = value
    return subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def _render_frontend_flag(value: str | None) -> subprocess.CompletedProcess[str]:
    environment = {
        "PATH": "/usr/bin:/bin",
    }
    if value is not None:
        environment[CONTROL] = value
    return subprocess.run(
        [
            "/bin/bash",
            "-c",
            (f'source .env.staging.frontend; printf "%s\\n" "${{{FRONTEND_FLAG}}}"'),
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def _run_frontend_build_allowlist(
    tmp_path: Path,
    value: str,
    *,
    build_environment: str = "staging",
    omit_flag: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    configuration = {key: f"test-{key.lower()}" for key in FRONTEND_BUILD_KEYS}
    if omit_flag:
        configuration.pop(FRONTEND_FLAG)
    else:
        configuration[FRONTEND_FLAG] = value
    configuration_path = tmp_path / f"{build_environment}-frontend-{value}.json"
    configuration_path.write_text(json.dumps(configuration), encoding="utf-8")
    environment = os.environ.copy()
    environment.update(
        {
            "JAVASCRIPT_BUILD_ENV_JSON": str(configuration_path),
            "JAVASCRIPT_SECRETS_FILE": f".env.{build_environment}.frontend",
        }
    )
    return subprocess.run(
        ["just", "_javascript_build_env_args"],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
    )


def _decode_build_arguments(output: bytes) -> dict[str, str]:
    records = output.split(b"\0")
    assert records[-1] == b""
    records.pop()
    assert records[::2] == [b"--env"] * (len(records) // 2)
    return {
        entry.decode("utf-8").split("=", 1)[0]: entry.decode("utf-8").split("=", 1)[1]
        for entry in records[1::2]
    }


def test_staging_provider_v7_switch_is_single_default_off_control() -> None:
    setting = f"{CONTROL}: ${{{{ vars.{CONTROL} || 'false' }}}}"
    manual = _read(".github/workflows/build_and_publish_local.yml")
    canonical = _read(".github/workflows/deploy_staging_candidate.yml")

    # Only the canonical candidate workflow deploys staging, so only it may
    # carry this control.
    assert CONTROL not in manual
    assert canonical.count(f"vars.{CONTROL}") > 0
    assert canonical.count(f"vars.{CONTROL}") == canonical.count(setting)

    for path in (".env.staging.backend", "just/direnv.just"):
        assert f'{CONTROL}="${{{CONTROL}:-false}}"' in _read(path)


def test_frontend_workflow_resolvers_include_authority_flag_without_control_leak() -> (
    None
):
    workflow = _read(".github/workflows/build_and_publish_local.yml")
    canonical = _read(".github/workflows/deploy_staging_candidate.yml")
    production_resolve = _frontend_resolve_step(workflow, "production")
    exact_boolean_check = f'(.{FRONTEND_FLAG} == "true" or .{FRONTEND_FLAG} == "false")'

    assert production_resolve.count(f'\n              {FRONTEND_FLAG}: "false",') == 1
    assert canonical.count(f"\n              {FRONTEND_FLAG},") == 1
    assert exact_boolean_check in production_resolve
    # Staging's resolver is in the candidate workflow and wraps the same check
    # across two lines; compare with whitespace collapsed so reindenting the
    # workflow cannot fail this for a reason unrelated to the contract.
    assert exact_boolean_check in " ".join(canonical.split())
    assert CONTROL not in production_resolve
    assert "VITE_SOVEREIGN_CLERK_TEMPLATE" not in workflow
    assert "VITE_SOVEREIGN_CLERK_TEMPLATE" not in canonical


def test_frontend_environment_files_remove_legacy_clerk_token_template() -> None:
    for path in (
        ".env.production.frontend",
        ".env.staging.frontend",
        ".env.dev.local-example",
        "just/build.just",
    ):
        assert "VITE_SOVEREIGN_CLERK_TEMPLATE" not in _read(path)


def test_frontend_build_allowlist_preserves_true_and_false_values(
    tmp_path: Path,
) -> None:
    for value in ("false", "true"):
        result = _run_frontend_build_allowlist(tmp_path, value)

        assert result.returncode == 0, result.stderr.decode("utf-8")
        arguments = _decode_build_arguments(result.stdout)
        assert tuple(sorted(arguments)) == tuple(sorted(FRONTEND_BUILD_KEYS))
        assert arguments[FRONTEND_FLAG] == value


def test_frontend_build_allowlist_requires_authority_flag(tmp_path: Path) -> None:
    result = _run_frontend_build_allowlist(tmp_path, "false", omit_flag=True)

    assert result.returncode != 0
    assert (
        b"Resolved frontend build configuration has an invalid shape." in result.stderr
    )


def test_frontend_build_allowlist_rejects_noncanonical_boolean(tmp_path: Path) -> None:
    result = _run_frontend_build_allowlist(tmp_path, "TRUE")

    assert result.returncode != 0
    assert (
        b"Resolved frontend build configuration has an invalid shape." in result.stderr
    )


def test_frontend_build_allowlist_keeps_production_false(tmp_path: Path) -> None:
    disabled = _run_frontend_build_allowlist(
        tmp_path, "false", build_environment="production"
    )
    enabled = _run_frontend_build_allowlist(
        tmp_path, "true", build_environment="production"
    )

    assert disabled.returncode == 0, disabled.stderr.decode("utf-8")
    assert _decode_build_arguments(disabled.stdout)[FRONTEND_FLAG] == "false"
    assert enabled.returncode != 0
    assert (
        b"Resolved frontend build configuration has an invalid shape." in enabled.stderr
    )


def test_unset_switch_blocks_canonical_deploy_while_frontend_defaults_false() -> None:
    result = _run_deploy_control(None)
    frontend = _render_frontend_flag(None)

    assert result.returncode == 1
    assert "must remain true for canonical staging deployment" in result.stdout
    assert frontend.returncode == 0, frontend.stderr
    assert frontend.stdout.strip() == "false"


def test_invalid_switch_is_rejected_before_configuration_is_rendered() -> None:
    result = _run_deploy_control("TRUE")

    assert result.returncode == 1
    assert (
        result.stdout.strip()
        == f"{CONTROL} must remain true for canonical staging deployment."
    )
    assert "AUTHORITY_KERNEL_ENABLED" not in result.stdout


def test_record_grant_switch_must_be_true_before_configuration_is_rendered() -> None:
    for value in (None, "false", "TRUE"):
        result = _run_deploy_control("true", record_grants=value)

        assert result.returncode == 1
        assert (
            result.stdout.strip()
            == "ENABLE_SOVEREIGN_RECORD_GRANTS must remain true for canonical "
            "staging deployment."
        )
        assert "AUTHORITY_KERNEL_ENABLED" not in result.stdout


def test_true_switch_executably_injects_both_backend_gates() -> None:
    result = _run_deploy_control("true")
    frontend_result = _render_frontend_flag("true")

    assert result.returncode == 0, result.stderr
    rendered = json.loads(result.stdout)
    assert all(rendered[name] == "true" for name in BACKEND_FLAGS)
    assert all(rendered[name] == "false" for name in FORCED_FALSE_FLAGS)
    assert all(rendered[name] == "false" for name in STRIPE_ROLLOUT_FLAGS)

    frontend = _read(".env.staging.frontend")
    assert f'export {FRONTEND_FLAG}="${{{CONTROL}:-false}}"' in frontend
    assert frontend_result.returncode == 0, frontend_result.stderr
    assert frontend_result.stdout.strip() == "true"


def test_staging_stripe_rollout_flags_follow_reviewed_configuration() -> None:
    configured = {name: "true" for name in (*STRIPE_ROLLOUT_FLAGS, *FORCED_FALSE_FLAGS)}
    result = _run_deploy_control("true", configuration=configured)

    assert result.returncode == 0, result.stderr
    rendered = json.loads(result.stdout)
    assert all(rendered[name] == "true" for name in STRIPE_ROLLOUT_FLAGS)
    assert all(rendered[name] == "false" for name in FORCED_FALSE_FLAGS)


def test_staging_contract_derives_issuer_and_pins_provider_origin() -> None:
    backend = _read(".env.staging.backend")
    frontend = _read(".env.staging.frontend")

    assert "CLERK_DEPLOYMENT_PUBLISHABLE_KEY='op://" in backend
    assert 'export AUTHORITY_CLERK_ISSUER="$(' in backend
    assert "--environment staging --derive-issuer" in backend
    provider_setting = (
        'export VITE_SOVEREIGN_PROVIDER_URL="'
        f"${{VITE_SOVEREIGN_PROVIDER_URL:-{PROVIDER_ORIGIN}}}"
        '"'
    )
    assert provider_setting in frontend
    for flag in BACKEND_FLAGS:
        assert f'export {flag}="$ENABLE_PROVIDER_V7"' in backend


def test_platform_api_origin_is_explicitly_propagated_in_both_environments() -> None:
    staging_deploy = _read("infra-staging/deploy.sh")
    staging_variables = _read("infra-staging/variables.tf")
    production_variables = _read("infra/variables.tf")

    assert "API_BASE_URL" in _shell_array(staging_deploy, "PUBLIC_VARS")
    assert '"API_BASE_URL"' in _hcl_variable(staging_variables, "python_public_vars")
    assert '"API_BASE_URL"' in _hcl_variable(production_variables, "python_public_vars")


def test_staging_activation_remains_provider_only() -> None:
    backend = _read(".env.staging.backend")
    deploy = _read("infra-staging/deploy.sh")

    assert f"export AGENT_API_SOVEREIGN_CLIENT_IDS={PROVIDER_CLIENT_ID}" in backend
    assert PUBLIC_CLIENT_ID not in backend
    assert CONTROL not in _shell_array(deploy, "PUBLIC_VARS")


def test_provider_v7_control_does_not_leak_into_production_paths() -> None:
    production_paths = (
        ".env.production.backend",
        ".env.production.frontend",
        "infra/deploy.sh",
    )
    for path in production_paths:
        source = _read(path)
        assert CONTROL not in source
        assert PROVIDER_ORIGIN not in source

    workflow = _read(".github/workflows/build_and_publish_local.yml")
    assert CONTROL not in workflow
