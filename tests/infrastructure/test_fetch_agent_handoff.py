import copy
import hashlib
import importlib.util
import io
import json
import stat
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).parents[2]
FETCHER_PATH = ROOT / ".github/scripts/fetch_agent_handoff.py"
REPOSITORY = "CosmaNeura/cosmaneura"
HEAD_SHA = "a" * 40
POLICY_SHA = "c" * 40
BASE_SHA = POLICY_SHA
RUN_ID = 9001
ARTIFACT_ID = 7001
KIND = "deployment_assessment"


def _load_fetcher() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "fetch_agent_handoff_test", FETCHER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fetcher() -> ModuleType:
    return _load_fetcher()


def _canonical_digest(document: dict[str, Any]) -> str:
    preimage = copy.deepcopy(document)
    preimage["integrity"].pop("artifact_digest", None)
    encoded = json.dumps(
        preimage,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _handoff() -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": "cosmaneura.agent-handoff.v1",
        "kind": KIND,
        "handoff_id": f"{KIND}:{REPOSITORY}:{HEAD_SHA}:run-{RUN_ID}",
        "producer": {
            "agent": "deploy-navigator",
            "policy_version": "1.0.0",
            "foundry_agent_version": "4",
            "model": "gpt-5.6-sol",
            "policy_sha": POLICY_SHA,
        },
        "subject": {
            "repository": REPOSITORY,
            "pull_request": 101,
            "base_sha": BASE_SHA,
            "head_sha": HEAD_SHA,
            "workflow_run_id": RUN_ID,
            "environment": "production",
        },
        "coverage": {
            "included": ["repository diff", "CI checks"],
            "omitted": ["production telemetry"],
            "evidence_fresh_until": "2099-08-25T17:00:00Z",
        },
        "decision": {
            "status": "READY",
            "risk_tier": "T1",
            "summary": "The exact commit has sufficient deployment evidence.",
        },
        "payload": {
            "target_environment": "production",
            "required_checks": [
                {
                    "name": "tests",
                    "status": "success",
                    "evidence_locator": "github-check:tests",
                }
            ],
            "migration_review_required": False,
        },
        "actions": [],
        "constraints": {
            "must_not": ["Modify production resources."],
            "approvals_required": [],
        },
        "integrity": {
            "generated_at": "2026-08-25T16:00:00Z",
            "input_digest": f"sha256:{'0' * 64}",
        },
    }
    document["integrity"]["artifact_digest"] = _canonical_digest(document)
    return document


def _zip_bytes(
    contents: bytes,
    name: str = "deployment-assessment.json",
    extra: bytes | None = None,
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, contents)
        if extra is not None:
            archive.writestr("other.json", extra)
    return buffer.getvalue()


def _run_metadata(run_id: int = RUN_ID) -> dict[str, Any]:
    return {
        "id": run_id,
        "name": "Foundry PR Assessment",
        "path": ".github/workflows/agent_pr_assessment.yml",
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "success",
        "head_branch": "master",
        "head_sha": POLICY_SHA,
        "repository": {"full_name": REPOSITORY},
        "head_repository": {"full_name": REPOSITORY},
    }


class FakeGitHub:
    def __init__(
        self,
        handoff: dict[str, Any],
        *,
        artifact_count: int = 1,
        artifact_kind: str = KIND,
        archive_name: str = "deployment-assessment.json",
        extra_archive_file: bool = False,
    ) -> None:
        self.handoff_bytes = json.dumps(handoff, sort_keys=True).encode()
        extra = b"{}" if extra_archive_file else None
        self.archive = _zip_bytes(self.handoff_bytes, archive_name, extra)
        self.artifact_count = artifact_count
        self.artifact_kind = artifact_kind
        self.runs = {
            RUN_ID + index: _run_metadata(RUN_ID + index)
            for index in range(artifact_count)
        }
        self.master_sha = POLICY_SHA
        self.pull_request = {
            "number": 101,
            "state": "open",
            "draft": False,
            "base": {
                "ref": "master",
                "sha": BASE_SHA,
                "repo": {"full_name": REPOSITORY},
            },
            "head": {"sha": HEAD_SHA},
        }
        self.digest_override: str | None = None
        self.commands: list[list[str]] = []
        self.keyword_arguments: list[dict[str, Any]] = []

    def _artifacts(self) -> list[dict[str, Any]]:
        digest = (
            self.digest_override or f"sha256:{hashlib.sha256(self.archive).hexdigest()}"
        )
        return [
            {
                "id": ARTIFACT_ID + index,
                "name": f"cosmaneura-agent-{self.artifact_kind}-{HEAD_SHA}",
                "expired": False,
                "size_in_bytes": len(self.archive),
                "digest": digest,
                "workflow_run": {"id": RUN_ID + index},
            }
            for index in range(self.artifact_count)
        ]

    def __call__(
        self, argv: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        assert isinstance(argv, list)
        assert all(isinstance(value, str) for value in argv)
        assert "shell" not in kwargs
        self.commands.append(argv)
        self.keyword_arguments.append(kwargs)

        if argv[:2] == ["gh", "run"]:
            run_id = int(argv[3])
            directory = Path(argv[argv.index("--dir") + 1])
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "deployment-assessment.json").write_bytes(self.handoff_bytes)
            assert run_id in self.runs
            return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

        assert argv[:2] == ["gh", "api"]
        endpoint = argv[2]
        if "/actions/artifacts?" in endpoint:
            payload: Any = [{"artifacts": self._artifacts()}]
        elif "/actions/runs/" in endpoint and endpoint.rsplit("/", 1)[-1].isdigit():
            payload = self.runs[int(endpoint.rsplit("/", 1)[-1])]
        elif endpoint.endswith("/git/ref/heads/master"):
            payload = {
                "ref": "refs/heads/master",
                "object": {"type": "commit", "sha": self.master_sha},
            }
        elif endpoint.endswith("/pulls/101"):
            payload = self.pull_request
        elif endpoint.endswith("/zip"):
            return subprocess.CompletedProcess(argv, 0, stdout=self.archive, stderr=b"")
        else:
            raise AssertionError(f"unexpected command: {argv}")
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps(payload).encode(), stderr=b""
        )


def _execute(
    fetcher: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake: FakeGitHub,
    *,
    kind: str = KIND,
) -> tuple[int, Path]:
    monkeypatch.setattr(fetcher.subprocess, "run", fake)
    output = tmp_path / "handoff.json"
    result = fetcher.main(["--sha", HEAD_SHA, "--kind", kind, "--output", str(output)])
    return result, output


def test_fetches_only_after_exact_provenance_and_archive_verification(
    fetcher: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    handoff = _handoff()
    fake = FakeGitHub(handoff)

    result, output = _execute(fetcher, monkeypatch, tmp_path, fake)

    assert result == 0
    assert json.loads(output.read_text()) == handoff
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert all(command[0] == "gh" for command in fake.commands)
    archive_index = next(
        index
        for index, command in enumerate(fake.commands)
        if command[2].endswith("/zip")
    )
    download_index = next(
        index
        for index, command in enumerate(fake.commands)
        if command[:3] == ["gh", "run", "download"]
    )
    assert archive_index < download_index
    assert ["--name", f"cosmaneura-agent-{KIND}-{HEAD_SHA}"] == fake.commands[
        download_index
    ][6:8]
    assert any(
        command[:3] == ["gh", "api", f"repos/{REPOSITORY}/pulls/101"]
        for command in fake.commands
    )


def test_rejects_archive_digest_mismatch_before_extraction(
    fetcher: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeGitHub(_handoff())
    fake.digest_override = f"sha256:{'f' * 64}"

    result, output = _execute(fetcher, monkeypatch, tmp_path, fake)

    assert result == 4
    assert not output.exists()
    assert "archive digest" in capsys.readouterr().err
    assert not any(command[:2] == ["gh", "run"] for command in fake.commands)


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("workflow_run_id", 9999, "subject.workflow_run_id"),
        ("repository", "attacker/fork", "subject.repository"),
        ("head_sha", "d" * 40, "subject.head_sha"),
    ],
)
def test_rejects_handoff_subject_binding_mismatch(
    fetcher: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    field: str,
    value: object,
    expected: str,
) -> None:
    handoff = _handoff()
    handoff["subject"][field] = value
    handoff["integrity"]["artifact_digest"] = _canonical_digest(handoff)
    fake = FakeGitHub(handoff)

    result, output = _execute(fetcher, monkeypatch, tmp_path, fake)

    assert result == 4
    assert not output.exists()
    assert expected in capsys.readouterr().err


def test_rejects_handoff_kind_that_differs_from_exact_artifact_kind(
    fetcher: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeGitHub(_handoff(), artifact_kind="security_assessment")

    result, output = _execute(
        fetcher, monkeypatch, tmp_path, fake, kind="security_assessment"
    )

    assert result == 4
    assert not output.exists()
    assert "kind='deployment_assessment'" in capsys.readouterr().err


def test_rejects_invalid_inner_handoff_digest(
    fetcher: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    handoff = _handoff()
    handoff["integrity"]["artifact_digest"] = f"sha256:{'f' * 64}"
    fake = FakeGitHub(handoff)

    result, output = _execute(fetcher, monkeypatch, tmp_path, fake)

    assert result == 4
    assert not output.exists()
    error = capsys.readouterr().err
    assert "integrity.artifact_digest" in error
    assert "canonical assessment content" in error


def test_rejects_policy_sha_that_differs_from_current_master(
    fetcher: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeGitHub(_handoff())
    fake.master_sha = "d" * 40

    result, output = _execute(fetcher, monkeypatch, tmp_path, fake)

    assert result == 4
    assert not output.exists()
    error = capsys.readouterr().err
    assert "no longer current master" in error
    assert "rerun" in error


def test_rejects_producer_policy_sha_that_differs_from_workflow_run(
    fetcher: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    handoff = _handoff()
    handoff["producer"]["policy_sha"] = "d" * 40
    handoff["integrity"]["artifact_digest"] = _canonical_digest(handoff)
    fake = FakeGitHub(handoff)

    result, output = _execute(fetcher, monkeypatch, tmp_path, fake)

    assert result == 4
    assert not output.exists()
    assert "producer.policy_sha" in capsys.readouterr().err


def test_rejects_expired_evidence_before_querying_current_master(
    fetcher: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    handoff = _handoff()
    handoff["coverage"]["evidence_fresh_until"] = "2020-01-01T00:00:00Z"
    handoff["integrity"]["generated_at"] = "2019-12-31T23:00:00Z"
    handoff["integrity"]["artifact_digest"] = _canonical_digest(handoff)
    fake = FakeGitHub(handoff)

    result, output = _execute(fetcher, monkeypatch, tmp_path, fake)

    assert result == 4
    assert not output.exists()
    assert "evidence has expired" in capsys.readouterr().err
    assert not any(
        command[2].endswith("/git/ref/heads/master")
        for command in fake.commands
        if command[:2] == ["gh", "api"]
    )


@pytest.mark.parametrize(
    "mutation,expected",
    [
        (lambda pull: pull.update({"state": "closed"}), "state='closed'"),
        (lambda pull: pull.update({"draft": True}), "draft=True"),
        (
            lambda pull: pull["base"].update({"ref": "staging"}),
            "base.ref='staging'",
        ),
        (
            lambda pull: pull["base"].update({"sha": "d" * 40}),
            "base.sha (handoff subject)",
        ),
        (
            lambda pull: pull["head"].update({"sha": "d" * 40}),
            "head.sha",
        ),
    ],
)
def test_rejects_pull_request_that_is_no_longer_exactly_assessed(
    fetcher: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    mutation: Any,
    expected: str,
) -> None:
    fake = FakeGitHub(_handoff())
    mutation(fake.pull_request)

    result, output = _execute(fetcher, monkeypatch, tmp_path, fake)

    assert result == 4
    assert not output.exists()
    error = capsys.readouterr().err
    assert expected in error
    assert "rerun the Foundry PR Assessment" in error


def test_rejects_missing_pull_request_binding_without_calling_pull_api(
    fetcher: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    handoff = _handoff()
    handoff["subject"].pop("pull_request")
    handoff["integrity"]["artifact_digest"] = _canonical_digest(handoff)
    fake = FakeGitHub(handoff)

    result, output = _execute(fetcher, monkeypatch, tmp_path, fake)

    assert result == 4
    assert not output.exists()
    assert "subject.pull_request" in capsys.readouterr().err
    assert not any(
        "/pulls/" in command[2]
        for command in fake.commands
        if command[:2] == ["gh", "api"]
    )


def test_rejects_ambiguous_successful_trusted_runs_without_downloading(
    fetcher: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeGitHub(_handoff(), artifact_count=2)

    result, output = _execute(fetcher, monkeypatch, tmp_path, fake)

    assert result == 6
    assert not output.exists()
    assert "multiple trusted artifacts" in capsys.readouterr().err
    assert not any(command[:2] == ["gh", "run"] for command in fake.commands)


@pytest.mark.parametrize(
    "archive_name,extra,expected",
    [
        ("../handoff.json", False, "unsafe path"),
        ("handoff.json", True, "exactly one handoff JSON"),
    ],
)
def test_rejects_unsafe_or_multi_file_archives_before_gh_extraction(
    fetcher: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    archive_name: str,
    extra: bool,
    expected: str,
) -> None:
    fake = FakeGitHub(_handoff(), archive_name=archive_name, extra_archive_file=extra)

    result, output = _execute(fetcher, monkeypatch, tmp_path, fake)

    assert result == 4
    assert not output.exists()
    assert expected in capsys.readouterr().err
    assert not any(command[:2] == ["gh", "run"] for command in fake.commands)


def test_rejects_non_dispatch_or_non_master_workflow_metadata(
    fetcher: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeGitHub(_handoff())
    fake.runs[RUN_ID]["event"] = "pull_request"
    fake.runs[RUN_ID]["head_branch"] = "feature/untrusted"

    result, output = _execute(fetcher, monkeypatch, tmp_path, fake)

    assert result == 5
    assert not output.exists()
    error = capsys.readouterr().err
    assert "workflow_dispatch" in error
    assert "master" in error
