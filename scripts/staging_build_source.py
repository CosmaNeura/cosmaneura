"""Bounded real Git/GitHub read transport for the staging build controller.

This is source evidence, NOT founder selection, human presence, Azure identity,
review acceptance or execution permission. An independently reviewed launcher
binds the exact request/closure to actual same-task authorization. No CLI, write
API, fetch, shell, credential export, build, publication or human consumer runs.
The existing snapshot transport and candidate verifier are reused unchanged.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import resource
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import staging_human_build_inputs as inputs
import staging_human_build_process as process
import staging_snapshot_source_provenance as source
import verify_staging_candidate as candidate

MODULES = frozenset(
    {
        "staging_build_source.py",
        "staging_build_controller.py",
        "staging_human_build_inputs.py",
        "staging_human_build_executor.py",
        "staging_human_build_process.py",
        "staging_snapshot_source_provenance.py",
        "verify_staging_candidate.py",
    }
)


class BuildSourceRefused(RuntimeError):
    """Content-free source refusal; no retry or live operation is authorized."""


def require(condition: object) -> None:
    if not condition:
        raise BuildSourceRefused("staging_build_source_refused")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class SourceRequest:
    context: candidate.HumanCandidateContext
    github_user_sha256: str
    source_archive_sha256: str
    controller_pr: int
    controller_reviewed_head: str
    controller_review_id: int
    controller_check_id: int
    # Pinned by the caller after accepting the actual review/findings. Successful
    # completion of a provider check alone never establishes review acceptance.
    controller_review_sha256: str
    production_files: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class SourceEvidence:
    observed_at: str
    candidate_sha: str
    controller_sha: str
    github_user_sha256: str
    source_archive: bytes
    source_archive_sha256: str
    protection_sha256: str
    evidence: tuple[tuple[str, str], ...]
    execution_authorized: bool = False
    deployment_authorized: bool = False


def validate_request(request: SourceRequest) -> None:
    require(type(request) is SourceRequest)
    context = request.context
    require(type(context) is candidate.HumanCandidateContext)
    require(context.repository == source.REPOSITORY)
    for value in (
        context.candidate_sha,
        context.controller_sha,
        request.controller_reviewed_head,
    ):
        require(type(value) is str and re.fullmatch(r"[0-9a-f]{40}", value))
    require(
        type(context.approval_pr_number) is str
        and re.fullmatch(r"[1-9][0-9]{0,8}", context.approval_pr_number)
    )
    require(
        type(context.expected_schema_after) is str
        and re.fullmatch(r"[0-9a-f]{12}", context.expected_schema_after)
    )
    for value in (
        request.controller_pr,
        request.controller_review_id,
        request.controller_check_id,
    ):
        require(type(value) is int and 0 < value < 10**15)
    require(
        inputs.digest(request.github_user_sha256)
        and inputs.digest(request.source_archive_sha256)
        and inputs.digest(request.controller_review_sha256)
    )
    require(type(request.production_files) is tuple)
    require(
        all(
            type(row) is tuple
            and len(row) == 2
            and type(row[0]) is str
            and inputs.digest(row[1])
            for row in request.production_files
        )
    )
    files = dict(request.production_files)
    require(len(files) == len(request.production_files) and set(files) == MODULES)
    require(all(inputs.digest(value) for value in files.values()))


class BuildSourceReader(source.ReadOnlySource):
    """A closed build-specific argv adapter; no mutation of snapshot allowlists.

    The inherited transport owns authenticated gh children and clean Git env.
    Its token is never read/returned by this process or passed to any builder.
    Same-UID/OS integrity and the existing CLI credential store remain trusted.
    """

    def __init__(self, root: Path, request: SourceRequest):
        validate_request(request)
        super().__init__(root)
        self.request = request
        self.sequence = 0
        # Preserve response hashes, not full /user objects or review links/tokens.
        self.response_hashes: list[tuple[str, str]] = []

    def allowed_git(self, args: list[str]) -> None:
        context = self.request.context
        if args in (
            ["remote", "get-url", "origin"],
            ["rev-parse", "HEAD"],
            ["rev-parse", "--is-shallow-repository"],
            ["rev-parse", "--path-format=absolute", "--git-common-dir"],
            ["status", "--porcelain=v1", "--untracked-files=all"],
            ["cat-file", "-e", context.candidate_sha + "^{commit}"],
            [
                "merge-base",
                "--is-ancestor",
                context.candidate_sha,
                context.controller_sha,
            ],
            ["ls-tree", "-z", context.candidate_sha + ":migrations/versions"],
        ):
            return
        if len(args) == 2 and args[0] == "show":
            revision, separator, path = args[1].partition(":")
            require(separator)
            if revision == context.candidate_sha and (
                path == "alembic.ini"
                or re.fullmatch(r"migrations/versions/[A-Za-z0-9_-]+\.py", path)
            ):
                return
            if revision in {
                context.controller_sha,
                self.request.controller_reviewed_head,
            } and path.startswith("scripts/"):
                require(path.removeprefix("scripts/") in MODULES)
                return
        if len(args) == 3 and args[:2] == ["cat-file", "commit"]:
            require(re.fullmatch(r"[0-9a-f]{40}", args[2]))
            return
        if len(args) == 4 and args[:2] == ["merge-base", "--is-ancestor"]:
            require(
                re.fullmatch(r"[0-9a-f]{40}", args[2])
                and args[3] == context.controller_sha
            )
            return
        raise BuildSourceRefused("staging_build_source_refused")

    def allowed_route(self, route: str) -> None:
        prefix = source.API + "/"
        require(route == "user" or route.startswith(prefix))
        if route == "user":
            return
        suffix = route[len(prefix) :]
        if suffix in {
            "branches/master",
            "branches/master/protection",
            "rulesets/3778800",
            "actions/permissions",
            "pulls/" + self.request.context.approval_pr_number,
            "pulls/" + str(self.request.controller_pr),
            "pulls/"
            + str(self.request.controller_pr)
            + "/reviews/"
            + str(self.request.controller_review_id),
            "check-runs/" + str(self.request.controller_check_id),
        }:
            return
        require(
            re.fullmatch(
                r"commits/[0-9a-f]{40}/(?:status\?per_page=100|check-runs\?filter=all&per_page=100)",
                suffix,
            )
        )

    def tools_check(self) -> None:
        require(time.monotonic() < self.deadline)
        for path, expected in self.tools.items():
            require(source.tool_identity(path) == expected)

    def read(self, argv: list[str], maximum: int = 8 * 1024**2) -> bytes:
        require(type(argv) is list and all(type(v) is str for v in argv))
        if argv[:2] == [str(source.GIT), "--no-replace-objects"]:
            self.allowed_git(argv[2:])
        else:
            prefix = [
                str(source.GH),
                "api",
                "--hostname",
                "github.com",
                "--method",
                "GET",
                "-H",
                "Accept: application/vnd.github+json",
                "-H",
                "X-GitHub-Api-Version: 2022-11-28",
            ]
            require(len(argv) == len(prefix) + 1 and argv[:-1] == prefix)
            self.allowed_route(argv[-1])
        self.tools_check()
        code, raw = source.read_command(
            argv,
            self.root,
            self.environment,
            min(self.deadline, time.monotonic() + 30),
            maximum,
        )
        require(code == 0)
        self.tools_check()
        self.sequence += 1
        require(self.sequence <= 2000)
        self.response_hashes.append(("observation-" + str(self.sequence), sha(raw)))
        return raw

    def gh(self, route: str) -> dict[str, Any]:
        return source.record(
            self.read(
                [
                    str(source.GH),
                    "api",
                    "--hostname",
                    "github.com",
                    "--method",
                    "GET",
                    "-H",
                    "Accept: application/vnd.github+json",
                    "-H",
                    "X-GitHub-Api-Version: 2022-11-28",
                    route,
                ]
            )
        )

    def git_bytes(self, *args: str) -> bytes:
        return self.read([str(source.GIT), "--no-replace-objects", *args])

    def stdout(self, argv: list[str]) -> str:
        require(type(argv) is list and all(type(v) is str for v in argv))
        if argv and argv[0] == "git":
            return self.git_bytes(*argv[1:]).decode().strip()
        require(
            len(argv) in {5, 7}
            and argv[:4] == ["gh", "api", "--hostname", "github.com"]
        )
        require(len(argv) == 5 or argv[4:6] == ["--method", "GET"])
        value = self.gh(argv[-1])
        if argv[-1] == "user":
            require(
                value.get("login") == candidate.FOUNDER_LOGIN
                and value.get("type") == "User"
            )
            require(type(value.get("id")) is int and value["id"] > 0)
            value = {key: value[key] for key in ("id", "login", "type")}
            require(sha(source.canonical(value)) == self.request.github_user_sha256)
        return json.dumps(value)

    def commit_exists(self, revision: str) -> bool:
        require(revision == self.request.context.candidate_sha)
        require(self.git_bytes("cat-file", "-e", revision + "^{commit}") == b"")
        return True

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        require(descendant == self.request.context.controller_sha)
        require(
            self.git_bytes("merge-base", "--is-ancestor", ancestor, descendant) == b""
        )
        return True

    def protections(self) -> str:
        policy = self.gh(source.API + "/branches/master/protection")
        rules = self.gh(source.API + "/rulesets/3778800")
        source.protection(policy, rules)
        require(self.gh(source.API + "/actions/permissions").get("enabled") is False)
        return sha(source.canonical({"classic": policy, "ruleset": rules}))

    def local_closure(self) -> None:
        for name, expected in self.request.production_files:
            path = self.root / "scripts" / name
            metadata = path.lstat()
            require(path.resolve() == path and stat.S_ISREG(metadata.st_mode))
            require(
                metadata.st_nlink == 1
                and 0 < metadata.st_size <= 1024**2
                and metadata.st_uid == os.geteuid()
                and not metadata.st_mode & 0o022
            )
            require(sha(path.read_bytes()) == expected)
            fields = (
                "st_dev",
                "st_ino",
                "st_uid",
                "st_mode",
                "st_nlink",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
            after = path.lstat()
            require(
                tuple(getattr(metadata, field) for field in fields)
                == tuple(getattr(after, field) for field in fields)
            )

    def final_boundaries(self) -> None:
        """Cheap final phase observation after the longer source/review checks."""
        context = self.request.context
        self.local_closure()
        require(
            self.stdout(["git", "remote", "get-url", "origin"])
            == "https://github.com/" + source.REPOSITORY + ".git"
        )
        require(self.stdout(["git", "rev-parse", "HEAD"]) == context.controller_sha)
        require(
            self.stdout(["git", "status", "--porcelain=v1", "--untracked-files=all"])
            == ""
        )
        self.stdout(
            ["gh", "api", "--hostname", "github.com", "--method", "GET", "user"]
        )
        branch = self.gh(source.API + "/branches/master")
        require(branch.get("name") == "master" and branch.get("protected") is True)
        require(branch.get("commit", {}).get("sha") == context.controller_sha)
        self.tools_check()

    def controller(self) -> None:
        request, context = self.request, self.request.context
        pull = self.gh(source.API + "/pulls/" + str(request.controller_pr))
        require(
            pull.get("number") == request.controller_pr
            and pull.get("state") == "closed"
            and pull.get("merged") is True
        )
        require(pull.get("base", {}).get("ref") == "master")
        for side in ("base", "head"):
            require(
                pull.get(side, {}).get("repo", {}).get("full_name") == source.REPOSITORY
            )
        merged, head = (
            source.commit(pull.get("merge_commit_sha")),
            source.commit(pull.get("head", {}).get("sha")),
        )
        raw = self.git_bytes("cat-file", "commit", merged)
        require(
            hashlib.sha1(b"commit " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            == merged
        )
        parents = [
            v[7:].decode()
            for v in raw.split(b"\n\n", 1)[0].splitlines()
            if v.startswith(b"parent ")
        ]
        require(
            len(parents) == 2
            and parents[1] == head
            and head == request.controller_reviewed_head
        )
        self.is_ancestor(merged, context.controller_sha)
        review = self.gh(
            source.API
            + "/pulls/"
            + str(request.controller_pr)
            + "/reviews/"
            + str(request.controller_review_id)
        )
        check = self.gh(source.API + "/check-runs/" + str(request.controller_check_id))
        require(
            review.get("id") == request.controller_review_id
            and review.get("commit_id") == request.controller_reviewed_head
        )
        require(
            review.get("user", {}).get("login") == "cursor[bot]"
            and review.get("user", {}).get("type") == "Bot"
            and review.get("state") == "COMMENTED"
        )
        body = review.get("body")
        require(
            type(body) is str
            and "<!-- BUGBOT_REVIEW -->" in body
            and "<!-- BUGBOT_REVIEW_STALE -->" not in body
            and "Bugbot reviewed your changes and found no new issues!" in body
            and f"for commit {request.controller_reviewed_head}" in body
        )
        require(sha(source.canonical(review)) == request.controller_review_sha256)
        require(
            check.get("id") == request.controller_check_id
            and check.get("head_sha") == request.controller_reviewed_head
        )
        require(
            check.get("app", {}).get("slug") == "cursor"
            and check.get("app", {}).get("id") == 1210556
            and check.get("name") == "Cursor Bugbot"
            and check.get("status") == "completed"
            and check.get("conclusion") == "success"
        )
        source.statuses(
            self.gh(source.API + "/commits/" + head + "/status?per_page=100"),
            self.gh(
                source.API + "/commits/" + head + "/check-runs?filter=all&per_page=100"
            ),
            head,
        )
        for name, expected in request.production_files:
            reviewed = self.git_bytes(
                "show", request.controller_reviewed_head + ":scripts/" + name
            )
            current = self.git_bytes(
                "show", context.controller_sha + ":scripts/" + name
            )
            local = (self.root / "scripts" / name).read_bytes()
            require(reviewed == current == local and sha(current) == expected)

    def archive(self) -> bytes:
        """Generate exact source bytes through Git, not a hash-shaped tar comment."""
        require(resource.getrlimit(resource.RLIMIT_CORE) == (0, 0))
        self.tools_check()
        output = io.BytesIO()
        owner = process.BuildProcesses(
            min(self.deadline, time.monotonic() + 60), self.tools_check
        )
        owner.run(
            [
                str(source.GIT),
                "--no-replace-objects",
                "archive",
                "--format=tar",
                self.request.context.candidate_sha,
            ],
            self.environment,
            inputs.MAX_SOURCE_BYTES,
            output.write,
            cwd=self.root,
        )
        require(owner.child is None and not owner.cleanup_failed)
        self.tools_check()
        raw = output.getvalue()
        require(
            0 < len(raw) <= inputs.MAX_SOURCE_BYTES
            and sha(raw) == self.request.source_archive_sha256
        )
        return raw


def collect_build_source(root: Path, request: SourceRequest) -> SourceEvidence:
    """Initial full source decision; the live controller still owes phase checks.

    Requires no enabled Actions and creates no credential or workspace. Caller
    must disable core dumps before any authenticated child. No receipt/review
    file, identity hash or SourceRequest can grant execution authority itself.
    """
    require(resource.getrlimit(resource.RLIMIT_CORE) == (0, 0))
    reader = BuildSourceReader(root, request)
    require(reader.git_bytes("rev-parse", "--is-shallow-repository") == b"false\n")
    common = Path(
        reader.git_bytes("rev-parse", "--path-format=absolute", "--git-common-dir")
        .decode()
        .strip()
    )
    require(
        common.is_absolute()
        and common.resolve() == common
        and not (common / "info/grafts").exists()
    )
    before = reader.protections()
    reader.controller()
    verified = candidate.verify_human_candidate(
        request.context,
        stdout=reader.stdout,
        commit_exists=reader.commit_exists,
        is_ancestor=reader.is_ancestor,
    )
    require(verified["github_user_sha256"] == request.github_user_sha256)
    archive = reader.archive()
    # Exact protected source and configured-user observations bracket Git export.
    after = candidate.verify_human_candidate(
        request.context,
        stdout=reader.stdout,
        commit_exists=reader.commit_exists,
        is_ancestor=reader.is_ancestor,
    )
    require(after == verified and reader.protections() == before)
    reader.controller()
    reader.final_boundaries()
    return SourceEvidence(
        reader.started_utc.isoformat(),
        request.context.candidate_sha,
        request.context.controller_sha,
        request.github_user_sha256,
        archive,
        sha(archive),
        before,
        tuple(reader.response_hashes),
    )
