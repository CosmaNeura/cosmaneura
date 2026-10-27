import hashlib
import importlib.util
import json
import subprocess
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
services_spec = importlib.util.spec_from_file_location(
    "local_ci_services", ROOT / "scripts/local_ci_services.py"
)
assert services_spec is not None and services_spec.loader is not None
services = importlib.util.module_from_spec(services_spec)
services_spec.loader.exec_module(services)


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
                expected_labels = ["self-hosted", "Linux", "X64", expected]
                if expected == "cosmaneura-production-local":
                    expected_labels.append(
                        "cosmaneura-production-local-${{ github.run_id }}"
                    )
                assert labels == expected_labels, path


def test_dependabot_schedule_is_preserved_but_cannot_start_hosted_jobs() -> None:
    assert not (ROOT / ".github/dependabot.yml").exists()
    paused = yaml.safe_load((ROOT / ".github/dependabot.paused.yml").read_text())
    assert paused["version"] == 2
    assert {
        (update["package-ecosystem"], update["directory"])
        for update in paused["updates"]
    } == {("github-actions", "/"), ("pip", "/"), ("npm", "/web")}


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


@pytest.mark.parametrize("fault", ["network-name", "network-owner", "attached-project"])
def test_cleanup_validates_ownership_before_removing_attached_resources(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    name = "cosma-local-runner-" + "a" * 16
    project = "cosma-local-tests-" + hashlib.sha256(name.encode()).hexdigest()[:16]
    metadata = {
        "Name": "unrelated" if fault == "network-name" else project,
        "Labels": {
            "cosmaneura.local-runner": "other" if fault == "network-owner" else name
        },
        "Containers": {"attached": {}},
    }
    removed = []

    def output(command: list[str], **kwargs: object) -> str:
        if command[:3] == ["docker", "network", "ls"]:
            return "network\n"
        if command[:3] == ["docker", "network", "inspect"]:
            return json.dumps([metadata])
        if command[:2] == ["docker", "inspect"]:
            return json.dumps(
                [{"Config": {"Labels": {"com.docker.compose.project": "unrelated"}}}]
            )
        raise AssertionError(f"Cleanup proceeded beyond the ownership gate: {command}")

    def mutation(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        removed.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runner.subprocess, "check_output", output)
    monkeypatch.setattr(runner.subprocess, "run", mutation)
    with pytest.raises(SystemExit, match="Refusing cleanup"):
        runner.cleanup(name)
    assert removed == [["docker", "rm", "--force", name]]


def test_concurrent_network_claim_rescans_after_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims = []

    def inventory(*args: str) -> str:
        if args == ("network", "ls", "-q"):
            return "network"
        configs = [{"Subnet": "10.252.1.0/24"}] if claims else []
        return json.dumps([{"IPAM": {"Config": configs}}])

    def claim(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        claims.append(command[command.index("--subnet") + 1])
        if len(claims) == 1:
            return subprocess.CompletedProcess(
                command, 1, stderr="Pool overlaps with other one on this address space"
            )
        return subprocess.CompletedProcess(command, 0, stderr="")

    monkeypatch.setattr(services, "docker", inventory)
    monkeypatch.setattr(services.subprocess, "run", claim)
    services.create_network("project", "runner")
    assert claims == ["10.252.1.0/24", "10.252.2.0/24"]


def test_network_creation_does_not_retry_other_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def inventory(*args: str) -> str:
        return (
            "network"
            if args == ("network", "ls", "-q")
            else '[{"IPAM":{"Config":null}}]'
        )

    def claim(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, stderr="permission denied")

    monkeypatch.setattr(services, "docker", inventory)
    monkeypatch.setattr(services.subprocess, "run", claim)
    with pytest.raises(SystemExit, match="non-overlap"):
        services.create_network("project", "runner")
    assert len(calls) == 1
