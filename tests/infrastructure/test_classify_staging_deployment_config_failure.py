"""Synthetic-only classification of staging deploy config-load failures."""

from __future__ import annotations

import os
import re
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/classify_staging_deployment_config_failure.py"
ENV_FILE = ROOT / ".env.staging.backend"
DEPLOY = ROOT / "infra-staging/deploy.sh"
MODULE = runpy.run_path(str(SCRIPT))

REASON_CODES = (
    "op_rate_limited",
    "op_auth_failed",
    "op_network",
    "op_ref_not_found",
    "op_field_not_found",
    "op_other",
    "guard_failed",
    "unclassified",
)

SYNTHETIC_SAMPLES = {
    "op_rate_limited": (
        "[ERROR] 1Password CLI: too many requests (429); service account rate limited\n"
        "export RECALL_WEBHOOK_SECRET=sk_test_SYNTHETIC123\n"
        "op://vault/item/field\n"
    ),
    "op_auth_failed": (
        "[ERROR] you are not currently signed in; unauthorized service account token\n"
        "OP_SERVICE_ACCOUNT_TOKEN=ops_synthetic_token_never_log\n"
        "op://a747na7sgxngfa7gczsqqwekfq/udnuqwjwscfcmyeo66dxrllxyi/password\n"
    ),
    "op_network": (
        "[ERROR] dial tcp: connection refused; TLS handshake failure; gateway timeout 504\n"
        "DATABASE_URL=postgresql://synthetic:sk_test_SYNTHETIC123@db.invalid/synthetic\n"
    ),
    "op_ref_not_found": (
        "[ERROR] could not find item 'missing-item' in vault; item not found\n"
        "config_key=RECALL_WEBHOOK_SECRET\n"
        "value=sk_test_SYNTHETIC123 op://vault/item/field\n"
    ),
    "op_field_not_found": (
        "[ERROR] field not found: credential\n"
        "config_key=RECALL_API_KEY\n"
        "secret=sk_test_SYNTHETIC123\n"
    ),
    "op_other": (
        "[ERROR] op inject failed for an unrecognized 1Password CLI reason\n"
        "config_key=SESSION_SECRET_KEY\n"
        "token=sk_test_SYNTHETIC123 op://vault/item/field\n"
    ),
    "guard_failed": (
        "Core CAB runtime configuration is invalid.\n"
        "guard=core_cab_runtime_config\n"
        "leak=sk_test_SYNTHETIC123 op://vault/item/field\n"
    ),
    "unclassified": (
        "completely opaque synthetic failure without provider markers\n"
        "sk_test_SYNTHETIC123\n"
        "password=never-emit-this-value\n"
    ),
}


def _environment() -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", os.defpath), "LC_ALL": "C"}


def _write_stderr(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "stderr.txt"
    path.write_text(text)
    path.chmod(0o600)
    return path


def _classify_file(
    tmp_path: Path, stderr: str, env_file: Path = ENV_FILE
) -> subprocess.CompletedProcess[str]:
    stderr_path = _write_stderr(tmp_path, stderr)
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            str(SCRIPT),
            "--env-file",
            str(env_file),
            "--stderr-file",
            str(stderr_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=_environment(),
        timeout=10,
    )


@pytest.mark.parametrize("reason", REASON_CODES)
def test_each_reason_code_from_synthetic_stderr(tmp_path, reason):
    result = _classify_file(tmp_path, SYNTHETIC_SAMPLES[reason])
    assert result.returncode == 0
    assert result.stderr == ""
    line = result.stdout.strip()
    assert line.startswith(f"reason={reason}")
    assert "sk_test_SYNTHETIC123" not in result.stdout
    assert "op://" not in result.stdout
    assert "ops_synthetic_token" not in result.stdout
    assert "postgresql://" not in result.stdout
    assert "never-emit-this-value" not in result.stdout
    assert "password=" not in result.stdout


def test_secret_looking_stderr_never_appears_in_output(tmp_path):
    stderr = (
        "export AZURE_OPENAI_API_KEY=sk_test_SYNTHETIC123\n"
        "op://vault/item/field\n"
        "password=super-secret-value-never-emit\n"
        "[ERROR] item not found for RECALL_WEBHOOK_SECRET\n"
        "config_key=RECALL_WEBHOOK_SECRET\n"
    )
    result = _classify_file(tmp_path, stderr)
    output = result.stdout + result.stderr
    assert result.returncode == 0
    assert output.strip() == "reason=op_ref_not_found key=RECALL_WEBHOOK_SECRET"
    assert "sk_test_SYNTHETIC123" not in output
    assert "op://" not in output
    assert "super-secret-value" not in output
    assert "password=" not in output


def test_key_extraction_rejects_names_absent_from_env_file(tmp_path):
    env_file = tmp_path / "env.backend"
    env_file.write_text(
        "export DATABASE_URL='synthetic'\nexport REDIS_URL='synthetic'\n"
    )
    stderr = (
        "[ERROR] item not found\n"
        "config_key=NOT_IN_STAGING_BACKEND\n"
        "export MISSING_FROM_FILE=sk_test_SYNTHETIC123\n"
    )
    result = _classify_file(tmp_path, stderr, env_file=env_file)
    assert result.returncode == 0
    assert result.stdout.strip() == "reason=op_ref_not_found"
    assert "NOT_IN_STAGING_BACKEND" not in result.stdout
    assert "MISSING_FROM_FILE" not in result.stdout
    assert "sk_test_SYNTHETIC123" not in result.stdout


def test_key_extraction_accepts_allowlisted_export_names(tmp_path):
    stderr = (
        "[ERROR] field not found\nconfig_key=RECALL_API_KEY\nsk_test_SYNTHETIC123\n"
    )
    result = _classify_file(tmp_path, stderr)
    assert result.stdout.strip() == "reason=op_field_not_found key=RECALL_API_KEY"


def test_guard_failed_emits_stable_guard_name(tmp_path):
    stderr = (
        "guard=core_cab_runtime_config\n"
        "Core CAB runtime configuration is invalid.\n"
        "sk_test_SYNTHETIC123\n"
    )
    result = _classify_file(tmp_path, stderr)
    assert result.stdout.strip() == (
        "reason=guard_failed guard=core_cab_runtime_config"
    )


def test_module_classify_matches_cli_closed_set():
    allowed = MODULE["_allowed_keys"](ENV_FILE)
    for reason, sample in SYNTHETIC_SAMPLES.items():
        line = MODULE["classify"](sample, allowed)
        assert line.startswith(f"reason={reason}")
        assert re.fullmatch(
            r"reason=(?:"
            + "|".join(REASON_CODES)
            + r")(?: (?:key=[A-Z][A-Z0-9_]{1,80}|guard=[a-z][a-z0-9_]{1,80}))?",
            line,
        )


def test_deploy_failure_path_classifies_without_leaking_stderr(tmp_path):
    source = DEPLOY.read_text()
    start = source.index("CONFIGURATION_STATUS=0\n")
    block = source[start : source.index("if ! printf '%s'", start)]
    calls = tmp_path / "resolver-calls"
    program = f"""
REPOSITORY_ROOT={ROOT.as_posix()!r}
PYTHON_BIN={sys.executable!r}
just() {{
    printf 'called\\n' >> {calls.as_posix()!r}
    printf '%s\\n' '[ERROR] 429 too many requests' >&2
    printf '%s\\n' 'config_key=RECALL_WEBHOOK_SECRET' >&2
    printf '%s\\n' 'sk_test_SYNTHETIC123' >&2
    printf '%s\\n' 'op://vault/item/field' >&2
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
        timeout=10,
    )
    assert result.returncode == 1
    assert calls.read_text() == "called\n"
    assert result.stdout == (
        "Failed to load staging deployment configuration: "
        "reason=op_rate_limited key=RECALL_WEBHOOK_SECRET\n"
    )
    assert result.stderr == ""
    assert "sk_test_SYNTHETIC123" not in result.stdout
    assert "op://" not in result.stdout


def test_success_path_leaves_stdout_empty_for_config_block(tmp_path):
    source = DEPLOY.read_text()
    start = source.index("CONFIGURATION_STATUS=0\n")
    block = source[start : source.index("if ! printf '%s'", start)]
    program = f"""
REPOSITORY_ROOT={ROOT.as_posix()!r}
PYTHON_BIN={sys.executable!r}
just() {{
    printf '%s' '{{"PYTHON_ENV":"staging"}}'
    return 0
}}
{block}
printf 'SUCCESS:%s\\n' "$ENV_JSON"
"""
    result = subprocess.run(
        ["bash", "-c", program],
        env=_environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == 'SUCCESS:{"PYTHON_ENV":"staging"}\n'
    assert "Failed to load staging deployment configuration" not in result.stdout


def test_failure_markers_absent_from_success_sourcing_path():
    # Markers exist only inside failure branches; success sourcing must not
    # print them. Prove by checking each marker is preceded by a failing arm.
    source = ENV_FILE.read_text()
    for marker in (
        'echo "guard=staging_deployment_database_tls" >&2',
        'echo "config_key=DATABASE_URL" >&2',
        'echo "guard=core_cab_runtime_config" >&2',
        'echo "guard=stripe_billing_item" >&2',
        'echo "guard=staging_recall_api_key_live_guard" >&2',
        'echo "config_key=SOVEREIGN_RECORD_GRANT_SECRET" >&2',
    ):
        assert marker in source
        index = source.index(marker)
        window = source[max(0, index - 240) : index]
        assert "if !" in window or "if [[" in window or "case " in window
