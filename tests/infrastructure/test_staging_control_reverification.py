from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.verify_staging_candidate import (
    CandidateContext,
    CandidateVerificationError,
    _parse_args,
    verify_candidate,
)

SCHEMA = "89c31f0ae275"


@pytest.fixture
def repo(tmp_path: Path):
    def git(*args: str) -> str:
        return subprocess.check_output(
            [
                "git",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "core.hooksPath=/dev/null",
                *args,
            ],
            cwd=tmp_path,
            text=True,
        ).strip()

    def write(path: str, value: str) -> None:
        p = tmp_path / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value)

    git("init", "-q")
    git("config", "user.name", "Synthetic provenance test")
    git("config", "user.email", "scribe-lab@invalid.example")
    source_root = Path(__file__).resolve().parents[2]
    manifest_path = "scripts/canonical_staging_control_dependencies.json"
    audit = json.loads((source_root / manifest_path).read_text())
    for path in audit["files"]:
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((source_root / path).read_bytes())
        destination.chmod(
            0o755 if (source_root / path).stat().st_mode & 0o111 else 0o644
        )
    write(manifest_path, (source_root / manifest_path).read_text())
    write(
        "scripts/verify_staging_candidate.py",
        (source_root / "scripts/verify_staging_candidate.py").read_text(),
    )
    write("migrations/versions/base.py", f'revision="{SCHEMA}"\ndown_revision=None\n')
    write("app/routes/agent.py", "first candidate\n")
    git("add", ".")
    git("commit", "-qm", "Synthetic immutable candidate")
    candidate = git("rev-parse", "HEAD")
    write("README.md", "dispatched protected controller\n")
    git("add", ".")
    git("commit", "-qm", "Synthetic dispatch controller")
    controller = git("rev-parse", "HEAD")
    write("app/routes/agent.py", "later unrelated application work\n")
    write(
        "docs/flight-plans/reviewed-excerpt-citation-receiver.md",
        "later application flight plan\n",
    )
    git("add", ".")
    git("commit", "-qm", "Synthetic protected master application advance")
    master = git("rev-parse", "HEAD")

    def verify(
        *, reverify: bool = True, protected: bool = True, valid_pull: bool = True
    ):
        git("checkout", "--detach", "-q", state["controller"])
        context = CandidateContext(
            candidate,
            "230",
            SCHEMA,
            state["controller"],
            "CosmaNeura/cosmaneura",
            "workflow_dispatch",
            "refs/heads/master",
            "true",
            "JohnClem",
            "JohnClem",
            reverify=reverify,
        )

        def stdout(command: list[str]) -> str:
            if command[:2] == ["gh", "api"]:
                if command[-1].endswith("branches/master"):
                    return json.dumps(
                        {
                            "name": "master",
                            "protected": protected,
                            "commit": {"sha": state["master"]},
                        }
                    )
                return json.dumps(
                    {
                        "state": "closed",
                        "merged_at": "2026-10-31T12:00:00Z",
                        "base": {"ref": "master"},
                        "merge_commit_sha": candidate if valid_pull else "f" * 40,
                    }
                )
            return git(*command[1:])

        def exists(sha: str) -> bool:
            return (
                subprocess.run(
                    ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
                    cwd=tmp_path,
                    capture_output=True,
                    check=False,
                ).returncode
                == 0
            )

        def ancestor(first: str, second: str) -> bool:
            return (
                subprocess.run(
                    ["git", "merge-base", "--is-ancestor", first, second],
                    cwd=tmp_path,
                    capture_output=True,
                    check=False,
                ).returncode
                == 0
            )

        return verify_candidate(
            context, stdout=stdout, commit_exists=exists, is_ancestor=ancestor
        )

    state = {
        "git": git,
        "write": write,
        "verify": verify,
        "master": master,
        "controller": controller,
        "root": tmp_path,
    }
    return state


def test_application_master_advance_passes_only_later_reverification(repo) -> None:
    with pytest.raises(CandidateVerificationError, match="master advanced"):
        repo["verify"](reverify=False)
    assert repo["verify"]()["master_sha"] == repo["master"]


@pytest.mark.parametrize(
    "path",
    [
        "scripts/runtime_guard.py",
        "infra-staging/deploy.sh",
        ".github/actions/new/action.yml",
        "AGENTS.md",
        ".env.staging.backend",
        "web/pnpm-lock.yaml",
    ],
)
def test_changed_or_added_protected_dependency_refuses(repo, path: str) -> None:
    repo["git"]("checkout", "--detach", "-q", repo["master"])
    repo["write"](path, "changed protected dependency\n")
    repo["git"]("add", ".")
    repo["git"]("commit", "-qm", "Synthetic changed deployment dependency")
    repo["master"] = repo["git"]("rev-parse", "HEAD")
    with pytest.raises(CandidateVerificationError, match="deployment controls changed"):
        repo["verify"]()


def test_non_descendant_master_refuses_even_with_identical_control_tree(repo) -> None:
    repo["git"]("checkout", "--orphan", "rewritten-master", "-q")
    repo["git"]("commit", "-qm", "Synthetic unrelated protected branch root")
    repo["master"] = repo["git"]("rev-parse", "HEAD")
    with pytest.raises(CandidateVerificationError, match="not a descendant"):
        repo["verify"]()


def test_changed_control_executable_mode_refuses(repo) -> None:
    (repo["root"] / "scripts/staging_release_contract.py").chmod(0o755)
    repo["git"]("add", "scripts/staging_release_contract.py")
    repo["git"]("commit", "-qm", "Synthetic changed control file mode")
    repo["master"] = repo["git"]("rev-parse", "HEAD")
    with pytest.raises(CandidateVerificationError, match="deployment controls changed"):
        repo["verify"]()


def test_unknown_controller_import_refuses_even_at_initial_dispatch(repo) -> None:
    repo["git"]("checkout", "--detach", "-q", repo["master"])
    repo["write"](
        "scripts/staging_release_contract.py", "from app import deployment_guard\n"
    )
    repo["write"]("app/deployment_guard.py", "changed imported controller helper\n")
    repo["git"]("add", ".")
    repo["git"]("commit", "-qm", "Synthetic unaudited controller import")
    repo["master"] = repo["controller"] = repo["git"]("rev-parse", "HEAD")
    with pytest.raises(CandidateVerificationError, match="unaudited"):
        repo["verify"](reverify=False)


def test_unknown_business_file_can_advance_without_changing_controller(repo) -> None:
    repo["git"]("checkout", "--detach", "-q", repo["master"])
    repo["write"]("app/new_business_file.py", "unrelated business source\n")
    repo["git"]("add", ".")
    repo["git"]("commit", "-qm", "Synthetic unrelated business source advance")
    repo["master"] = repo["git"]("rev-parse", "HEAD")
    assert repo["verify"]()["master_sha"] == repo["master"]


def test_reverification_keeps_live_protection_and_exact_candidate_gates(repo) -> None:
    with pytest.raises(CandidateVerificationError, match="protected master metadata"):
        repo["verify"](protected=False)
    with pytest.raises(CandidateVerificationError, match="merge commit"):
        repo["verify"](valid_pull=False)


def test_reverify_phase_requires_explicit_cli_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "CANDIDATE_SHA": "a" * 40,
        "APPROVAL_PR_NUMBER": "230",
        "EXPECTED_SCHEMA_AFTER": SCHEMA,
        "GITHUB_SHA": "b" * 40,
        "GITHUB_REPOSITORY": "CosmaNeura/cosmaneura",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/master",
        "GITHUB_REF_PROTECTED": "true",
        "GITHUB_ACTOR": "JohnClem",
        "GITHUB_TRIGGERING_ACTOR": "JohnClem",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("REVERIFY", "true")
    assert _parse_args([]).reverify is False
    assert _parse_args(["--reverify"]).reverify is True
