from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "change-budget.yml"


def _workflow_source() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _budget_script() -> str:
    source = _workflow_source()
    marker = "        run: |\n"
    script = source[source.index(marker) + len(marker) :]
    return textwrap.dedent(script)


def _workflow_limit(name: str) -> str:
    prefix = f"      {name}: "
    line = next(
        line for line in _workflow_source().splitlines() if line.startswith(prefix)
    )
    return line.removeprefix(prefix).strip("'")


def _run_budget(
    tmp_path: Path,
    *,
    additions: str | None,
    deletions: str | None,
    changed_files: str | None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    for name, value in {
        "ADDITIONS": additions,
        "DELETIONS": deletions,
        "CHANGED_FILES": changed_files,
    }.items():
        if value is None:
            environment.pop(name, None)
        else:
            environment[name] = value
    environment.update(
        {
            "MAX_CHANGED_LINES": _workflow_limit("MAX_CHANGED_LINES"),
            "MAX_CHANGED_FILES": _workflow_limit("MAX_CHANGED_FILES"),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        }
    )
    return subprocess.run(
        ["bash", "-c", _budget_script()],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def test_change_budget_uses_only_trusted_event_metadata() -> None:
    source = _workflow_source()
    trigger = source[: source.index("permissions:")]

    assert "pull_request_target:" in trigger
    assert "\n  pull_request:" not in trigger
    assert "workflow_dispatch" not in trigger
    assert "permissions: {}" in source
    assert _workflow_limit("MAX_CHANGED_LINES") == "2000"
    assert _workflow_limit("MAX_CHANGED_FILES") == "50"
    assert "uses:" not in source
    assert "checkout" not in source
    assert "github.event.pull_request.head" not in source

    normalized = source.lower()
    for escape in ("label", "checkbox", "admin", "continue-on-error"):
        assert escape not in normalized


@pytest.mark.parametrize(
    ("additions", "deletions", "changed_files"),
    [
        ("2000", "0", "50"),
        ("1000", "1000", "50"),
        ("0", "0", "0"),
    ],
)
def test_change_budget_accepts_exact_boundaries(
    tmp_path: Path,
    additions: str,
    deletions: str,
    changed_files: str,
) -> None:
    result = _run_budget(
        tmp_path,
        additions=additions,
        deletions=deletions,
        changed_files=changed_files,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("additions", "deletions", "changed_files"),
    [
        ("2001", "0", "50"),
        ("1000", "1001", "50"),
        ("2000", "0", "51"),
    ],
)
def test_change_budget_rejects_over_limit_changes(
    tmp_path: Path,
    additions: str,
    deletions: str,
    changed_files: str,
) -> None:
    result = _run_budget(
        tmp_path,
        additions=additions,
        deletions=deletions,
        changed_files=changed_files,
    )

    assert result.returncode != 0
    assert "too large for one reliable review" in result.stdout


@pytest.mark.parametrize(
    ("additions", "deletions", "changed_files"),
    [
        (None, "0", "1"),
        ("1", "not-a-number", "1"),
        ("1", "0", "-1"),
    ],
)
def test_change_budget_fails_closed_for_invalid_event_metrics(
    tmp_path: Path,
    additions: str | None,
    deletions: str | None,
    changed_files: str | None,
) -> None:
    result = _run_budget(
        tmp_path,
        additions=additions,
        deletions=deletions,
        changed_files=changed_files,
    )

    assert result.returncode != 0
    assert "Missing or invalid pull-request metric" in result.stdout
