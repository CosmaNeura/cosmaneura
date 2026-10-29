import json
import os
import re
import subprocess
import sys
from pathlib import Path

from app.configuration.authority import (
    CANONICAL_CLERK_ISSUER_PATTERN,
    AuthorityKernelSettings,
)
from app.services.sovereign_record_grant import CANONICAL_FERNET_KEY_PATTERN

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _hcl_variable(source: str, name: str) -> str:
    start = source.index(f'variable "{name}"')
    next_variable = source.find('\nvariable "', start + 1)
    return source[start:] if next_variable == -1 else source[start:next_variable]


def _hcl_quoted_pattern(source: str, marker: str) -> str:
    start = source.index(marker)
    match = re.search(r'"(\^[^"\n]+\$)"', source[start:])
    assert match is not None
    return json.loads(f'"{match.group(1)}"')


def test_session_prep_production_rollout_defaults_dark() -> None:
    block = _hcl_variable(
        _read("infra/variables.tf"), "session_prep_production_rollout"
    )

    assert "nullable = false" in block
    assert "enabled             = false" in block
    assert "provider_clerk_subjects" not in block
    assert 'sovereign_client_id = ""' in block
    assert 'agent_api_audience  = ""' in block


def test_one_source_controlled_switch_drives_all_core_receiver_gates() -> None:
    rollout = _read("infra/session_prep_rollout.tf")
    switch = "tostring(var.session_prep_production_rollout.enabled)"

    for key in (
        "AUTHORITY_KERNEL_ENABLED",
        "AGENT_API_ENABLED",
        "SESSION_PREP_PROVIDER_ENABLED",
    ):
        assert rollout.count(f"{key}") == 1
    assert rollout.count(switch) == 3
    assert "SESSION_PREP_HEALTH_ALERTS_ENABLED" not in rollout


def test_activation_requires_paired_identity_without_a_manual_provider_cohort() -> None:
    block = _hcl_variable(
        _read("infra/variables.tf"), "session_prep_production_rollout"
    )

    assert "provider_clerk_subjects" not in block
    assert 'sovereign_client_id != "00000000-0000-0000-0000-000000000000"' in block
    assert 'agent_api_audience != "00000000-0000-0000-0000-000000000000"' in block


def test_runtime_has_no_manual_session_prep_subject_allowlist() -> None:
    configuration = _read("app/configuration/session_prep.py")
    rollout = _read("infra/session_prep_rollout.tf")

    assert "SESSION_PREP_PROVIDER_CLERK_SUBJECTS" not in configuration
    assert "clerk_subjects" not in configuration
    assert "SESSION_PREP_PROVIDER_CLERK_SUBJECTS" not in rollout


def test_api_worker_and_scheduler_receive_one_coherent_session_prep_projection() -> (
    None
):
    main = _read("infra/main.tf")

    assert main.count("local.session_prep_shared_public_values") == 3
    assert main.count("local.session_prep_authority_runtime_public_values") == 2
    assert main.count("local.session_prep_api_public_values") == 1
    assert main.count("local.session_prep_api_env_name_collisions") == 1
    assert main.count("local.session_prep_worker_env_name_collisions") == 1
    assert main.count("local.session_prep_scheduler_env_name_collisions") == 1
    assert main.count("local.session_prep_production_prerequisites_valid") == 1
    assert main.count("local.session_prep_authority_runtime_prerequisites_valid") == 1


def test_worker_receives_the_authority_runtime_required_by_source_revalidation() -> (
    None
):
    rollout = _read("infra/session_prep_rollout.tf")
    main = _read("infra/main.tf")
    worker_runtime = _read("app/services/session_prep_worker.py")
    runtime = _read("app/services/session_prep_runtime.py")

    for key, value in (
        ("AUTHORITY_KERNEL_ENABLED", None),
        ("MOUNT_CONTEXT_TOKEN_TTL_SECONDS", "900"),
        ("MOUNT_CONTEXT_TOKEN_REFRESH_AFTER_SECONDS", "300"),
        ("MOUNT_CONTEXT_SESSION_MAX_AGE_SECONDS", "28800"),
    ):
        assert key in rollout
        if value is not None:
            assert f"{key}" in rollout and f'= "{value}"' in rollout
    worker_block = main[main.index('resource "azurerm_container_app" "worker"') :]
    assert "local.session_prep_authority_runtime_public_values" in worker_block
    assert "settings: AuthorityKernelSettings = AUTHORITY_KERNEL_SETTINGS" in (
        worker_runtime
    )
    assert "not settings.enabled" in runtime
    assert "not settings.is_complete()" in runtime

    environment = os.environ.copy()
    environment.update(
        {
            "AUTHORITY_KERNEL_ENABLED": "true",
            "AUTHORITY_CLERK_ISSUER": "https://clerk.example.invalid",
            "MOUNT_CONTEXT_TOKEN_TTL_SECONDS": "900",
            "MOUNT_CONTEXT_TOKEN_REFRESH_AFTER_SECONDS": "300",
            "MOUNT_CONTEXT_SESSION_MAX_AGE_SECONDS": "28800",
        }
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from app.services.session_prep_worker import "
                "AUTHORITY_KERNEL_SETTINGS as s; "
                "assert s.enabled and s.is_complete()"
            ),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_activation_preflight_uses_the_exact_runtime_issuer_and_fernet_contracts() -> (
    None
):
    rollout = _read("infra/session_prep_rollout.tf")

    assert (
        _hcl_quoted_pattern(rollout, "session_prep_clerk_issuer_valid")
        == CANONICAL_CLERK_ISSUER_PATTERN
    )
    assert (
        _hcl_quoted_pattern(rollout, "session_prep_record_grant_key_valid")
        == CANONICAL_FERNET_KEY_PATTERN
    )
    assert "length(local.session_prep_record_grant_key) == 44" in rollout
    assert "trimspace(local.session_prep_clerk_issuer)" in rollout
    assert "session_prep_record_grant_key" not in _read("infra/outputs.tf")

    for issuer, expected in (
        ("https://clerk.example.invalid", True),
        ("https://clerk.example.invalid/", False),
        ("https://clerk.example.invalid/path", False),
        ("https://clerk.example.invalid?query=yes", False),
        ("https://user@clerk.example.invalid", False),
        ("https://clerk.example.invalid:443", False),
        (" https://clerk.example.invalid", False),
        ("https://localhost", False),
        ("HTTPs://clerk.example.invalid", False),
    ):
        settings = AuthorityKernelSettings(
            enabled=True,
            token_ttl_seconds=900,
            token_refresh_after_seconds=300,
            clerk_issuer=issuer,
        )
        assert settings.has_valid_clerk_issuer() is expected


def test_generic_environment_sets_cannot_shadow_rollout_authority() -> None:
    variables = _hcl_variable(_read("infra/variables.tf"), "python_public_vars")

    for key in (
        "AUTHORITY_KERNEL_ENABLED",
        "AGENT_API_ENABLED",
        "AGENT_API_AUDIENCE",
        "AGENT_API_SOVEREIGN_CLIENT_IDS",
        "MOUNT_CONTEXT_TOKEN_TTL_SECONDS",
        "MOUNT_CONTEXT_TOKEN_REFRESH_AFTER_SECONDS",
        "MOUNT_CONTEXT_SESSION_MAX_AGE_SECONDS",
        "SESSION_PREP_PROVIDER_ENABLED",
        # The retired subject allowlist remains reserved so it cannot be
        # reintroduced through an unreviewed generic production environment.
        "SESSION_PREP_PROVIDER_CLERK_SUBJECTS",
    ):
        assert f'"{key}"' in variables


def test_core_rollout_cannot_enable_the_provider_frontend_or_sovereign() -> None:
    frontend = _read(".env.production.frontend")
    rollout = _read("infra/session_prep_rollout.tf")

    assert (
        'VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED="${VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED:-false}"'
        in frontend
    )
    assert "VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED" not in rollout
    assert "azurerm_container_app" not in rollout
