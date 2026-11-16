from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/session_prep_production_frontend_admission.py"
WORKFLOW = ROOT / ".github/workflows/build_and_publish_local.yml"
FLAG = "VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED"
SOURCE = subprocess.check_output(
    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
).strip()


def configuration() -> dict[str, str]:
    # Non-routable fixture; no real account, recording or resolved credential.
    clerk = base64.urlsafe_b64encode(b"clerk.production.invalid$").decode().rstrip("=")
    return {
        "NODE_ENV": "production",
        "OPENAPI_JSON_PATH": "openapi.json",
        "VITE_CLERK_PUBLIC_KEY": f"pk_live_{clerk}",
        "VITE_INTAKE_CHAT_DOMAIN": "chat.cosmaneura.com",
        "VITE_POSTHOG_HOST": "https://analytics.invalid",
        "VITE_POSTHOG_KEY": "synthetic-public-key",
        "VITE_PYTHON_URL": "https://app.cosmaneura.com/",
        "VITE_SENTRY_DSN": "https://public@monitor.invalid/1",
        FLAG: "false",
        "VITE_SOVEREIGN_PROVIDER_URL": "https://sovereign.cosmaneura.com",
    }


def context() -> dict[str, str]:
    return {
        "PATH": f"{Path(sys.executable).parent}:{os.environ['PATH']}",
        "GITHUB_ACTOR": "JohnClem",
        "GITHUB_TRIGGERING_ACTOR": "JohnClem",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/master",
        "GITHUB_REPOSITORY": "CosmaNeura/cosmaneura",
        "GITHUB_WORKFLOW_REF": (
            "CosmaNeura/cosmaneura/.github/workflows/"
            "build_and_publish_local.yml@refs/heads/master"
        ),
        "GITHUB_SHA": SOURCE,
        "GITHUB_RUN_ID": "123456789",
    }


def prepare(
    directory: Path,
    *,
    config: dict[str, str] | None = None,
    enabled: str = "true",
    reviewed_sha: str = SOURCE,
    changes: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[bytes], Path, Path]:
    config_path, receipt_path = (
        directory / "frontend.json",
        directory / "admission.json",
    )
    config_path.write_text(json.dumps(config or configuration()))
    config_path.chmod(0o600)
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "prepare",
            "--configuration",
            str(config_path),
            "--admission",
            str(receipt_path),
            "--enabled",
            enabled,
            "--source-sha",
            SOURCE,
            "--reviewed-source-sha",
            reviewed_sha,
        ],
        cwd=ROOT,
        env={**context(), **(changes or {})},
        capture_output=True,
        check=False,
    )
    return result, config_path, receipt_path


def build_arguments(
    config_path: Path, receipt_path: Path | None, changes: dict[str, str] | None = None
) -> subprocess.CompletedProcess[bytes]:
    env = {
        **context(),
        "JAVASCRIPT_SECRETS_FILE": ".env.production.frontend",
        "JAVASCRIPT_BUILD_ENV_JSON": str(config_path),
        **(changes or {}),
    }
    if receipt_path is not None:
        env["JAVASCRIPT_SESSION_PREP_ADMISSION_JSON"] = str(receipt_path)
    return subprocess.run(
        ["just", "_javascript_build_env_args"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
    )


def assert_refused(result: subprocess.CompletedProcess[bytes]) -> None:
    assert result.returncode != 0
    assert result.stdout == b""
    assert b"production_session_prep_frontend_admission_refused" in result.stderr
    # A refusal never echoes public or private configuration values.
    assert b"synthetic-public-key" not in result.stderr
    assert b"monitor.invalid" not in result.stderr


def test_default_dark_needs_no_admission_or_founder_context(tmp_path: Path) -> None:
    result, config, receipt = prepare(
        tmp_path, enabled="false", reviewed_sha="", changes={"GITHUB_ACTOR": "Other"}
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"enabled": False}
    assert not receipt.exists()
    assert json.loads(config.read_bytes())[FLAG] == "false"
    assert build_arguments(config, None).returncode == 0


def test_explicit_admission_enables_only_allowlisted_public_builder_arguments(
    tmp_path: Path,
) -> None:
    result, config, receipt = prepare(tmp_path)
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["enabled"] is True and summary["source_sha"] == SOURCE
    assert b"synthetic-public-key" not in result.stdout
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert config.stat().st_mode & 0o777 == 0o600
    built = build_arguments(
        config,
        receipt,
        {"OP_SERVICE_ACCOUNT_TOKEN": "never-in-arguments", "GH_TOKEN": "also-never"},
    )
    assert built.returncode == 0, built.stderr
    records = built.stdout.split(b"\0")
    assert records[-1] == b""
    assert records[:-1:2] == [b"--env"] * 10
    values = dict(record.decode().split("=", 1) for record in records[1:-1:2])
    assert set(values) == set(configuration())
    assert values[FLAG] == "true"
    assert (
        b"never-in-arguments" not in built.stdout and b"also-never" not in built.stdout
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("GITHUB_ACTOR", "Other"),
        ("GITHUB_TRIGGERING_ACTOR", "Other"),
        ("GITHUB_EVENT_NAME", "push"),
        ("GITHUB_REF", "refs/heads/staging"),
        ("GITHUB_REPOSITORY", "Other/cosmaneura"),
        ("GITHUB_WORKFLOW_REF", "retired-workflow@refs/heads/master"),
        ("GITHUB_SHA", "a" * 40),
        ("GITHUB_RUN_ID", ""),
        ("GITHUB_RUN_ID", "123\n456"),
    ],
)
def test_untrusted_manual_context_cannot_admit_or_reuse(
    tmp_path: Path, key: str, value: str
) -> None:
    directory = tmp_path / "invalid"
    directory.mkdir()
    result, _, receipt = prepare(directory, changes={key: value})
    assert_refused(result)
    assert not receipt.exists()
    valid, config, receipt = prepare(tmp_path)
    assert valid.returncode == 0
    assert_refused(build_arguments(config, receipt, {key: value}))


@pytest.mark.parametrize("reviewed", ["", "a" * 40, SOURCE[:12], SOURCE.upper()])
def test_only_exact_reviewed_source_is_admitted(tmp_path: Path, reviewed: str) -> None:
    result, config, receipt = prepare(tmp_path, reviewed_sha=reviewed)
    assert_refused(result)
    assert json.loads(config.read_bytes())[FLAG] == "false"
    assert not receipt.exists()


@pytest.mark.parametrize("enabled", ["TRUE", "1", "", "true\n", " false"])
def test_dispatch_control_is_a_canonical_boolean(tmp_path: Path, enabled: str) -> None:
    result, _, receipt = prepare(tmp_path, enabled=enabled)
    assert_refused(result)
    assert not receipt.exists()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("NODE_ENV", "staging"),
        ("OPENAPI_JSON_PATH", "/tmp/openapi.json"),
        ("VITE_PYTHON_URL", "https://staging-app.cosmaneura.com/"),
        ("VITE_PYTHON_URL", "https://app.cosmaneura.com/?other=1"),
        ("VITE_INTAKE_CHAT_DOMAIN", "staging-chat.cosmaneura.com"),
        ("VITE_CLERK_PUBLIC_KEY", "pk_test_invented"),
        ("VITE_CLERK_PUBLIC_KEY", "pk_live_invalid"),
        ("VITE_SOVEREIGN_PROVIDER_URL", "https://public.cosmaneura.com"),
        ("VITE_SOVEREIGN_PROVIDER_URL", "https://staging-sovereign.cosmaneura.com"),
        ("VITE_SOVEREIGN_PROVIDER_URL", "https://sovereign.cosmaneura.com/"),
        ("VITE_SOVEREIGN_PROVIDER_URL", "https://user@sovereign.cosmaneura.com"),
        ("VITE_SOVEREIGN_PROVIDER_URL", "https://sovereign.cosmaneura.com:443"),
        ("VITE_SOVEREIGN_PROVIDER_URL", "https://sovereign.cosmaneura.com#other"),
        ("VITE_SOVEREIGN_PROVIDER_URL", " https://sovereign.cosmaneura.com"),
        ("VITE_POSTHOG_KEY", "op://vault/item/field"),
        (FLAG, "true"),
        (FLAG, "TRUE"),
    ],
)
def test_staging_public_or_unresolved_configuration_cannot_be_activated(
    tmp_path: Path, key: str, value: str
) -> None:
    result, _, receipt = prepare(tmp_path, config={**configuration(), key: value})
    assert_refused(result)
    assert not receipt.exists()


@pytest.mark.parametrize(
    "domain", ["clerk.staging.invalid", "instance.clerk.accounts.dev"]
)
def test_live_prefix_does_not_admit_a_staging_clerk_domain(
    tmp_path: Path, domain: str
) -> None:
    encoded = base64.urlsafe_b64encode(f"{domain}$".encode()).decode().rstrip("=")
    result, _, _ = prepare(
        tmp_path,
        config={**configuration(), "VITE_CLERK_PUBLIC_KEY": f"pk_live_{encoded}"},
    )
    assert_refused(result)


@pytest.mark.parametrize(
    "tamper", ["config", "context", "source", "extra", "bool", "float"]
)
def test_changed_configuration_or_receipt_cannot_enter_builder(
    tmp_path: Path, tamper: str
) -> None:
    result, config, receipt = prepare(tmp_path)
    assert result.returncode == 0
    if tamper == "config":
        value = json.loads(config.read_bytes())
        value["VITE_POSTHOG_KEY"] = "changed-public-key"
        config.write_text(json.dumps(value))
    else:
        value = json.loads(receipt.read_bytes())
        if tamper == "context":
            value["manual_workflow_context"]["GITHUB_RUN_ID"] = "987654321"
        elif tamper == "source":
            value["source_sha"] = "b" * 40
        elif tamper == "extra":
            value["claimed_approval"] = True
        elif tamper == "bool":
            value["version"] = True
        else:
            value["version"] = 1.0
        receipt.write_text(json.dumps(value))
    assert_refused(build_arguments(config, receipt))


def test_rehashed_origin_substitution_still_fails(tmp_path: Path) -> None:
    result, config, receipt = prepare(tmp_path)
    assert result.returncode == 0
    config_value = json.loads(config.read_bytes())
    config_value["VITE_SOVEREIGN_PROVIDER_URL"] = "https://public.cosmaneura.com"
    config.write_text(json.dumps(config_value))
    admission = json.loads(receipt.read_bytes())
    admission["public_configuration_sha256"] = hashlib.sha256(
        json.dumps(config_value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    receipt.write_text(json.dumps(admission))
    assert_refused(build_arguments(config, receipt))


@pytest.mark.parametrize("bad_file", ["missing", "link", "duplicate", "large"])
def test_missing_unbounded_or_ambiguous_receipt_is_refused(
    tmp_path: Path, bad_file: str
) -> None:
    result, config, receipt = prepare(tmp_path)
    assert result.returncode == 0
    if bad_file == "missing":
        receipt.unlink()
    elif bad_file == "link":
        original = tmp_path / "original.json"
        receipt.rename(original)
        receipt.symlink_to(original)
    elif bad_file == "duplicate":
        raw = receipt.read_text()
        receipt.write_text('{"version":1,' + raw[1:])
    else:
        receipt.write_bytes(b" " * 16385)
    assert_refused(build_arguments(config, receipt))


def test_true_without_receipt_or_with_extra_builder_key_is_refused(
    tmp_path: Path,
) -> None:
    result, config, receipt = prepare(tmp_path)
    assert result.returncode == 0
    assert_refused(build_arguments(config, None))
    value = json.loads(config.read_bytes())
    value["SECRET_KEY"] = "not-a-public-build-input"
    config.write_text(json.dumps(value))
    assert_refused(build_arguments(config, receipt))


@pytest.mark.parametrize("enabled", ["false", "true"])
@pytest.mark.parametrize("job", ["production-candidate", "deploy"])
def test_actual_workflow_resolver_projects_dark_then_admits_explicit_choice(
    tmp_path: Path, enabled: str, job: str
) -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    resolver = next(
        step
        for step in workflow["jobs"][job]["steps"]
        if step.get("name", "").startswith(
            ("Resolve production frontend", "Resolve fresh production frontend")
        )
    )
    # Substitute only the secret resolver command. Execute the tracked shell,
    # real jq projection, helper and subsequent real build-argument recipe.
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    stub = stub_bin / "just"
    stub.write_text('#!/bin/sh\ncat "$STUB_FRONTEND_EXPORT"\n')
    stub.chmod(0o700)
    exported = tmp_path / "export.json"
    exported.write_text(
        json.dumps({**configuration(), FLAG: "true", "SECRET_KEY": "excluded"})
    )
    config, receipt = tmp_path / "resolved.json", tmp_path / "admission.json"
    env = {
        **context(),
        "PATH": f"{stub_bin}:{context()['PATH']}",
        "STUB_FRONTEND_EXPORT": str(exported),
        "FRONTEND_ENV_SOURCE": ".env.production.frontend",
        "JAVASCRIPT_BUILD_ENV_JSON": str(config),
        "JAVASCRIPT_SESSION_PREP_ADMISSION_JSON": str(receipt),
        "SESSION_PREP_FRONTEND_ENABLED": enabled,
        "SESSION_PREP_REVIEWED_SOURCE_SHA": SOURCE if enabled == "true" else "",
    }
    resolved = subprocess.run(
        ["bash", "-c", resolver["run"]],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
    )
    assert resolved.returncode == 0, resolved.stderr
    value = json.loads(config.read_bytes())
    assert set(value) == set(configuration()) and value[FLAG] == enabled
    assert receipt.exists() is (enabled == "true")
    built = build_arguments(config, receipt if receipt.exists() else None)
    assert built.returncode == 0, built.stderr
    assert b"SECRET_KEY" not in built.stdout and b"excluded" not in built.stdout


@pytest.mark.parametrize("ambiguous", ["duplicate", "link", "deep"])
def test_ambiguous_or_linked_configuration_is_refused_before_preparation(
    tmp_path: Path, ambiguous: str
) -> None:
    config, receipt = tmp_path / "frontend.json", tmp_path / "admission.json"
    raw = json.dumps(configuration())
    if ambiguous == "duplicate":
        config.write_text('{"NODE_ENV":"production",' + raw[1:])
    elif ambiguous == "deep":
        config.write_text("[" * 2000 + "0" + "]" * 2000)
    else:
        original = tmp_path / "original.json"
        original.write_text(raw)
        config.symlink_to(original)
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "prepare",
            "--configuration",
            str(config),
            "--admission",
            str(receipt),
            "--enabled",
            "true",
            "--source-sha",
            SOURCE,
            "--reviewed-source-sha",
            SOURCE,
        ],
        cwd=ROOT,
        env=context(),
        capture_output=True,
        check=False,
    )
    assert_refused(result)
    assert not receipt.exists()


def test_manual_workflow_keeps_existing_gates_and_default_dark_projection() -> None:
    source = WORKFLOW.read_text()
    assert source.count("scripts/superadmin_capability_config_preflight.py; then") == 2
    workflow = yaml.safe_load(source)
    dispatch = workflow.get("on", workflow.get(True))["workflow_dispatch"]["inputs"]
    assert dispatch["production_session_prep_frontend_enabled"]["default"] is False
    assert dispatch["production_session_prep_reviewed_core_sha"]["default"] == ""
    deploy = workflow["jobs"]["deploy"]
    assert deploy["environment"] == "production"
    assert all(
        expected in deploy["if"]
        for expected in (
            "github.event_name == 'workflow_dispatch'",
            "github.ref == 'refs/heads/master'",
            "github.actor == 'JohnClem'",
            "github.triggering_actor == 'JohnClem'",
            "needs.release-gates.result == 'success'",
        )
    )
    steps = deploy["steps"]
    resolver = next(
        s
        for s in steps
        if s.get("name")
        == "Resolve fresh production frontend configuration for comparison"
    )
    builder = next(
        s
        for s in workflow["jobs"]["production-candidate"]["steps"]
        if s.get("name") == "Build the combined production frontend and Core image"
    )
    cleanup = next(
        s
        for s in steps
        if s.get("name") == "Remove resolved production frontend configuration"
    )
    assert f'{FLAG}: "false"' in resolver["run"]
    assert " prepare " in resolver["run"]
    assert "JAVASCRIPT_SESSION_PREP_ADMISSION_JSON" in builder["env"]
    assert "SESSION_PREP_FRONTEND_ENABLED" not in builder["env"]
    assert "OP_SERVICE_ACCOUNT_TOKEN" not in builder["env"]
    assert cleanup["if"] == "always()"
    assert "$RUNNER_TEMP/production-session-prep-admission.json" in cleanup["run"]
    assert (
        "scripts/session_prep_production_frontend_admission.py"
        in source[
            source.index("git diff --quiet") : source.index(
                "Production infrastructure changed"
            )
        ]
    )
    retired = (ROOT / ".github/workflows/production_release_verified.yml").read_text()
    assert f'{FLAG}: "false"' in retired
