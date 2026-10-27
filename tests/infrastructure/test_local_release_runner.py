import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "local_release_runner", ROOT / "scripts/local_release_runner.py"
)
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_every_tracked_workflow_requires_explicit_local_compute() -> None:
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        workflow = yaml.safe_load(path.read_text())
        for job in workflow.get("jobs", {}).values():
            if "runs-on" in job:
                labels = job["runs-on"]
                assert isinstance(labels, list), path
                expected = (
                    "cosmaneura-production-local"
                    if f".github/workflows/{path.name}" in runner.WORKFLOWS
                    else "cosmaneura-validation-local"
                )
                assert labels == ["self-hosted", "Linux", "X64", expected], path


@pytest.mark.parametrize(
    "field,value",
    [
        ("event", "push"),
        ("event", "pull_request"),
        ("head_branch", "staging"),
        ("head_sha", "b" * 40),
        ("path", ".github/workflows/pull_request_checks.yml"),
        ("actor", {"login": "other"}),
        ("triggering_actor", {"login": "other"}),
        ("status", "completed"),
    ],
)
def test_invalid_release_never_registers_or_starts_a_runner(
    monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    run = {
        "event": "workflow_dispatch",
        "head_branch": "master",
        "head_sha": "a" * 40,
        "path": ".github/workflows/infra_apply_local.yml",
        "actor": {"login": "JohnClem"},
        "triggering_actor": {"login": "JohnClem"},
        "status": "queued",
        field: value,
    }
    reads = {
        "user": {"login": "JohnClem"},
        f"repos/{runner.REPOSITORY}/git/ref/heads/master": {
            "object": {"sha": "a" * 40}
        },
        f"repos/{runner.REPOSITORY}/actions/runs/123": run,
    }

    def only_allowed_reads(path: str, body: dict | None = None) -> dict:
        assert body is None, "Refused runs must never request JIT credentials."
        return reads[path]

    monkeypatch.setattr(runner, "github", only_allowed_reads)
    monkeypatch.setattr(
        sys,
        "argv",
        ["runner", "123", "--image", "sha256:" + "c" * 64],
    )
    with pytest.raises(SystemExit, match="founder-authorized"):
        runner.main()
