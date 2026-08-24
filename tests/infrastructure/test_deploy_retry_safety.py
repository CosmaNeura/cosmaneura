from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "deploy_revision_suffix.sh"
SHA = "72dee94186283eabb736e787575b4c2a5e1ce32e"
CONTAINERS = (
    "cosmaneura-staging-worker",
    "cosmaneura-staging-scheduler",
    "cosmaneura-staging-app",
)


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
