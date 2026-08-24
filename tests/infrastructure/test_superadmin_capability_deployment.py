from __future__ import annotations

import base64
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "superadmin_capability_config_preflight.py"


def _load_preflight() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "standalone_superadmin_capability_config_preflight",
        SCRIPT,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _publishable_key(*, live: bool, host: str) -> str:
    encoded = base64.urlsafe_b64encode(f"{host}$".encode()).decode().rstrip("=")
    return f"pk_{'live' if live else 'test'}_{encoded}"


@pytest.mark.parametrize(
    ("environment", "live", "private_prefix"),
    [("staging", False, "sk_test_"), ("production", True, "sk_live_")],
)
def test_preflight_accepts_only_matching_environment_scoped_clerk_instance(
    environment: str,
    live: bool,
    private_prefix: str,
) -> None:
    preflight = _load_preflight()
    host = f"{environment}-instance.example.invalid"
    issuer = f"https://{host}"
    payload = {
        "PYTHON_ENV": environment,
        "AUTHORITY_CLERK_ISSUER": issuer,
        "CLERK_DEPLOYMENT_PUBLISHABLE_KEY": _publishable_key(
            live=live,
            host=host,
        ),
        "CLERK_PRIVATE_KEY": f"{private_prefix}private",
    }

    preflight.validate_resolved_configuration(payload, environment=environment)

    for field, replacement in (
        ("AUTHORITY_CLERK_ISSUER", ""),
        ("AUTHORITY_CLERK_ISSUER", "https://different.example.invalid"),
        ("PYTHON_ENV", "test"),
        (
            "CLERK_DEPLOYMENT_PUBLISHABLE_KEY",
            _publishable_key(live=not live, host=host),
        ),
        ("CLERK_PRIVATE_KEY", "sk_test_wrong" if live else "sk_live_wrong"),
    ):
        invalid = dict(payload)
        invalid[field] = replacement
        with pytest.raises(preflight.ConfigurationError):
            preflight.validate_resolved_configuration(
                invalid,
                environment=environment,
            )


def test_cli_failure_is_value_minimized() -> None:
    secret_marker = "must-not-appear"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--environment", "production"],
        input=json.dumps(
            {
                "PYTHON_ENV": "production",
                "AUTHORITY_CLERK_ISSUER": "",
                "CLERK_DEPLOYMENT_PUBLISHABLE_KEY": secret_marker,
                "CLERK_PRIVATE_KEY": secret_marker,
            }
        ),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert secret_marker not in result.stderr


def test_deployments_validate_capability_identity_before_provider_calls() -> None:
    for relative, environment in (
        ("infra/deploy.sh", "production"),
        ("infra-staging/deploy.sh", "staging"),
    ):
        source = (ROOT / relative).read_text()
        validation = source.index("superadmin_capability_config_preflight.py")
        first_provider_call = source.index("az account show")
        assert validation < first_provider_call
        assert f"--environment {environment}" in source


def test_runtime_issuer_is_derived_and_propagated_without_hard_coding() -> None:
    production = (ROOT / ".env.production.backend").read_text()
    staging = (ROOT / ".env.staging.backend").read_text()
    for source, environment in (
        (production, "production"),
        (staging, "staging"),
    ):
        assert "CLERK_DEPLOYMENT_PUBLISHABLE_KEY='op://" in source
        assert 'AUTHORITY_CLERK_ISSUER="$(' in source
        assert f"--environment {environment} --derive-issuer" in source
        authority_assignment = next(
            line
            for line in source.splitlines()
            if line.startswith("export AUTHORITY_CLERK_ISSUER=")
        )
        assert "https://" not in authority_assignment

    production_variables = (ROOT / "infra" / "variables.tf").read_text()
    assert '"AUTHORITY_CLERK_ISSUER"' in production_variables
