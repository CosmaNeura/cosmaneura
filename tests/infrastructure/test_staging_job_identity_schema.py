"""Synthetic checks for the local schema harness; no provider or Azure calls."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts import check_staging_job_identity_schema as checker


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    source = tmp_path / "fixture.tf"
    source.write_text('schema_validation_enabled = true\nlifecycle = "None"\n')
    (tmp_path / ".terraform.lock.hcl").write_bytes(b"synthetic-pinned-lock\n")
    (tmp_path / ".terraform/providers").mkdir(parents=True)
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    monkeypatch.setattr(checker, "FIXTURE", source)
    monkeypatch.setattr(checker.shutil, "which", lambda _name: "/fixed/tofu")
    results = [
        (0, {"terraform_version": "1.12.5"}),
        (0, {}),
        (
            1,
            {
                "valid": False,
                "error_count": 1,
                "diagnostics": [
                    {
                        "severity": "error",
                        "detail": "embedded schema validation failed: properties.configuration.identitySettings.0.lifecycle has invalid value",
                    }
                ],
            },
        ),
        (0, {"valid": True, "error_count": 0}),
    ]
    state: dict[str, Any] = {"results": results, "calls": [], "alter_lock": False}

    def fake_run(
        binary: str, directory: Path, arguments: list[str]
    ) -> subprocess.CompletedProcess[str]:
        index = len(state["calls"])
        state["calls"].append((binary, list(arguments)))
        copied = (directory / "main.tf").read_text()
        assert ("lifecycle = true" in copied) is (index < 3)
        if state["alter_lock"] and index == 3:
            (directory / ".terraform.lock.hcl").write_bytes(b"changed\n")
        code, report = state["results"][index]
        output = report if isinstance(report, str) else json.dumps(report)
        return subprocess.CompletedProcess(arguments, code, stdout=output, stderr="")

    monkeypatch.setattr(checker, "_run", fake_run)
    return state


def test_main_only_runs_version_init_and_validation(
    harness: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    checker.main()
    assert harness["calls"] == [
        ("/fixed/tofu", ["version", "-json"]),
        (
            "/fixed/tofu",
            [
                "init",
                "-backend=false",
                "-input=false",
                "-lockfile=readonly",
                "-no-color",
                f"-plugin-dir={checker.ROOT / '.terraform/providers'}",
            ],
        ),
        ("/fixed/tofu", ["validate", "-json"]),
        ("/fixed/tofu", ["validate", "-json"]),
    ]
    report = json.loads(capsys.readouterr().out)
    assert report["boolean_lifecycle_rejected"] is True
    assert report["none_lifecycle_schema_valid"] is True
    for key in (
        "azure_preflight",
        "backend_initialized",
        "live_job_support_proven",
        "runtime_acceptance",
        "activation_authorized",
    ):
        assert report[key] is False


@pytest.mark.parametrize(
    ("index", "replacement"),
    [
        (0, (0, {"terraform_version": "1.12.4"})),
        (0, (1, {"terraform_version": "1.12.5"})),
        (1, (1, "synthetic provider install failure")),
        (2, (0, {"valid": True})),
        (2, (1, {"valid": "false", "diagnostics": []})),
        (
            2,
            (
                1,
                {
                    "valid": False,
                    "diagnostics": [
                        {"severity": "error", "detail": "unrelated bool field"}
                    ],
                },
            ),
        ),
        (
            2,
            (
                1,
                {
                    "valid": False,
                    "diagnostics": [
                        {"severity": "warning", "detail": "lifecycle bool"}
                    ],
                },
            ),
        ),
        (
            2,
            (
                1,
                {
                    "valid": False,
                    "diagnostics": [
                        {"severity": "error", "detail": "lifecycle API unavailable"}
                    ],
                },
            ),
        ),
        (2, (1, "not-json")),
        (2, (1, [])),
        (3, (1, {"valid": False, "error_count": 1})),
        (3, (0, {"valid": True, "error_count": 1})),
        (3, (0, {"valid": "true", "error_count": 0})),
        (3, (0, "not-json")),
    ],
)
def test_failure_cannot_be_reported_as_schema_success(
    harness: dict[str, Any], index: int, replacement: tuple[int, Any]
) -> None:
    harness["results"][index] = replacement
    with pytest.raises(RuntimeError):
        checker.main()


def test_lockfile_must_remain_exact(harness: dict[str, Any]) -> None:
    harness["alter_lock"] = True
    with pytest.raises(RuntimeError, match="changed the pinned lockfile"):
        checker.main()


@pytest.mark.parametrize(
    "source",
    [
        'schema_validation_enabled = true\nlifecycle = "All"\n',
        'schema_validation_enabled = false\nlifecycle = "None"\n',
        'schema_validation_enabled = true\nlifecycle = "None"\nlifecycle = "None"\n',
    ],
)
def test_fixture_invariants_checked_before_commands(
    harness: dict[str, Any], source: str
) -> None:
    checker.FIXTURE.write_text(source)
    with pytest.raises(RuntimeError):
        checker.main()
    assert not harness["calls"]


def test_missing_tofu_fails_before_commands(
    harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(checker.shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="required"):
        checker.main()
    assert not harness["calls"]


def test_subprocess_environment_is_credential_neutral(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    observed: dict[str, Any] = {}
    monkeypatch.setenv("ARM_CLIENT_SECRET", "synthetic-do-not-forward")
    monkeypatch.setenv("TF_CLI_ARGS", "synthetic-do-not-forward")
    monkeypatch.setenv("HTTPS_PROXY", "synthetic-do-not-forward")

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        observed.update(kwargs)
        observed["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr(checker.subprocess, "run", fake_run)
    checker._run("/fixed/tofu", tmp_path, ["validate", "-json"])
    assert observed["command"] == [
        "/fixed/tofu",
        f"-chdir={tmp_path}",
        "validate",
        "-json",
    ]
    assert set(observed["env"]) == {
        "PATH",
        "TF_IN_AUTOMATION",
        "TF_INPUT",
        "TF_DATA_DIR",
    }
    assert observed["env"]["TF_DATA_DIR"] == str(tmp_path / "provider-data")
    assert observed["timeout"] == 300
    assert observed["capture_output"] is True
    assert observed["check"] is False
