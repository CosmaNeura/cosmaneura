#!/usr/bin/env python3
"""Verify an immutable, reviewed staging candidate against protected master."""

from __future__ import annotations

import argparse
import ast
import configparser
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

FULL_SHA = re.compile(r"[0-9a-f]{40}")
PR_NUMBER = re.compile(r"[1-9][0-9]*")
SCHEMA_REVISION = re.compile(r"[0-9a-f]{12}")
FOUNDER_LOGIN = "JohnClem"
GITHUB_HOSTNAME = "github.com"
MASTER_REF = "refs/heads/master"
REPOSITORY = "CosmaNeura/cosmaneura"
ALEMBIC_CONFIG = "alembic.ini"
MIGRATION_DIRECTORY = "migrations/versions"
# Business trees may advance only while a pinned audit proves the canonical
# controller executes/reads exclusively the protected dependency closure below.
# This is an audited source snapshot, not an inferred generic import graph.
CANDIDATE_ONLY_PREFIXES = (
    "app/",
    "web/",
    "migrations/",
    "tests/",
    "docs/flight-plans/",
)
AUDITED_DEPENDENCIES_PATH = "scripts/canonical_staging_control_dependencies.json"
AUDITED_DEPENDENCIES_SHA256 = (
    "4b21b24d92ca53ae7662e0c566d8276c54954ce3a177876133dc5cb60572880b"
)
REQUIRED_CONTROL_PATHS = frozenset(
    {
        ".github/workflows/deploy_staging_candidate.yml",
        "scripts/verify_staging_candidate.py",
        "infra-staging/deploy.sh",
        "AGENTS.md",
        "CLAUDE.md",
    }
)


class CandidateVerificationError(RuntimeError):
    """A fixed-message staging candidate verification failure."""


@dataclass(frozen=True, slots=True)
class CandidateContext:
    candidate_sha: str
    approval_pr_number: str
    expected_schema_after: str
    workflow_sha: str
    repository: str
    event_name: str
    ref: str
    ref_protected: str
    actor: str
    triggering_actor: str
    reverify: bool = False


@dataclass(frozen=True, slots=True)
class HumanCandidateContext:
    candidate_sha: str
    approval_pr_number: str
    expected_schema_after: str
    controller_sha: str
    repository: str


def _stdout(command: list[str]) -> str:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise CandidateVerificationError("candidate provenance lookup failed")
    return completed.stdout.strip()


def _commit_exists(sha: str) -> bool:
    return (
        subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            check=False,
            capture_output=True,
        ).returncode
        == 0
    )


def _is_ancestor(ancestor: str, descendant: str) -> bool:
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            check=False,
            capture_output=True,
        ).returncode
        == 0
    )


def _protected_master_sha(
    repository: str,
    *,
    stdout: Callable[[list[str]], str],
) -> str:
    response = stdout(
        [
            "gh",
            "api",
            "--hostname",
            GITHUB_HOSTNAME,
            f"repos/{repository}/branches/master",
        ]
    )
    try:
        branch: dict[str, Any] = json.loads(response)
        commit = branch["commit"]
        sha = commit["sha"]
    except (KeyError, TypeError, json.JSONDecodeError):
        raise CandidateVerificationError(
            "protected master metadata is invalid"
        ) from None
    if (
        branch.get("name") != "master"
        or branch.get("protected") is not True
        or not isinstance(commit, dict)
        or not isinstance(sha, str)
        or FULL_SHA.fullmatch(sha) is None
    ):
        raise CandidateVerificationError("protected master metadata is invalid")
    return sha


def _migration_revisions(source: str, path: str) -> tuple[str, tuple[str, ...]]:
    try:
        module = ast.parse(source, filename=path)
    except SyntaxError:
        raise CandidateVerificationError(
            "candidate migration metadata is invalid"
        ) from None

    assignments: dict[str, ast.expr] = {}
    for statement in module.body:
        name: str | None = None
        value: ast.expr | None = None
        if isinstance(statement, ast.AnnAssign) and isinstance(
            statement.target, ast.Name
        ):
            name = statement.target.id
            value = statement.value
        elif isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            if isinstance(target, ast.Name):
                name = target.id
                value = statement.value
        if name not in {"revision", "down_revision"}:
            continue
        if value is None or name in assignments:
            raise CandidateVerificationError("candidate migration metadata is invalid")
        assignments[name] = value

    if set(assignments) != {"revision", "down_revision"}:
        raise CandidateVerificationError("candidate migration metadata is invalid")
    try:
        revision = ast.literal_eval(assignments["revision"])
        down_revision = ast.literal_eval(assignments["down_revision"])
    except (ValueError, TypeError, SyntaxError):
        raise CandidateVerificationError(
            "candidate migration metadata is invalid"
        ) from None

    if not isinstance(revision, str) or SCHEMA_REVISION.fullmatch(revision) is None:
        raise CandidateVerificationError("candidate migration metadata is invalid")
    if down_revision is None:
        parents: tuple[str, ...] = ()
    elif isinstance(down_revision, str):
        parents = (down_revision,)
    elif isinstance(down_revision, (tuple, list)):
        parents = tuple(down_revision)
    else:
        raise CandidateVerificationError("candidate migration metadata is invalid")
    if not parents or all(
        isinstance(parent, str) and SCHEMA_REVISION.fullmatch(parent)
        for parent in parents
    ):
        return revision, parents
    raise CandidateVerificationError("candidate migration metadata is invalid")


def _verify_alembic_inventory_contract(
    candidate_sha: str,
    *,
    stdout: Callable[[list[str]], str],
) -> None:
    source = stdout(["git", "show", f"{candidate_sha}:{ALEMBIC_CONFIG}"])
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(source)
        script_location = parser.get("alembic", "script_location").strip()
        version_locations = parser.get(
            "alembic", "version_locations", fallback=""
        ).strip()
        recursive = parser.getboolean(
            "alembic", "recursive_version_locations", fallback=False
        )
        sourceless = parser.getboolean("alembic", "sourceless", fallback=False)
    except (configparser.Error, KeyError, ValueError):
        raise CandidateVerificationError(
            "candidate Alembic inventory configuration is invalid"
        ) from None
    if script_location != "migrations" or version_locations or recursive or sourceless:
        raise CandidateVerificationError(
            "candidate Alembic inventory configuration is unsupported"
        )


def _candidate_schema_head(
    candidate_sha: str,
    *,
    stdout: Callable[[list[str]], str],
) -> str:
    _verify_alembic_inventory_contract(candidate_sha, stdout=stdout)
    tree = stdout(
        [
            "git",
            "ls-tree",
            "-z",
            f"{candidate_sha}:{MIGRATION_DIRECTORY}",
        ]
    )
    paths: list[str] = []
    for entry in tree.split("\0"):
        if not entry:
            continue
        try:
            metadata, name = entry.split("\t", 1)
            mode, object_type, _object_sha = metadata.split(" ", 2)
        except ValueError:
            raise CandidateVerificationError(
                "candidate migration inventory is invalid"
            ) from None
        if not name.endswith(".py"):
            continue
        if "/" in name or object_type != "blob" or mode not in {"100644", "100755"}:
            raise CandidateVerificationError("candidate migration inventory is invalid")
        paths.append(f"{MIGRATION_DIRECTORY}/{name}")
    paths.sort()

    revisions: set[str] = set()
    parents_by_revision: dict[str, tuple[str, ...]] = {}
    referenced_revisions: set[str] = set()
    for path in paths:
        source = stdout(["git", "show", f"{candidate_sha}:{path}"])
        revision, parents = _migration_revisions(source, path)
        if revision in revisions:
            raise CandidateVerificationError(
                "candidate migration graph contains duplicate revisions"
            )
        revisions.add(revision)
        parents_by_revision[revision] = parents
        referenced_revisions.update(parents)

    if referenced_revisions - revisions:
        raise CandidateVerificationError(
            "candidate migration graph references unavailable revisions"
        )
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(revision: str) -> None:
        if revision in visiting:
            raise CandidateVerificationError(
                "candidate migration graph contains a cycle"
            )
        if revision in visited:
            return
        visiting.add(revision)
        for parent in parents_by_revision[revision]:
            visit(parent)
        visiting.remove(revision)
        visited.add(revision)

    for revision in revisions:
        visit(revision)

    if revisions:
        connected: set[str] = set()
        pending = [next(iter(revisions))]
        children_by_revision = {revision: set() for revision in revisions}
        for revision, parents in parents_by_revision.items():
            for parent in parents:
                children_by_revision[parent].add(revision)
        while pending:
            revision = pending.pop()
            if revision in connected:
                continue
            connected.add(revision)
            pending.extend(parents_by_revision[revision])
            pending.extend(children_by_revision[revision])
        if connected != revisions:
            raise CandidateVerificationError(
                "candidate migration graph contains disconnected revisions"
            )

    heads = revisions - referenced_revisions
    if not heads:
        raise CandidateVerificationError("candidate migration graph has no head")
    if len(heads) != 1:
        raise CandidateVerificationError("candidate migration graph has multiple heads")
    return heads.pop()


def _control_inventory(
    sha: str,
    *,
    stdout: Callable[[list[str]], str],
    extra_control_paths: frozenset[str] = frozenset(),
) -> dict[str, tuple[str, str, str]]:
    """Compare Git blob identity AND mode; never import descendant source."""
    tree = stdout(["git", "ls-tree", "--full-tree", "-r", "-z", sha])
    inventory: dict[str, tuple[str, str, str]] = {}
    seen: set[str] = set()
    for entry in tree.split("\0"):
        if not entry:
            continue
        try:
            metadata, path = entry.split("\t", 1)
            mode, kind, object_sha = metadata.split(" ")
        except ValueError:
            raise CandidateVerificationError(
                "protected control inventory is invalid"
            ) from None
        if (
            not path
            or path in seen
            or path.startswith("/")
            or any(part in {".", "..", ""} for part in path.split("/"))
            or any(ord(character) < 32 or ord(character) == 127 for character in path)
            or FULL_SHA.fullmatch(object_sha) is None
            or (mode, kind)
            not in {
                ("100644", "blob"),
                ("100755", "blob"),
                ("120000", "blob"),
                ("160000", "commit"),
            }
        ):
            raise CandidateVerificationError("protected control inventory is invalid")
        seen.add(path)
        if path in extra_control_paths or not path.startswith(CANDIDATE_ONLY_PREFIXES):
            inventory[path] = (mode, kind, object_sha)
    if not REQUIRED_CONTROL_PATHS <= inventory.keys():
        raise CandidateVerificationError("protected control inventory is incomplete")
    return inventory


def _verify_audited_controller(
    controller_sha: str, *, stdout: Callable[[list[str]], str]
) -> frozenset[str]:
    if type(controller_sha) is not str or FULL_SHA.fullmatch(controller_sha) is None:
        raise CandidateVerificationError("controller SHA is invalid")
    raw = stdout(["git", "show", f"{controller_sha}:{AUDITED_DEPENDENCIES_PATH}"])
    if hashlib.sha256(raw.encode()).hexdigest() != AUDITED_DEPENDENCIES_SHA256:
        raise CandidateVerificationError(
            "canonical dependency audit manifest changed or is missing"
        )
    try:
        audit = json.loads(raw)
        files = audit["files"]
        paths = frozenset(files)
    except (ValueError, KeyError, TypeError):
        raise CandidateVerificationError(
            "canonical dependency audit manifest is invalid"
        ) from None
    inventory = _control_inventory(
        controller_sha, stdout=stdout, extra_control_paths=paths
    )
    selected = {path: inventory.get(path) for path in files}
    observed = hashlib.sha256(
        json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if observed != audit["control_inventory_sha256"]:
        raise CandidateVerificationError(
            "canonical controller dependency bytes or mode are unaudited"
        )
    return paths


def _verify_unchanged_descendant_controls(
    controller_sha: str,
    current_master: str,
    *,
    stdout: Callable[[list[str]], str],
    commit_exists: Callable[[str], bool],
    is_ancestor: Callable[[str, str], bool],
    audited_dependencies: frozenset[str] | None = None,
) -> None:
    if audited_dependencies is None:
        audited_dependencies = _verify_audited_controller(controller_sha, stdout=stdout)
    if not commit_exists(current_master):
        # Fetch only the authenticated exact SHA. No refs, working tree,
        # submodules, hooks or descendant scripts are updated/executed.
        stdout(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "fetch",
                "--no-tags",
                "--no-write-fetch-head",
                "--no-recurse-submodules",
                "--no-auto-maintenance",
                f"https://github.com/{REPOSITORY}.git",
                current_master,
            ]
        )
    if not commit_exists(current_master) or not is_ancestor(
        controller_sha, current_master
    ):
        raise CandidateVerificationError(
            "current protected master is not a descendant of the dispatched controls"
        )
    if _control_inventory(
        controller_sha, stdout=stdout, extra_control_paths=audited_dependencies
    ) != _control_inventory(
        current_master, stdout=stdout, extra_control_paths=audited_dependencies
    ):
        raise CandidateVerificationError(
            "protected deployment controls changed after dispatch; start a new reviewed deployment"
        )


def _verify_candidate_source(
    *,
    candidate_sha: str,
    approval_pr_number: str,
    expected_schema_after: str,
    controller_sha: str,
    repository: str,
    stdout: Callable[[list[str]], str],
    commit_exists: Callable[[str], bool],
    is_ancestor: Callable[[str, str], bool],
    reverify: bool = False,
    audited_dependencies: frozenset[str] | None = None,
) -> dict[str, str | int]:
    """Shared source decision only; no actor or execution authority."""
    if repository != REPOSITORY or not all(
        type(value) is str
        for value in (
            candidate_sha,
            approval_pr_number,
            expected_schema_after,
            controller_sha,
        )
    ):
        raise CandidateVerificationError("invalid candidate source context")
    if FULL_SHA.fullmatch(candidate_sha) is None:
        raise CandidateVerificationError(
            "candidate SHA must be a full lowercase 40-character commit SHA"
        )
    if FULL_SHA.fullmatch(controller_sha) is None:
        raise CandidateVerificationError("controller SHA is invalid")
    if PR_NUMBER.fullmatch(approval_pr_number) is None:
        raise CandidateVerificationError("approval PR number is invalid")
    if SCHEMA_REVISION.fullmatch(expected_schema_after) is None:
        raise CandidateVerificationError(
            "expected schema-after revision must be 12 lowercase hexadecimal characters"
        )

    local_head = stdout(["git", "rev-parse", "HEAD"])
    if local_head != controller_sha:
        raise CandidateVerificationError(
            "verifier checkout does not match the workflow SHA"
        )

    current_master = _protected_master_sha(repository, stdout=stdout)
    if current_master != controller_sha:
        if not reverify:
            raise CandidateVerificationError(
                "master advanced after dispatch; start a new reviewed deployment"
            )
        _verify_unchanged_descendant_controls(
            controller_sha,
            current_master,
            stdout=stdout,
            commit_exists=commit_exists,
            is_ancestor=is_ancestor,
            audited_dependencies=audited_dependencies,
        )
    if not commit_exists(candidate_sha):
        raise CandidateVerificationError("candidate commit is unavailable")
    if not is_ancestor(candidate_sha, current_master):
        raise CandidateVerificationError(
            "candidate is not contained in current protected master"
        )

    schema_head = _candidate_schema_head(candidate_sha, stdout=stdout)
    if schema_head != expected_schema_after:
        raise CandidateVerificationError(
            "expected schema-after revision does not match the candidate migration head"
        )

    pull_text = stdout(
        [
            "gh",
            "api",
            "--hostname",
            GITHUB_HOSTNAME,
            f"repos/{repository}/pulls/{approval_pr_number}",
        ]
    )
    try:
        pull: dict[str, Any] = json.loads(pull_text)
    except (TypeError, json.JSONDecodeError):
        raise CandidateVerificationError("approval PR metadata is invalid") from None
    if not isinstance(pull, dict):
        raise CandidateVerificationError("approval PR metadata is invalid")
    base = pull.get("base")
    if (
        pull.get("state") != "closed"
        or not pull.get("merged_at")
        or not isinstance(base, dict)
        or base.get("ref") != "master"
        or pull.get("merge_commit_sha") != candidate_sha
    ):
        raise CandidateVerificationError(
            "candidate is not the merge commit of the supplied approved master PR"
        )

    final_master = _protected_master_sha(repository, stdout=stdout)
    if final_master != current_master:
        raise CandidateVerificationError(
            "master advanced during candidate verification; start a new reviewed deployment"
        )

    return {
        "approval_pr_number": int(approval_pr_number),
        "candidate_sha": candidate_sha,
        "master_sha": current_master,
        "schema_head": schema_head,
    }


def verify_candidate(
    context: CandidateContext,
    *,
    stdout: Callable[[list[str]], str] = _stdout,
    commit_exists: Callable[[str], bool] = _commit_exists,
    is_ancestor: Callable[[str, str], bool] = _is_ancestor,
) -> dict[str, str | int]:
    if type(context.reverify) is not bool:
        raise CandidateVerificationError("candidate verification phase is invalid")
    if context.repository != REPOSITORY:
        raise CandidateVerificationError("unexpected repository")
    if context.event_name != "workflow_dispatch":
        raise CandidateVerificationError(
            "staging deployment must be manually dispatched"
        )
    if context.ref != MASTER_REF or context.ref_protected.lower() != "true":
        raise CandidateVerificationError(
            "staging deployment controls must run from protected master"
        )
    if context.actor != FOUNDER_LOGIN or context.triggering_actor != FOUNDER_LOGIN:
        raise CandidateVerificationError(
            "staging deployment and reruns are restricted to the founder account"
        )
    audited_dependencies = _verify_audited_controller(
        context.workflow_sha, stdout=stdout
    )
    return _verify_candidate_source(
        candidate_sha=context.candidate_sha,
        approval_pr_number=context.approval_pr_number,
        expected_schema_after=context.expected_schema_after,
        controller_sha=context.workflow_sha,
        repository=context.repository,
        stdout=stdout,
        commit_exists=commit_exists,
        is_ancestor=is_ancestor,
        reverify=context.reverify,
        audited_dependencies=audited_dependencies,
    )


def _human_github_user(stdout: Callable[[list[str]], str]) -> str:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate metadata key")
            result[key] = value
        return result

    try:
        raw = stdout(
            ["gh", "api", "--hostname", GITHUB_HOSTNAME, "--method", "GET", "user"]
        )
        user = json.loads(raw, object_pairs_hook=unique)
        if (
            not isinstance(user, dict)
            or user.get("login") != FOUNDER_LOGIN
            or user.get("type") != "User"
            or type(user.get("id")) is not int
            or user["id"] <= 0
        ):
            raise ValueError("unexpected configured GitHub user")
    except (ValueError, TypeError):
        raise CandidateVerificationError("human GitHub identity is invalid") from None
    identity = {"id": user["id"], "login": user["login"], "type": user["type"]}
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def verify_human_candidate(
    context: HumanCandidateContext,
    *,
    stdout: Callable[[list[str]], str],
    commit_exists: Callable[[str], bool],
    is_ancestor: Callable[[str, str], bool],
) -> dict[str, str | int]:
    """Human-route source decision with REQUIRED externally bound read transport.

    No default runner, CLI flag, credentials, presence, plan or release authority.
    A reviewed human controller must bind bounded authenticated GET-only transport
    and revalidate this decision at each privileged boundary. Mock callbacks or
    matching GITHUB_* strings do not authenticate a user or authorize execution.
    Azure configured-user/same-token identity, role disclosure, founder candidate
    selection, review, rehearsal, expiry and human execution remain separate gates.
    """
    if (
        not isinstance(context, HumanCandidateContext)
        or context.repository != REPOSITORY
    ):
        raise CandidateVerificationError("invalid human candidate context")
    user_before = _human_github_user(stdout)
    if stdout(["git", "remote", "get-url", "origin"]) != (
        "https://github.com/" + REPOSITORY + ".git"
    ):
        raise CandidateVerificationError("human controller origin is invalid")
    if stdout(["git", "status", "--porcelain=v1", "--untracked-files=all"]):
        raise CandidateVerificationError("human controller checkout is dirty")
    result = _verify_candidate_source(
        candidate_sha=context.candidate_sha,
        approval_pr_number=context.approval_pr_number,
        expected_schema_after=context.expected_schema_after,
        controller_sha=context.controller_sha,
        repository=context.repository,
        stdout=stdout,
        commit_exists=commit_exists,
        is_ancestor=is_ancestor,
    )
    if _human_github_user(stdout) != user_before:
        raise CandidateVerificationError(
            "human GitHub identity changed during verification"
        )
    if stdout(["git", "rev-parse", "HEAD"]) != context.controller_sha:
        raise CandidateVerificationError(
            "human controller HEAD changed during verification"
        )
    if stdout(["git", "remote", "get-url", "origin"]) != (
        "https://github.com/" + REPOSITORY + ".git"
    ):
        raise CandidateVerificationError(
            "human controller origin changed during verification"
        )
    if stdout(["git", "status", "--porcelain=v1", "--untracked-files=all"]):
        raise CandidateVerificationError(
            "human controller checkout changed during verification"
        )
    return result | {
        "github_user_sha256": user_before,
        "execution_authorized": False,
        "deployment_authorized": False,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reverify",
        action="store_true",
        help="Reverify after initial authorization; permit only unchanged protected descendant controls.",
    )
    arguments = {
        "--candidate-sha": "CANDIDATE_SHA",
        "--approval-pr-number": "APPROVAL_PR_NUMBER",
        "--expected-schema-after": "EXPECTED_SCHEMA_AFTER",
        "--workflow-sha": "GITHUB_SHA",
        "--repository": "GITHUB_REPOSITORY",
        "--event-name": "GITHUB_EVENT_NAME",
        "--ref": "GITHUB_REF",
        "--ref-protected": "GITHUB_REF_PROTECTED",
        "--actor": "GITHUB_ACTOR",
        "--triggering-actor": "GITHUB_TRIGGERING_ACTOR",
    }
    for flag, environment_name in arguments.items():
        default = os.environ.get(environment_name) or None
        parser.add_argument(flag, default=default, required=default is None)
    return parser.parse_args(argv)


def main() -> int:
    args = _parse_args()
    context = CandidateContext(
        candidate_sha=args.candidate_sha,
        approval_pr_number=args.approval_pr_number,
        expected_schema_after=args.expected_schema_after,
        workflow_sha=args.workflow_sha,
        repository=args.repository,
        event_name=args.event_name,
        ref=args.ref,
        ref_protected=args.ref_protected,
        actor=args.actor,
        triggering_actor=args.triggering_actor,
        reverify=args.reverify,
    )
    try:
        result = verify_candidate(context)
    except CandidateVerificationError as error:
        print(f"staging candidate verification failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
