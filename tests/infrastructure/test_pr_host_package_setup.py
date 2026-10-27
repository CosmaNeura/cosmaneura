"""Execute host-package setup with inert commands; never invoke apt or sudo."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def _run(tmp_path: Path, event: str, **overrides: str):
    action = yaml.safe_load(
        (ROOT / ".github/actions/common-setup/action.yml").read_text()
    )
    step = next(
        step
        for step in action["runs"]["steps"]
        if "APT_PACKAGES" in step.get("env", {})
    )
    commands = tmp_path / "commands"
    commands.mkdir()
    calls = tmp_path / "apt-calls"
    for name, source in {
        "dpkg-query": """#!/bin/bash
if [[ ${QUERY_FAIL:-} == yes || ${!#} == "${MISSING_PACKAGE:-}" ]]; then exit 1; fi
printf '%s' "${PACKAGE_STATUS:-install ok installed}"
""",
        "sudo": """#!/bin/bash
printf '%s\\n' "$*" >> "$APT_CALLS"
if [[ ${FAIL_UPDATE:-} == yes && "$*" == *" update "* ]]; then exit 42; fi
""",
    }.items():
        path = commands / name
        path.write_text(source)
        path.chmod(0o700)
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", step["run"]],
        env={
            "PATH": f"{commands}:{os.defpath}",
            "GITHUB_EVENT_NAME": event,
            "APT_PACKAGES": "zsh libnss3-tools",
            "APT_OPTS": "-o Acquire::Retries=3",
            "APT_CALLS": str(calls),
            **overrides,
        },
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result, calls.read_text().splitlines() if calls.exists() else []


def test_pr_uses_installed_packages_without_contacting_apt(tmp_path: Path):
    result, calls = _run(tmp_path, "pull_request")
    assert result.returncode == 0, result.stderr
    assert calls == []


@pytest.mark.parametrize("event", ["workflow_dispatch", "push"])
def test_non_pr_setup_retains_update_and_install(tmp_path: Path, event: str):
    result, calls = _run(tmp_path, event)
    assert result.returncode == 0, result.stderr
    assert calls == [
        "apt-get -o Acquire::Retries=3 update -qq",
        "apt-get -o Acquire::Retries=3 install -y --no-install-recommends zsh libnss3-tools",
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {"MISSING_PACKAGE": "libnss3-tools"},
        {"QUERY_FAIL": "yes"},
        {"PACKAGE_STATUS": "deinstall ok config-files"},
    ],
)
def test_pr_falls_back_when_any_package_is_not_confirmed_installed(
    tmp_path: Path, overrides: dict[str, str]
):
    result, calls = _run(tmp_path, "pull_request", **overrides)
    assert result.returncode == 0, result.stderr
    assert len(calls) == 2
    assert calls[0].endswith(" update -qq")
    assert calls[1].endswith(" zsh libnss3-tools")


def test_failed_update_is_not_ignored_or_followed_by_install(tmp_path: Path):
    result, calls = _run(tmp_path, "pull_request", QUERY_FAIL="yes", FAIL_UPDATE="yes")
    assert result.returncode == 42
    assert len(calls) == 1 and calls[0].endswith(" update -qq")


def test_frontend_requests_only_its_host_dependency():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/pull_request_checks.yml").read_text()
    )
    setup = next(
        step
        for step in workflow["jobs"]["web"]["steps"]
        if step.get("uses") == "./.github/actions/common-setup"
    )
    assert setup["with"]["apt-packages"] == "zsh"
