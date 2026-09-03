from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from scripts.verify_staging_candidate import (
    CandidateContext,
    CandidateVerificationError,
    _parse_args,
    verify_candidate,
)

CANDIDATE = "8fa02a71f4b880cd43f4fccc8c81c92a0d635623"
MASTER = "6cea63e59203a8730af39497a3672b3f4598ba98"
BASE_SCHEMA = "89c31f0ae275"
EXPECTED_SCHEMA = "9a2399212488"
MIGRATION_PATHS = (
    "migrations/versions/2026_08_24_89c31f0ae275_base.py",
    "migrations/versions/2026_08_29_9a2399212488_head.py",
)


def _context(**overrides: str) -> CandidateContext:
    values = {
        "candidate_sha": CANDIDATE,
        "approval_pr_number": "230",
        "expected_schema_after": EXPECTED_SCHEMA,
        "workflow_sha": MASTER,
        "repository": "CosmaNeura/cosmaneura",
        "event_name": "workflow_dispatch",
        "ref": "refs/heads/master",
        "ref_protected": "true",
        "actor": "JohnClem",
        "triggering_actor": "JohnClem",
    }
    values.update(overrides)
    return CandidateContext(**values)


def _stdout(command: list[str]) -> str:
    if command[:2] == ["git", "ls-tree"]:
        return "\0".join((*MIGRATION_PATHS, ""))
    if command[:2] == ["git", "show"]:
        path = command[2].split(":", 1)[1]
        if path == MIGRATION_PATHS[0]:
            return f'revision = "{BASE_SCHEMA}"\ndown_revision = None\n'
        if path == MIGRATION_PATHS[1]:
            return (
                'raise RuntimeError("candidate migration code must not execute")\n'
                f'revision: str = "{EXPECTED_SCHEMA}"\n'
                f'down_revision: str | None = "{BASE_SCHEMA}"\n'
            )
        raise AssertionError(f"unexpected candidate path: {path}")
    if command[-1] == ".object.sha":
        return MASTER
    if command[:2] == ["gh", "api"]:
        return json.dumps(
            {
                "state": "closed",
                "merged_at": "2026-08-31T22:58:05Z",
                "merge_commit_sha": CANDIDATE,
                "base": {"ref": "master"},
            }
        )
    raise AssertionError(f"unexpected command: {command}")


def _verify(
    context: CandidateContext,
    *,
    stdout: Callable[[list[str]], str] = _stdout,
    commit_exists: Callable[[str], bool] = lambda _sha: True,
    is_ancestor: Callable[[str, str], bool] = lambda _ancestor, _descendant: True,
) -> dict[str, str | int]:
    return verify_candidate(
        context,
        stdout=stdout,
        commit_exists=commit_exists,
        is_ancestor=is_ancestor,
    )


def test_accepts_exact_merged_pr_candidate_on_current_protected_master() -> None:
    assert _verify(_context()) == {
        "approval_pr_number": 230,
        "candidate_sha": CANDIDATE,
        "master_sha": MASTER,
        "schema_head": EXPECTED_SCHEMA,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("candidate_sha", CANDIDATE[:12]),
        ("approval_pr_number", "0"),
        ("expected_schema_after", "HEAD"),
        ("repository", "someone/fork"),
        ("event_name", "push"),
        ("ref", "refs/heads/staging"),
        ("ref_protected", "false"),
        ("actor", "not-the-founder"),
        ("triggering_actor", "not-the-founder"),
    ],
)
def test_rejects_invalid_or_unauthorized_dispatch(field: str, value: str) -> None:
    with pytest.raises(CandidateVerificationError):
        _verify(_context(**{field: value}))


def test_rejects_master_advance() -> None:
    def stdout(command: list[str]) -> str:
        if command[-1] == ".object.sha":
            return "a" * 40
        return _stdout(command)

    with pytest.raises(CandidateVerificationError, match="master advanced"):
        _verify(_context(), stdout=stdout)


@pytest.mark.parametrize(
    "pull_override",
    [
        {"state": "open"},
        {"merged_at": None},
        {"merge_commit_sha": "f" * 40},
        {"base": {"ref": "staging"}},
    ],
)
def test_rejects_candidate_without_exact_merged_master_pr(
    pull_override: dict[str, object],
) -> None:
    pull = {
        "state": "closed",
        "merged_at": "2026-08-31T22:58:05Z",
        "merge_commit_sha": CANDIDATE,
        "base": {"ref": "master"},
    }
    pull.update(pull_override)

    def stdout(command: list[str]) -> str:
        if command[-1] == ".object.sha":
            return MASTER
        if command[:2] == ["gh", "api"]:
            return json.dumps(pull)
        return _stdout(command)

    with pytest.raises(CandidateVerificationError, match="merge commit"):
        _verify(_context(), stdout=stdout)


def test_rejects_missing_or_non_ancestor_candidate() -> None:
    with pytest.raises(CandidateVerificationError, match="unavailable"):
        _verify(_context(), commit_exists=lambda _sha: False)
    with pytest.raises(CandidateVerificationError, match="not contained"):
        _verify(_context(), is_ancestor=lambda _ancestor, _descendant: False)


def test_reads_migrations_from_exact_candidate_git_tree_without_importing() -> None:
    commands: list[list[str]] = []

    def stdout(command: list[str]) -> str:
        commands.append(command)
        return _stdout(command)

    assert _verify(_context(), stdout=stdout)["schema_head"] == EXPECTED_SCHEMA
    assert [
        "git",
        "ls-tree",
        "-r",
        "-z",
        "--name-only",
        CANDIDATE,
        "--",
        "migrations/versions",
    ] in commands
    assert [
        "git",
        "show",
        f"{CANDIDATE}:{MIGRATION_PATHS[1]}",
    ] in commands


def test_rejects_schema_head_mismatch() -> None:
    with pytest.raises(CandidateVerificationError, match="does not match"):
        _verify(_context(expected_schema_after="da6e199668a4"))


def test_rejects_candidate_with_multiple_migration_heads() -> None:
    paths = (
        "migrations/versions/aaaaaaaaaaaa_root.py",
        "migrations/versions/bbbbbbbbbbbb_one.py",
        "migrations/versions/cccccccccccc_two.py",
    )
    parents = {
        "aaaaaaaaaaaa": None,
        "bbbbbbbbbbbb": "aaaaaaaaaaaa",
        "cccccccccccc": "aaaaaaaaaaaa",
    }

    def stdout(command: list[str]) -> str:
        if command[:2] == ["git", "ls-tree"]:
            return "\0".join((*paths, ""))
        if command[:2] == ["git", "show"]:
            revision = command[2].rsplit("/", 1)[1][:12]
            return f"revision = {revision!r}\ndown_revision = {parents[revision]!r}\n"
        return _stdout(command)

    with pytest.raises(CandidateVerificationError, match="multiple heads"):
        _verify(_context(), stdout=stdout)


def test_rejects_candidate_without_a_migration_head() -> None:
    def stdout(command: list[str]) -> str:
        if command[:2] == ["git", "ls-tree"]:
            return ""
        return _stdout(command)

    with pytest.raises(CandidateVerificationError, match="no head"):
        _verify(_context(), stdout=stdout)


def test_rejects_candidate_with_cycle_hidden_beside_one_valid_head() -> None:
    paths = tuple(
        f"migrations/versions/{revision}_migration.py"
        for revision in ("aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc")
    )
    parents = {
        "aaaaaaaaaaaa": None,
        "bbbbbbbbbbbb": "cccccccccccc",
        "cccccccccccc": "bbbbbbbbbbbb",
    }

    def stdout(command: list[str]) -> str:
        if command[:2] == ["git", "ls-tree"]:
            return "\0".join((*paths, ""))
        if command[:2] == ["git", "show"]:
            revision = command[2].rsplit("/", 1)[1][:12]
            return f"revision = {revision!r}\ndown_revision = {parents[revision]!r}\n"
        return _stdout(command)

    with pytest.raises(CandidateVerificationError, match="contains a cycle"):
        _verify(_context(), stdout=stdout)


def test_rejects_disconnected_migration_graph() -> None:
    paths = tuple(
        f"migrations/versions/{revision}_migration.py"
        for revision in ("aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc")
    )
    parents = {
        "aaaaaaaaaaaa": None,
        "bbbbbbbbbbbb": "aaaaaaaaaaaa",
        "cccccccccccc": None,
    }

    def stdout(command: list[str]) -> str:
        if command[:2] == ["git", "ls-tree"]:
            return "\0".join((*paths, ""))
        if command[:2] == ["git", "show"]:
            revision = command[2].rsplit("/", 1)[1][:12]
            return f"revision = {revision!r}\ndown_revision = {parents[revision]!r}\n"
        return _stdout(command)

    with pytest.raises(CandidateVerificationError, match="disconnected"):
        _verify(_context(), stdout=stdout)


def test_cli_uses_github_environment_defaults_and_preserves_flag_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = {
        "CANDIDATE_SHA": "f" * 40,
        "APPROVAL_PR_NUMBER": "230",
        "EXPECTED_SCHEMA_AFTER": EXPECTED_SCHEMA,
        "GITHUB_SHA": MASTER,
        "GITHUB_REPOSITORY": "CosmaNeura/cosmaneura",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/master",
        "GITHUB_REF_PROTECTED": "true",
        "GITHUB_ACTOR": "JohnClem",
        "GITHUB_TRIGGERING_ACTOR": "JohnClem",
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    args = _parse_args(["--candidate-sha", CANDIDATE])

    assert vars(args) == {
        "candidate_sha": CANDIDATE,
        "approval_pr_number": "230",
        "expected_schema_after": EXPECTED_SCHEMA,
        "workflow_sha": MASTER,
        "repository": "CosmaNeura/cosmaneura",
        "event_name": "workflow_dispatch",
        "ref": "refs/heads/master",
        "ref_protected": "true",
        "actor": "JohnClem",
        "triggering_actor": "JohnClem",
    }


def test_cli_requires_value_when_flag_and_environment_are_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment_names = (
        "CANDIDATE_SHA",
        "APPROVAL_PR_NUMBER",
        "EXPECTED_SCHEMA_AFTER",
        "GITHUB_SHA",
        "GITHUB_REPOSITORY",
        "GITHUB_EVENT_NAME",
        "GITHUB_REF",
        "GITHUB_REF_PROTECTED",
        "GITHUB_ACTOR",
        "GITHUB_TRIGGERING_ACTOR",
    )
    for name in environment_names:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(SystemExit, match="2"):
        _parse_args([])
