#!/usr/bin/env python3
"""Verify an immutable, reviewed staging candidate against protected master."""

from __future__ import annotations

import argparse
import ast
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
MASTER_REF = "refs/heads/master"
REPOSITORY = "CosmaNeura/cosmaneura"
MIGRATION_DIRECTORY = "migrations/versions"


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


def _candidate_schema_head(
    candidate_sha: str,
    *,
    stdout: Callable[[list[str]], str],
) -> str:
    tree = stdout(
        [
            "git",
            "ls-tree",
            "-r",
            "-z",
            "--name-only",
            candidate_sha,
            "--",
            MIGRATION_DIRECTORY,
        ]
    )
    paths = sorted(
        path
        for path in tree.split("\0")
        if path.startswith(f"{MIGRATION_DIRECTORY}/") and path.endswith(".py")
    )

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


def verify_candidate(
    context: CandidateContext,
    *,
    stdout: Callable[[list[str]], str] = _stdout,
    commit_exists: Callable[[str], bool] = _commit_exists,
    is_ancestor: Callable[[str, str], bool] = _is_ancestor,
) -> dict[str, str | int]:
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
    if FULL_SHA.fullmatch(context.candidate_sha) is None:
        raise CandidateVerificationError(
            "candidate SHA must be a full lowercase 40-character commit SHA"
        )
    if FULL_SHA.fullmatch(context.workflow_sha) is None:
        raise CandidateVerificationError("workflow SHA is invalid")
    if PR_NUMBER.fullmatch(context.approval_pr_number) is None:
        raise CandidateVerificationError("approval PR number is invalid")
    if SCHEMA_REVISION.fullmatch(context.expected_schema_after) is None:
        raise CandidateVerificationError(
            "expected schema-after revision must be 12 lowercase hexadecimal characters"
        )

    current_master = stdout(
        [
            "gh",
            "api",
            f"repos/{context.repository}/git/ref/heads/master",
            "--jq",
            ".object.sha",
        ]
    )
    if current_master != context.workflow_sha:
        raise CandidateVerificationError(
            "master advanced after dispatch; start a new reviewed deployment"
        )
    if not commit_exists(context.candidate_sha):
        raise CandidateVerificationError("candidate commit is unavailable")
    if not is_ancestor(context.candidate_sha, current_master):
        raise CandidateVerificationError(
            "candidate is not contained in current protected master"
        )

    schema_head = _candidate_schema_head(context.candidate_sha, stdout=stdout)
    if schema_head != context.expected_schema_after:
        raise CandidateVerificationError(
            "expected schema-after revision does not match the candidate migration head"
        )

    pull_text = stdout(
        [
            "gh",
            "api",
            f"repos/{context.repository}/pulls/{context.approval_pr_number}",
        ]
    )
    try:
        pull: dict[str, Any] = json.loads(pull_text)
    except (TypeError, json.JSONDecodeError):
        raise CandidateVerificationError("approval PR metadata is invalid") from None
    base = pull.get("base")
    if (
        pull.get("state") != "closed"
        or not pull.get("merged_at")
        or not isinstance(base, dict)
        or base.get("ref") != "master"
        or pull.get("merge_commit_sha") != context.candidate_sha
    ):
        raise CandidateVerificationError(
            "candidate is not the merge commit of the supplied approved master PR"
        )

    return {
        "approval_pr_number": int(context.approval_pr_number),
        "candidate_sha": context.candidate_sha,
        "master_sha": current_master,
        "schema_head": schema_head,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
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
