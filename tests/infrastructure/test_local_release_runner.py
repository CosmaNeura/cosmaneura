import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

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


def test_admission_hook_uses_runner_supported_script_extension() -> None:
    # Runner JobHookProvider refuses extensionless files before executing them.
    dockerfile = (ROOT / "scripts/local-runner/Dockerfile").read_text()
    hook = next(
        line.split("=", 1)[1]
        for line in dockerfile.splitlines()
        if line.startswith("ENV ACTIONS_RUNNER_HOOK_JOB_STARTED=")
    )
    assert hook.endswith(".sh")
    assert f"COPY job-started.sh {hook}" in dockerfile
    assert (
        f'"{hook}": "job-started.sh"'
        in (ROOT / "scripts/local_release_runner.py").read_text()
    )


def test_every_tracked_workflow_requires_approved_compute() -> None:
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        workflow = yaml.safe_load(path.read_text())
        for job in workflow.get("jobs", {}).values():
            if "runs-on" in job:
                labels = job["runs-on"]
                if path.name == "build_and_publish_local.yml":
                    assert labels == "ubuntu-24.04"
                    assert set(workflow.get("on", workflow.get(True, {}))) == {
                        "workflow_dispatch"
                    }
                    assert "inputs.github_hosted_fallback_authorized" in job["if"]
                    continue
                assert isinstance(labels, list), path
                expected = (
                    "cosmaneura-production-local"
                    if f".github/workflows/{path.name}" in runner.WORKFLOWS
                    else "cosmaneura-validation-local"
                )
                architecture = (
                    "ARM64" if expected == "cosmaneura-production-local" else "X64"
                )
                expected_labels = ["self-hosted", "Linux", architecture, expected]
                if expected == "cosmaneura-production-local":
                    expected_labels.append(
                        "cosmaneura-production-local-${{ github.run_id }}"
                    )
                assert labels == expected_labels, path


@pytest.mark.parametrize(
    "field,value,expected",
    [
        (None, None, True),
        ("github.event_name", "push", False),
        ("github.ref", "refs/heads/other", False),
        ("github.actor", "other", False),
        ("github.triggering_actor", "other", False),
        ("inputs.github_hosted_fallback_authorized", False, False),
    ],
)
def test_hosted_release_never_starts_without_full_owner_opt_in(
    field, value, expected
) -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/build_and_publish_local.yml").read_text()
    )
    context = {
        "github.event_name": "workflow_dispatch",
        "github.ref": "refs/heads/master",
        "github.actor": "JohnClem",
        "github.triggering_actor": "JohnClem",
        "inputs.github_hosted_fallback_authorized": True,
        "needs.authorize-production.result": "success",
    }
    if field is not None:
        context[field] = value
    inputs = workflow.get("on", workflow.get(True))["workflow_dispatch"]["inputs"]
    assert inputs["github_hosted_fallback_authorized"]["default"] is False
    for job in workflow["jobs"].values():
        expression = job["if"].strip().removeprefix("${{").removesuffix("}}")
        for key, actual in context.items():
            expression = expression.replace(key, repr(actual))
        expression = expression.replace("always()", "True").replace("&&", " and ")
        parsed = ast.parse("(" + expression.strip() + ")", mode="eval")
        assert all(
            isinstance(
                node,
                (
                    ast.Expression,
                    ast.BoolOp,
                    ast.And,
                    ast.Compare,
                    ast.Eq,
                    ast.Constant,
                ),
            )
            for node in ast.walk(parsed)
        )
        assert (
            eval(compile(parsed, "<hosted-admission>", "eval"), {"__builtins__": {}})
            is expected
        )


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


@pytest.mark.parametrize(
    "mutation", ["uid", "session", "command", "wrapper", "emulated", "none"]
)
def test_forwarder_cleanup_never_signals_an_unowned_process(
    monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    command = (
        b"socat\0TCP-LISTEN:5432,bind=127.0.0.1,reuseaddr,fork\0TCP:postgres:5432\0"
    )
    if mutation == "emulated":
        command = b"/usr/bin/qemu-x86_64\0/usr/bin/socat\0" + command
    elif mutation == "wrapper":
        command = b"another-wrapper\0" + command
    process = SimpleNamespace(
        exists=lambda: True,
        stat=lambda: SimpleNamespace(st_uid=999 if mutation == "uid" else 1001),
        joinpath=lambda _: SimpleNamespace(
            read_bytes=lambda: b"another-command\0"
            if mutation == "command"
            else command
        ),
    )
    killed = []
    monkeypatch.setattr(services, "Path", lambda _: process)
    monkeypatch.setattr(services.os, "getuid", lambda: 1001)
    monkeypatch.setattr(
        services.os, "getsid", lambda _: 999 if mutation == "session" else 42
    )
    monkeypatch.setattr(
        services.os, "killpg", lambda pid, sig: killed.append((pid, sig))
    )
    forwards = [{"pid": 42, "service": "postgres", "port": 5432}]
    if mutation in {"none", "emulated"}:
        services.stop_forwarders(forwards)
        assert killed == [(42, services.signal.SIGTERM)]
    else:
        with pytest.raises(SystemExit, match="Refusing cleanup"):
            services.stop_forwarders(forwards)
        assert not killed
