from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "squawk_migration_lint.sh"


def _run_lint(
    tmp_path: Path,
    *,
    alembic_status: int = 0,
    squawk_status: int = 0,
    squawk_report: object | None = None,
    raw_squawk_report: str | None = None,
    ci: bool = False,
    mktemp_fail_at: int = 0,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log = tmp_path / "calls.log"
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        """#!/usr/bin/env zsh
set -eu
print -r -- "$*" >> "$FAKE_CALL_LOG"
if [[ "$*" == "run alembic upgrade base:head --sql" ]]; then
    print -r -- 'CREATE TABLE example (id bigint);'
    exit "$FAKE_ALEMBIC_STATUS"
fi
if [[ "$*" == "run squawk --reporter=json --exclude=prefer-text-field" ]]; then
    print -rn -- "$FAKE_SQUAWK_REPORT"
    exit "$FAKE_SQUAWK_STATUS"
fi
print -r -- "unexpected fake uv invocation: $*" >&2
exit 64
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    fake_mktemp = fake_bin / "mktemp"
    fake_mktemp.write_text(
        """#!/usr/bin/env zsh
set -eu
count=0
if [[ -f "$FAKE_MKTEMP_COUNTER" ]]; then
    count=$(<"$FAKE_MKTEMP_COUNTER")
fi
count=$((count + 1))
print -r -- "$count" > "$FAKE_MKTEMP_COUNTER"
if [[ "$FAKE_MKTEMP_FAIL_AT" -eq "$count" ]]; then
    exit 42
fi
path="$FAKE_MKTEMP_ROOT/squawk-temp-$count"
: > "$path"
print -r -- "$path"
""",
        encoding="utf-8",
    )
    fake_mktemp.chmod(0o755)

    if raw_squawk_report is None:
        raw_squawk_report = json.dumps([] if squawk_report is None else squawk_report)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "FAKE_CALL_LOG": str(call_log),
            "FAKE_ALEMBIC_STATUS": str(alembic_status),
            "FAKE_SQUAWK_STATUS": str(squawk_status),
            "FAKE_SQUAWK_REPORT": raw_squawk_report,
            "FAKE_MKTEMP_COUNTER": str(tmp_path / "mktemp-count"),
            "FAKE_MKTEMP_FAIL_AT": str(mktemp_fail_at),
            "FAKE_MKTEMP_ROOT": str(tmp_path),
        }
    )
    if ci:
        env["CI"] = "true"
    else:
        env.pop("CI", None)
    result = subprocess.run(
        ["zsh", str(SCRIPT), "base"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = (
        call_log.read_text(encoding="utf-8").splitlines() if call_log.exists() else []
    )
    return result, calls


def _finding(level: str) -> dict[str, object]:
    return {
        "file": "stdin",
        "line": 1,
        "column": 0,
        "level": level,
        "messages": [{"Note": "synthetic migration finding"}],
        "rule_name": "synthetic-rule",
    }


def test_alembic_execution_failure_propagates_and_stops_before_squawk(
    tmp_path: Path,
) -> None:
    result, calls = _run_lint(tmp_path, alembic_status=37)

    assert result.returncode == 37
    assert calls == ["run alembic upgrade base:head --sql"]
    assert not list(tmp_path.glob("squawk-temp-*"))


def test_squawk_execution_failure_propagates(tmp_path: Path) -> None:
    result, calls = _run_lint(
        tmp_path,
        squawk_status=23,
        raw_squawk_report="",
    )

    assert result.returncode == 23
    assert calls == [
        "run alembic upgrade base:head --sql",
        "run squawk --reporter=json --exclude=prefer-text-field",
    ]
    assert "Squawk execution failed with status 23" in result.stderr
    assert not list(tmp_path.glob("squawk-temp-*"))


def test_second_temp_file_allocation_failure_cleans_up_the_first(
    tmp_path: Path,
) -> None:
    result, calls = _run_lint(tmp_path, mktemp_fail_at=2)

    assert result.returncode == 42
    assert calls == []
    assert not list(tmp_path.glob("squawk-temp-*"))


def test_squawk_status_one_without_findings_fails_closed(tmp_path: Path) -> None:
    result, _ = _run_lint(tmp_path, squawk_status=1, squawk_report=[])

    assert result.returncode == 1
    assert "Squawk failed without reporting a lint finding" in result.stderr


@pytest.mark.parametrize(
    "report",
    [
        "not-json",
        json.dumps([_finding("Info")]),
        json.dumps([{"level": "Warning"}]),
        json.dumps([{**_finding("Warning"), "file": ""}]),
        json.dumps([{**_finding("Warning"), "line": -1}]),
        json.dumps([{**_finding("Warning"), "column": 0.5}]),
        json.dumps([{**_finding("Warning"), "rule_name": ""}]),
        json.dumps(
            [
                {
                    **_finding("Warning"),
                    "messages": [
                        {"Note": "synthetic migration finding"},
                        {"Help": ""},
                    ],
                }
            ]
        ),
        json.dumps(
            [
                {
                    **_finding("Warning"),
                    "messages": [{"Help": "missing primary note"}],
                }
            ]
        ),
    ],
)
def test_invalid_or_unsupported_squawk_reports_fail_closed(
    tmp_path: Path, report: str
) -> None:
    result, _ = _run_lint(
        tmp_path,
        squawk_status=1,
        raw_squawk_report=report,
    )

    assert result.returncode == 1
    assert "valid supported JSON findings array" in result.stderr


def test_warning_findings_remain_advisory(tmp_path: Path) -> None:
    finding = _finding("Warning")
    finding["messages"] = [
        {"Note": "synthetic migration finding"},
        {"Help": "safe remediation"},
    ]
    result, _ = _run_lint(
        tmp_path,
        squawk_status=1,
        squawk_report=[finding],
    )

    assert result.returncode == 0
    assert "Warning [synthetic-rule] synthetic migration finding" in result.stdout
    assert not list(tmp_path.glob("squawk-temp-*"))


def test_ci_warning_uses_github_annotation_format(tmp_path: Path) -> None:
    result, _ = _run_lint(
        tmp_path,
        squawk_status=1,
        squawk_report=[_finding("Warning")],
        ci=True,
    )

    assert result.returncode == 0
    assert (
        "::warning file=stdin,line=1,col=0,title=synthetic-rule::"
        "synthetic migration finding"
    ) in result.stdout


def test_error_findings_fail_lint(tmp_path: Path) -> None:
    result, _ = _run_lint(
        tmp_path,
        squawk_status=1,
        squawk_report=[_finding("Error")],
    )

    assert result.returncode == 1
    assert "Error [synthetic-rule] synthetic migration finding" in result.stdout


def test_clean_schema_passes(tmp_path: Path) -> None:
    result, _ = _run_lint(tmp_path, squawk_status=0, squawk_report=[])

    assert result.returncode == 0
    assert result.stdout == ""
    assert not list(tmp_path.glob("squawk-temp-*"))
