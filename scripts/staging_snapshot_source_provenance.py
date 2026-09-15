"""Read-only protected GitHub/Git source collection for the human snapshot.

External expected head and all eighteen hashes must come from an actual whole-
bundle independent review. This collector cannot establish that review itself.
No merge, fetch, status write, plan/handoff, claim, receipt or human consumer.
The caller preserves returned public evidence exclusively and obtains its review.
"""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import selectors
import signal
import stat
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

REPOSITORY = "CosmaNeura/cosmaneura"
API = "repos/" + REPOSITORY
CHECKS = ["Backend", "Frontend", "change-budget"]
GH = Path("/opt/homebrew/Cellar/gh/2.97.0/bin/gh")
GIT = Path("/usr/bin/git")
PS = Path("/bin/ps")
TOOL_PINS = {
    GH: (38983666, "6a2ab5fa89553eac1f0df50a26a5eaeea9a665d8971f5a51b32487b72c708f5c"),
    GIT: (118640, "b8763cf250e607a778bb4603cecb5b90338814d0a3dfcba0d57b1de242f610e9"),
    PS: (170432, "3bbba882e30d91fc4ff6e6844ca7c59fef4351b9a3fea35dca55708d6d487d1c"),
}
SOURCE_NAMES = {
    "staging_encrypted_workspace.py",
    *(
        "staging_snapshot_" + name + ".py"
        for name in (
            "artifacts",
            "azure",
            "bootstrap",
            "capture",
            "cli",
            "configuration",
            "console",
            "human",
            "identity",
            "native_files",
            "operations",
            "pg_io",
            "postgresql_runtime",
            "public_ip",
            "runtime",
            "session",
            "workspace",
        )
    ),
}


class SourceProvenanceRefused(RuntimeError):
    pass


def require(value: object) -> None:
    if not value:
        raise SourceProvenanceRefused("snapshot_source_provenance_refused")


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def commit(value: object) -> str:
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{40}", value))
    return cast(str, value)


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        require(key not in value)
        value[key] = item
    return value


def reject_constant(_value: str) -> None:
    raise SourceProvenanceRefused("snapshot_source_provenance_refused")


def record(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            raw, object_pairs_hook=unique, parse_constant=reject_constant
        )
    except (ValueError, UnicodeError, RecursionError):
        raise SourceProvenanceRefused("snapshot_source_provenance_refused") from None
    require(type(value) is dict)
    return value


def tool_identity(path: Path) -> tuple:
    size, expected = TOOL_PINS[path]
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_size == size)
    require(before.st_uid in {0, os.geteuid()} and not before.st_mode & 0o022)
    with path.open("rb") as stream:
        raw = stream.read(size + 1)
    after = path.lstat()
    fields = (
        "st_dev",
        "st_ino",
        "st_uid",
        "st_mode",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    identity = tuple(getattr(before, field) for field in fields)
    require(identity == tuple(getattr(after, field) for field in fields))
    require(len(raw) == size and sha(raw) == expected)
    return identity


def closed_arguments(argv: list[str]) -> None:
    require(type(argv) is list and all(type(arg) is str for arg in argv))
    if argv[:2] == [str(GIT), "--no-replace-objects"]:
        args = argv[2:]
        if args in (
            ["remote", "get-url", "origin"],
            ["rev-parse", "--is-shallow-repository"],
            ["rev-parse", "--path-format=absolute", "--git-common-dir"],
        ):
            return
        if len(args) == 3 and args[:2] == ["cat-file", "commit"]:
            commit(args[2])
            return
        if len(args) == 3 and args[:2] == ["cat-file", "blob"]:
            revision, separator, name = args[2].partition(":scripts/")
            require(separator and name in SOURCE_NAMES)
            commit(revision)
            return
        if len(args) == 4 and args[:2] == ["merge-base", "--is-ancestor"]:
            commit(args[2])
            commit(args[3])
            return
    prefix = [
        str(GH),
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
    route = argv[-1]
    require(route.startswith(API + "/"))
    suffix = route[len(API) + 1 :]
    require(
        suffix
        in {
            "branches/master",
            "branches/master/protection",
            "rulesets/3778800",
            "actions/permissions",
        }
        or re.fullmatch(r"pulls/[1-9][0-9]{0,8}", suffix)
        or re.fullmatch(
            r"commits/[0-9a-f]{40}/(?:status\?per_page=100|check-runs\?filter=all&per_page=100)",
            suffix,
        )
    )


def group_members(process_id: int, deadline: float) -> tuple[tuple[int, str], ...]:
    """Inspect only the owned PGID; denied/ambiguous reads are not absence."""
    identity = tool_identity(PS)
    remaining = deadline - time.monotonic()
    require(remaining > 0)
    result = subprocess.run(
        [str(PS), "-g", str(process_id), "-o", "pid=,pgid=,stat="],
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=min(remaining, 1),
        check=False,
    )
    require(deadline - time.monotonic() > 0)
    require(tool_identity(PS) == identity and len(result.stdout) <= 16384)
    require(deadline - time.monotonic() > 0)
    if result.returncode == 1 and not result.stdout.strip():
        return ()
    require(result.returncode == 0 and result.stdout.strip())
    members = []
    for line in result.stdout.decode("ascii").splitlines():
        fields = line.split()
        require(
            len(fields) == 3
            and all(value.isdigit() for value in fields[:2])
            and int(fields[1]) == process_id
            and re.fullmatch(r"[A-Z][A-Za-z+<>s]*", fields[2])
        )
        members.append((int(fields[0]), fields[2]))
    return tuple(members)


def finish_group(process: subprocess.Popen[bytes]) -> None:
    """Terminate descendants while leader PID is allocated, then reap once."""
    require(process.returncode is None)
    deadline = time.monotonic() + 2

    def inspect_unreaped() -> tuple[tuple[int, str], ...]:
        members = group_members(process.pid, deadline)
        require(
            members
            and len({pid for pid, _state in members}) == len(members)
            and any(pid == process.pid for pid, _state in members)
        )
        return members

    inspection_failed = False
    try:
        members = inspect_unreaped()
    except BaseException:
        # An ambiguous read still requires cancellation and can never become
        # accepted evidence, even if later cleanup succeeds.
        inspection_failed, members = True, None
    try:
        # A proven lone zombie needs no signal (macOS may reject that signal).
        # Every other state gets one cancellation attempt before any reap.
        if members is None or len(members) != 1 or not members[0][1].startswith("Z"):
            os.killpg(process.pid, signal.SIGKILL)
        while True:
            if (
                members is not None
                and len(members) == 1
                and members[0][1].startswith("Z")
            ):
                break
            remaining = deadline - time.monotonic()
            require(remaining > 0)
            time.sleep(min(0.01, remaining))
            members = inspect_unreaped()
    finally:
        # No signal may follow this wait: even error paths must not target a
        # recycled PID/PGID. A failed inspection or surviving group refuses.
        process.wait(timeout=max(0.01, deadline - time.monotonic()))
    members = group_members(process.pid, deadline)
    require(deadline - time.monotonic() > 0)
    require(not members and not inspection_failed)


def read_command(
    argv: list[str],
    root: Path,
    environment: dict[str, str],
    deadline: float,
    maximum: int,
) -> tuple[int, bytes]:
    """Bound stdout and retire the owned group on success as well as failure."""
    require(deadline > time.monotonic() and 0 < maximum <= 8 * 1024**2)
    process = subprocess.Popen(
        argv,
        cwd=root,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    chunks, size = [], 0
    try:
        require(process.stdout is not None)
        output = cast(Any, process.stdout)
        os.set_blocking(output.fileno(), False)
        with selectors.DefaultSelector() as selector:
            selector.register(output, selectors.EVENT_READ)
            ended = False
            while not ended:
                remaining = deadline - time.monotonic()
                require(remaining > 0)
                for _, _events in selector.select(min(remaining, 1)):
                    chunk = os.read(output.fileno(), min(65536, maximum - size + 1))
                    if not chunk:
                        ended = True
                        break
                    size += len(chunk)
                    require(size <= maximum)
                    chunks.append(chunk)
        # WNOWAIT observes exit without releasing the leader's PID. Its PGID
        # remains safe to signal if a descendant closed stdout and stayed alive.
        while True:
            remaining = deadline - time.monotonic()
            require(remaining > 0)
            status = os.waitid(
                os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT
            )
            if status is not None:
                require(status.si_pid == process.pid)
                require(status.si_code in {os.CLD_EXITED, os.CLD_KILLED, os.CLD_DUMPED})
                result = (
                    status.si_status
                    if status.si_code == os.CLD_EXITED
                    else -status.si_status
                )
                break
            time.sleep(min(0.01, remaining))
    finally:
        try:
            finish_group(process)
        finally:
            if process.stdout is not None:
                process.stdout.close()
    require(time.monotonic() < deadline)
    return result, b"".join(chunks)


class ReadOnlySource:
    def __init__(self, root: Path):
        require(type(root) is Path or isinstance(root, Path))
        require(
            root.is_absolute() and root.resolve(strict=True) == root and root.is_dir()
        )
        require(os.getuid() == os.geteuid() and os.geteuid() != 0)
        self.root = root
        self.started, self.deadline = time.monotonic(), time.monotonic() + 180
        self.started_utc = datetime.now(UTC)
        self.tools = {path: tool_identity(path) for path in TOOL_PINS}
        self.evidence: list[tuple[str, bytes]] = []
        self.environment = {
            "HOME": pwd.getpwuid(os.geteuid()).pw_dir,
            "PATH": "/usr/bin:/bin",
            "LANG": "C",
            "LC_ALL": "C",
            "GH_HOST": "github.com",
            "GH_PROMPT_DISABLED": "1",
            "NO_COLOR": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
        }

    def run(
        self, label: str, argv: list[str], maximum: int, codes: set[int] = {0}
    ) -> bytes:
        closed_arguments(argv)
        require(type(label) is str and re.fullmatch(r"[A-Za-z0-9._-]{1,100}", label))
        require(label not in {name for name, _ in self.evidence})
        tool = Path(argv[0])
        require(tool in self.tools and tool_identity(tool) == self.tools[tool])
        status, raw = read_command(
            argv,
            self.root,
            self.environment,
            min(self.deadline, time.monotonic() + 30),
            maximum,
        )
        require(status in codes and tool_identity(tool) == self.tools[tool])
        self.evidence.append((label, raw))
        return raw

    def github(self, label: str, route: str) -> dict[str, Any]:
        # Routes are constructed solely inside collect_provenance below.
        require(route.startswith(API + "/") and ".." not in route)
        return record(
            self.run(
                label,
                [
                    str(GH),
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
                ],
                8 * 1024**2,
            )
        )

    def git(self, label: str, *arguments: str, maximum: int = 128 * 1024) -> bytes:
        return self.run(label, [str(GIT), "--no-replace-objects", *arguments], maximum)


def protection(value: dict[str, Any], ruleset: dict[str, Any]) -> None:
    checks = value.get("required_status_checks")
    require(type(checks) is dict and checks.get("strict") is True)
    checks = cast(dict[str, Any], checks)
    require(checks.get("contexts") == CHECKS)
    require(
        checks.get("checks") == [{"context": name, "app_id": None} for name in CHECKS]
    )
    for key, expected in (
        ("enforce_admins", True),
        ("allow_force_pushes", False),
        ("allow_deletions", False),
        ("required_conversation_resolution", True),
    ):
        item = value.get(key)
        require(type(item) is dict and item.get("enabled") is expected)
    reviews = value.get("required_pull_request_reviews")
    require(type(reviews) is dict and reviews.get("dismiss_stale_reviews") is True)
    reviews = cast(dict[str, Any], reviews)
    require(
        type(reviews.get("required_approving_review_count")) is int
        and reviews["required_approving_review_count"] == 0
    )
    require(
        reviews.get("require_code_owner_reviews") is False
        and reviews.get("require_last_push_approval") is False
    )
    require(ruleset.get("id") == 3778800 and ruleset.get("target") == "branch")
    require(
        ruleset.get("source") == REPOSITORY
        and ruleset.get("source_type") == "Repository"
    )
    require(
        ruleset.get("enforcement") == "active" and ruleset.get("bypass_actors") == []
    )
    require(
        ruleset.get("conditions")
        == {"ref_name": {"exclude": [], "include": ["refs/heads/master"]}}
    )
    require(
        canonical(ruleset.get("rules"))
        == canonical(
            [
                {"type": "non_fast_forward"},
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "strict_required_status_checks_policy": True,
                        "do_not_enforce_on_create": True,
                        "required_status_checks": [{"context": "change-budget"}],
                    },
                },
            ]
        )
    )


def statuses(value: dict[str, Any], runs: dict[str, Any], expected_head: str) -> None:
    require(value.get("sha") == expected_head)
    rows, checks = value.get("statuses"), runs.get("check_runs")
    require(type(rows) is list and type(checks) is list)
    rows, checks = cast(list[Any], rows), cast(list[Any], checks)
    require(
        type(value.get("total_count")) is int
        and value["total_count"] == len(rows) <= 100
    )
    require(
        type(runs.get("total_count")) is int
        and runs["total_count"] == len(checks) <= 100
    )
    seen, status_names = set(), set()
    for row in rows:
        require(type(row) is dict and type(row.get("context")) is str)
        row = cast(dict[str, Any], row)
        name = row["context"]
        require(name not in status_names)
        status_names.add(name)
        if name in CHECKS:
            require(row.get("state") == "success")
            seen.add(name)
    for row in checks:
        require(type(row) is dict and row.get("head_sha") == expected_head)
        row = cast(dict[str, Any], row)
        if row.get("name") in CHECKS:
            require(
                row.get("status") == "completed" and row.get("conclusion") == "success"
            )
            seen.add(row["name"])
    require(seen == set(CHECKS))


@dataclass(frozen=True)
class SourceCollection:
    provenance: bytes
    evidence: tuple[tuple[str, bytes], ...]
    evidence_hashes: bytes
    source_files: tuple[tuple[str, bytes], ...]


def collect_provenance(
    root: Path,
    *,
    source_commit: str,
    pr_number: int,
    reviewed_head: str,
    expected_files: dict[str, str],
    expected_bundle_sha256: str,
) -> SourceCollection:
    """Only call after review of the entire expected bundle and normal source merge.

    This is public preparation, not the human snapshot consumer. It performs
    bounded GitHub GETs/local Git reads only and returns evidence without writes.
    Missing local objects require a separate ordinary fetch; this never fetches.
    """
    commit(source_commit)
    commit(reviewed_head)
    require(type(pr_number) is int and 0 < pr_number < 10**9)
    require(type(expected_files) is dict and set(expected_files) == SOURCE_NAMES)
    require(
        all(
            type(v) is str and re.fullmatch(r"[0-9a-f]{64}", v)
            for v in expected_files.values()
        )
    )
    require(sha(canonical(expected_files)) == expected_bundle_sha256)
    expected_files = dict(expected_files)
    try:
        reader = ReadOnlySource(root)
        remote = (
            reader.git("git-origin.txt", "remote", "get-url", "origin").decode().strip()
        )
        require(
            remote
            in {
                "https://github.com/" + REPOSITORY + ".git",
                "git@github.com:" + REPOSITORY + ".git",
            }
        )
        require(
            reader.git("git-shallow.txt", "rev-parse", "--is-shallow-repository")
            == b"false\n"
        )
        common = Path(
            reader.git(
                "git-common.txt",
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            )
            .decode()
            .strip()
        )
        require(common.is_absolute() and not (common / "info/grafts").exists())
        branch = reader.github("master-before.json", API + "/branches/master")
        require(branch.get("name") == "master" and branch.get("protected") is True)
        require(branch.get("commit", {}).get("sha") == source_commit)
        policy = reader.github(
            "protection-before.json", API + "/branches/master/protection"
        )
        rules = reader.github("ruleset-before.json", API + "/rulesets/3778800")
        protection(policy, rules)
        actions = reader.github("actions-before.json", API + "/actions/permissions")
        require(actions.get("enabled") is False)
        pr = reader.github("pull-request.json", API + "/pulls/" + str(pr_number))
        require(
            pr.get("number") == pr_number
            and pr.get("state") == "closed"
            and pr.get("merged") is True
        )
        require(pr.get("base", {}).get("ref") == "master")
        for side in ("head", "base"):
            require(pr.get(side, {}).get("repo", {}).get("full_name") == REPOSITORY)
        require(pr["head"].get("sha") == reviewed_head)
        merged = commit(pr.get("merge_commit_sha"))
        merge_raw = reader.git("merge-object.txt", "cat-file", "commit", merged)
        require(
            hashlib.sha1(
                b"commit " + str(len(merge_raw)).encode() + b"\0" + merge_raw
            ).hexdigest()
            == merged
        )
        parents = [
            line.removeprefix(b"parent ").decode()
            for line in merge_raw.split(b"\n\n", 1)[0].splitlines()
            if line.startswith(b"parent ")
        ]
        require(len(parents) == 2 and parents[1] == reviewed_head)
        commit(parents[0])
        require(
            reader.git(
                "merge-ancestor.txt",
                "merge-base",
                "--is-ancestor",
                merged,
                source_commit,
            )
            == b""
        )
        state = reader.github(
            "statuses.json", API + "/commits/" + reviewed_head + "/status?per_page=100"
        )
        runs = reader.github(
            "check-runs.json",
            API + "/commits/" + reviewed_head + "/check-runs?filter=all&per_page=100",
        )
        statuses(state, runs, reviewed_head)
        source_files = []
        for name in sorted(SOURCE_NAMES):
            expected = expected_files[name]
            head_raw = reader.git(
                "reviewed-" + name,
                "cat-file",
                "blob",
                reviewed_head + ":scripts/" + name,
            )
            source_raw = reader.git(
                "protected-" + name,
                "cat-file",
                "blob",
                source_commit + ":scripts/" + name,
            )
            require(head_raw == source_raw and sha(source_raw) == expected)
            source_files.append((name, source_raw))
        statuses(
            reader.github(
                "statuses-after.json",
                API + "/commits/" + reviewed_head + "/status?per_page=100",
            ),
            reader.github(
                "check-runs-after.json",
                API
                + "/commits/"
                + reviewed_head
                + "/check-runs?filter=all&per_page=100",
            ),
            reviewed_head,
        )
        after = reader.github("master-after.json", API + "/branches/master")
        require(after.get("name") == "master" and after.get("protected") is True)
        require(after.get("commit", {}).get("sha") == source_commit)
        after_policy = reader.github(
            "protection-after.json", API + "/branches/master/protection"
        )
        after_rules = reader.github("ruleset-after.json", API + "/rulesets/3778800")
        protection(after_policy, after_rules)
        require(
            canonical(policy) == canonical(after_policy)
            and canonical(rules) == canonical(after_rules)
        )
        require(
            reader.github("actions-after.json", API + "/actions/permissions").get(
                "enabled"
            )
            is False
        )
        observed = datetime.now(UTC)
        elapsed = time.monotonic() - reader.started
        require(
            0 <= elapsed < 180
            and abs((observed - reader.started_utc).total_seconds() - elapsed) <= 5
        )
        provenance = {
            "schema_version": 1,
            "repository": REPOSITORY,
            "protected_branch": "master",
            "source_commit": source_commit,
            "observed_at": reader.started_utc.isoformat().replace("+00:00", "Z"),
            "required_checks": CHECKS,
            "enforce_admins": True,
            "allow_force_pushes": False,
            "allow_deletions": False,
            "files": expected_files,
            "pull_requests": [
                {
                    "number": pr_number,
                    "head_commit": reviewed_head,
                    "merge_commit": merged,
                    "state": "MERGED",
                    "reviewed_files": expected_files,
                }
            ],
        }
        return SourceCollection(
            canonical(provenance),
            tuple(reader.evidence),
            canonical({name: sha(raw) for name, raw in reader.evidence}),
            tuple(source_files),
        )
    except BaseException:
        raise SourceProvenanceRefused("snapshot_source_provenance_refused") from None


if __name__ == "__main__":
    raise SystemExit(
        "snapshot_source_collection_requires_independent_whole_bundle_review"
    )
