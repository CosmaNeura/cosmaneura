from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL = "ENABLE_PROVIDER_V7"
BACKEND_FLAGS = ("AUTHORITY_KERNEL_ENABLED", "AGENT_API_ENABLED")
FRONTEND_FLAG = "VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED"
CLERK_ISSUER = "https://resolved-emu-53.clerk.accounts.dev"
PROVIDER_ORIGIN = (
    "https://cosmaneura-sovereign-staging-app.happygrass-736dc5a6."
    "northcentralus.azurecontainerapps.io"
)
PROVIDER_CLIENT_ID = "8cb2ecb5-3554-4405-be6f-5eda985c2f04"
PUBLIC_CLIENT_ID = "5074cac1-5555-43e9-a469-3e0b7e16608e"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _staging_job(workflow: str) -> str:
    return workflow[workflow.index("\n  deploy-staging:") :]


def _shell_array(source: str, name: str) -> str:
    start = source.index(f"{name}=(")
    end = source.index("\n)", start)
    return source[start : end + 2]


def _deploy_control_prefix() -> str:
    deploy = _read("infra-staging/deploy.sh")
    return deploy[
        deploy.index(f'{CONTROL}="${{{CONTROL}:-false}}"') : deploy.index(
            'if [[ "$ENABLE_SOVEREIGN_RECORD_GRANTS"'
        )
    ]


def _run_deploy_control(value: str | None) -> subprocess.CompletedProcess[str]:
    # Execute the real validation and JSON reconciliation statements while
    # replacing the secret-bearing direnv command with a deterministic stub.
    script = _deploy_control_prefix().replace(
        'if ! ENV_JSON=$(cd "$SCRIPT_DIR/.." && just direnv_export .env.staging.backend); then',
        "if ! ENV_JSON='{}'; then",
    )
    script += '\nprintf "%s\\n" "$ENV_JSON"\n'
    environment = os.environ.copy()
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


def test_staging_provider_v7_switch_is_single_default_off_control() -> None:
    workflow = _read(".github/workflows/build_and_publish.yml")
    setting = f"{CONTROL}: ${{{{ vars.{CONTROL} || 'false' }}}}"
    staging_start = workflow.index("\n  deploy-staging:")

    assert setting not in workflow[:staging_start]
    assert _staging_job(workflow).count(setting) == 1

    for path in (".env.staging.backend", "just/direnv.just"):
        assert f'{CONTROL}="${{{CONTROL}:-false}}"' in _read(path)


def test_unset_switch_executably_injects_both_backend_gates_as_false() -> None:
    result = _run_deploy_control(None)
    frontend = _render_frontend_flag(None)

    assert result.returncode == 0, result.stderr
    assert (
        result.stdout.strip()
        == '{"AUTHORITY_KERNEL_ENABLED":"false","AGENT_API_ENABLED":"false"}'
    )
    assert frontend.returncode == 0, frontend.stderr
    assert frontend.stdout.strip() == "false"


def test_invalid_switch_is_rejected_before_configuration_is_rendered() -> None:
    result = _run_deploy_control("TRUE")

    assert result.returncode == 1
    assert result.stdout.strip() == f"{CONTROL} must be true or false."
    assert "AUTHORITY_KERNEL_ENABLED" not in result.stdout


def test_true_switch_executably_injects_both_backend_gates() -> None:
    result = _run_deploy_control("true")
    frontend_result = _render_frontend_flag("true")

    assert result.returncode == 0, result.stderr
    assert (
        result.stdout.strip()
        == '{"AUTHORITY_KERNEL_ENABLED":"true","AGENT_API_ENABLED":"true"}'
    )

    frontend = _read(".env.staging.frontend")
    assert f'export {FRONTEND_FLAG}="${{{CONTROL}:-false}}"' in frontend
    assert frontend_result.returncode == 0, frontend_result.stderr
    assert frontend_result.stdout.strip() == "true"


def test_staging_contract_pins_exact_issuer_and_provider_origin() -> None:
    backend = _read(".env.staging.backend")
    frontend = _read(".env.staging.frontend")

    assert f"export AUTHORITY_CLERK_ISSUER={CLERK_ISSUER}" in backend
    provider_setting = (
        'export VITE_SOVEREIGN_PROVIDER_URL="'
        f"${{VITE_SOVEREIGN_PROVIDER_URL:-{PROVIDER_ORIGIN}}}"
        '"'
    )
    assert provider_setting in frontend
    for flag in BACKEND_FLAGS:
        assert f'export {flag}="$ENABLE_PROVIDER_V7"' in backend


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

    workflow = _read(".github/workflows/build_and_publish.yml")
    assert CONTROL not in workflow[: workflow.index("\n  deploy-staging:")]
