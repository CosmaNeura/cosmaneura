#!/usr/bin/env python3
"""Fetch one provenance-bound Foundry assessment from GitHub Actions.

The trusted producer is deliberately fixed.  This program accepts an assessed
commit and assessment kind, finds the matching immutable artifact, verifies the
workflow run and archive before extraction, validates the handoff contract, and
checks that the exact pull request is still open, non-draft, and unchanged
before writing the selected assessment atomically.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import ModuleType
from typing import Any, NoReturn, Sequence
from urllib.parse import quote

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INPUT = 3
EXIT_INVALID = 4
EXIT_NOT_FOUND = 5
EXIT_AMBIGUOUS = 6

TRUSTED_REPOSITORY = "CosmaNeura/cosmaneura"
TRUSTED_WORKFLOW_PATH = ".github/workflows/agent_pr_assessment.yml"
TRUSTED_WORKFLOW_NAME = "Foundry PR Assessment"
TRUSTED_BRANCH = "master"
ASSESSMENT_KINDS = (
    "deployment_assessment",
    "migration_assessment",
    "security_assessment",
)
SHA_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
DIGEST_PATTERN = re.compile(r"sha256:([0-9a-f]{64})\Z")

MAX_API_JSON_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_HANDOFF_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 16
MAX_COMPRESSION_RATIO = 200

HANDOFFCTL_PATH = Path(__file__).resolve().with_name("handoffctl.py")


class FetchError(Exception):
    """Base class for a safe, user-facing failure."""


class InputError(FetchError):
    """Local input, output, or GitHub response could not be processed."""


class InvalidProvenance(FetchError):
    """A candidate failed an integrity or provenance check."""


class NotFound(FetchError):
    """No trusted candidate exists for the requested assessment."""


class Ambiguous(FetchError):
    """More than one trusted candidate exists for the request."""


@dataclass(frozen=True)
class Artifact:
    artifact_id: int
    run_id: int
    name: str
    size_in_bytes: int
    digest: str | None


@dataclass(frozen=True)
class TrustedCandidate:
    artifact: Artifact
    run: dict[str, Any]


@dataclass(frozen=True)
class ArchiveHandoff:
    path: PurePosixPath
    contents: bytes


def _command_error(
    argv: Sequence[str], result: subprocess.CompletedProcess[bytes]
) -> str:
    command = " ".join(argv[:3])
    stderr = result.stderr.decode("utf-8", errors="replace").strip()
    if len(stderr) > 1000:
        stderr = f"{stderr[:1000]}..."
    return f"{command} failed with exit code {result.returncode}: {stderr or 'no error output'}"


def _run_gh(argv: list[str], *, timeout: int = 120) -> bytes:
    if len(argv) < 2 or argv[0] != "gh" or argv[1] not in {"api", "run"}:
        raise InputError("internal command allowlist rejected a non-GitHub command")
    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError as error:
        raise InputError("GitHub CLI 'gh' is not installed") from error
    except subprocess.TimeoutExpired as error:
        raise InputError(f"GitHub CLI timed out after {timeout} seconds") from error
    except OSError as error:
        raise InputError(f"could not execute GitHub CLI: {error}") from error
    if result.returncode != 0:
        raise InputError(_command_error(argv, result))
    return result.stdout


def _reject_non_json_constant(value: str) -> NoReturn:
    raise ValueError(f"non-JSON numeric constant {value!r}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate object key {key!r}")
        value[key] = item
    return value


def _parse_json_bytes(raw: bytes, source: str) -> Any:
    if len(raw) > MAX_API_JSON_BYTES:
        raise InputError(f"{source} exceeded the {MAX_API_JSON_BYTES}-byte limit")
    try:
        return json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_non_json_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise InputError(f"{source} is not strict UTF-8 JSON: {error}") from error


def _gh_json(endpoint: str, *, paginate: bool = False) -> Any:
    argv = ["gh", "api", endpoint]
    if paginate:
        argv.extend(["--paginate", "--slurp"])
    return _parse_json_bytes(_run_gh(argv), f"GitHub API response for {endpoint}")


def _positive_int(value: Any, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise InvalidProvenance(f"{path} must be a positive integer")
    return value


def _flatten_artifact_pages(response: Any) -> list[Any]:
    pages = response if isinstance(response, list) else [response]
    artifacts: list[Any] = []
    for index, page in enumerate(pages):
        if not isinstance(page, dict) or not isinstance(page.get("artifacts"), list):
            raise InputError(f"artifact API page {index} has an unexpected shape")
        artifacts.extend(page["artifacts"])
    return artifacts


def _artifact_from_json(value: Any, expected_name: str) -> Artifact | None:
    if not isinstance(value, dict):
        raise InputError("artifact API returned a non-object artifact")
    if value.get("name") != expected_name:
        return None
    if value.get("expired") is True:
        return None
    if value.get("expired") is not False:
        raise InvalidProvenance(
            f"artifact {expected_name!r} has no definitive expiry state"
        )

    artifact_id = _positive_int(value.get("id"), "artifact.id")
    workflow_run = value.get("workflow_run")
    if not isinstance(workflow_run, dict):
        raise InvalidProvenance(f"artifact {artifact_id} has no workflow_run binding")
    run_id = _positive_int(workflow_run.get("id"), "artifact.workflow_run.id")
    size = _positive_int(value.get("size_in_bytes"), "artifact.size_in_bytes")
    if size > MAX_ARCHIVE_BYTES:
        raise InvalidProvenance(
            f"artifact {artifact_id} exceeds the {MAX_ARCHIVE_BYTES}-byte archive limit"
        )

    digest = value.get("digest")
    if digest is not None:
        if not isinstance(digest, str) or DIGEST_PATTERN.fullmatch(digest) is None:
            raise InvalidProvenance(
                f"artifact {artifact_id} has an invalid SHA-256 digest"
            )
    return Artifact(artifact_id, run_id, expected_name, size, digest)


def _validate_run(run: Any, expected_run_id: int) -> dict[str, Any]:
    if not isinstance(run, dict):
        raise InvalidProvenance(f"workflow run {expected_run_id} is not an object")
    repository = run.get("repository")
    head_repository = run.get("head_repository")
    checks: dict[str, tuple[Any, Any]] = {
        "id": (run.get("id"), expected_run_id),
        "name": (run.get("name"), TRUSTED_WORKFLOW_NAME),
        "path": (run.get("path"), TRUSTED_WORKFLOW_PATH),
        "event": (run.get("event"), "workflow_dispatch"),
        "status": (run.get("status"), "completed"),
        "conclusion": (run.get("conclusion"), "success"),
        "head_branch": (run.get("head_branch"), TRUSTED_BRANCH),
        "repository.full_name": (
            repository.get("full_name") if isinstance(repository, dict) else None,
            TRUSTED_REPOSITORY,
        ),
        "head_repository.full_name": (
            head_repository.get("full_name")
            if isinstance(head_repository, dict)
            else None,
            TRUSTED_REPOSITORY,
        ),
    }
    failures = [
        f"{field}={actual!r} (expected {expected!r})"
        for field, (actual, expected) in checks.items()
        if actual != expected
    ]
    policy_sha = run.get("head_sha")
    if not isinstance(policy_sha, str) or SHA_PATTERN.fullmatch(policy_sha) is None:
        failures.append("head_sha is not a complete lowercase Git commit SHA")
    if failures:
        raise InvalidProvenance(
            f"workflow run {expected_run_id} is not a trusted producer run: "
            + "; ".join(failures)
        )
    return run


def _get_run(run_id: int) -> dict[str, Any]:
    response = _gh_json(
        f"repos/{TRUSTED_REPOSITORY}/actions/runs/{run_id}", paginate=False
    )
    return _validate_run(response, run_id)


def _current_master_sha() -> str:
    endpoint = f"repos/{TRUSTED_REPOSITORY}/git/ref/heads/{TRUSTED_BRANCH}"
    response = _gh_json(endpoint, paginate=False)
    if not isinstance(response, dict) or response.get("ref") != "refs/heads/master":
        raise InvalidProvenance("GitHub did not return the exact refs/heads/master ref")
    target = response.get("object")
    if not isinstance(target, dict) or target.get("type") != "commit":
        raise InvalidProvenance(
            "refs/heads/master does not resolve directly to a commit"
        )
    sha = target.get("sha")
    if not isinstance(sha, str) or SHA_PATTERN.fullmatch(sha) is None:
        raise InvalidProvenance("refs/heads/master has an invalid commit SHA")
    return sha


def _verify_current_pull_request(
    document: dict[str, Any], assessed_sha: str, current_master_sha: str
) -> None:
    subject = document["subject"]
    try:
        pull_request = _positive_int(
            subject.get("pull_request"), "handoff.subject.pull_request"
        )
    except InvalidProvenance as error:
        raise InvalidProvenance(
            f"{error}; rerun the Foundry PR Assessment workflow before consuming it"
        ) from error
    endpoint = f"repos/{TRUSTED_REPOSITORY}/pulls/{pull_request}"
    response = _gh_json(endpoint, paginate=False)
    if not isinstance(response, dict):
        raise InvalidProvenance(
            f"pull request {pull_request} metadata is not a JSON object; rerun the "
            "Foundry PR Assessment workflow before consuming it"
        )

    base = response.get("base")
    head = response.get("head")
    base_repository = base.get("repo") if isinstance(base, dict) else None
    checks: dict[str, tuple[Any, Any]] = {
        "number": (response.get("number"), pull_request),
        "state": (response.get("state"), "open"),
        "draft": (response.get("draft"), False),
        "base.ref": (
            base.get("ref") if isinstance(base, dict) else None,
            TRUSTED_BRANCH,
        ),
        "base.repo.full_name": (
            base_repository.get("full_name")
            if isinstance(base_repository, dict)
            else None,
            TRUSTED_REPOSITORY,
        ),
        "base.sha (handoff subject)": (
            base.get("sha") if isinstance(base, dict) else None,
            subject.get("base_sha"),
        ),
        "base.sha (current master)": (
            base.get("sha") if isinstance(base, dict) else None,
            current_master_sha,
        ),
        "base.sha (producer policy)": (
            base.get("sha") if isinstance(base, dict) else None,
            document["producer"]["policy_sha"],
        ),
        "head.sha": (
            head.get("sha") if isinstance(head, dict) else None,
            assessed_sha,
        ),
    }
    failures = [
        f"{field}={actual!r} (expected {expected!r})"
        for field, (actual, expected) in checks.items()
        if actual != expected
    ]
    if failures:
        raise InvalidProvenance(
            f"pull request {pull_request} is no longer the assessed open PR: "
            + "; ".join(failures)
            + "; rerun the Foundry PR Assessment workflow before consuming it"
        )


def _list_exact_artifacts(expected_name: str, run_id: int | None) -> list[Artifact]:
    if run_id is None:
        encoded_name = quote(expected_name, safe="")
        endpoint = (
            f"repos/{TRUSTED_REPOSITORY}/actions/artifacts"
            f"?name={encoded_name}&per_page=100"
        )
    else:
        endpoint = (
            f"repos/{TRUSTED_REPOSITORY}/actions/runs/{run_id}/artifacts?per_page=100"
        )
    response = _gh_json(endpoint, paginate=True)
    artifacts = []
    for value in _flatten_artifact_pages(response):
        artifact = _artifact_from_json(value, expected_name)
        if artifact is not None and (run_id is None or artifact.run_id == run_id):
            artifacts.append(artifact)
    return artifacts


def _select_candidate(expected_name: str, run_id: int | None) -> TrustedCandidate:
    artifacts = _list_exact_artifacts(expected_name, run_id)
    if not artifacts:
        raise NotFound(f"no unexpired artifact named {expected_name!r} was found")

    candidates: list[TrustedCandidate] = []
    rejected: list[str] = []
    runs: dict[int, dict[str, Any]] = {}
    for artifact in artifacts:
        try:
            run = runs.get(artifact.run_id)
            if run is None:
                run = _get_run(artifact.run_id)
                runs[artifact.run_id] = run
        except InvalidProvenance as error:
            rejected.append(str(error))
            continue
        candidates.append(TrustedCandidate(artifact, run))

    if not candidates:
        detail = f" ({'; '.join(rejected)})" if rejected else ""
        raise NotFound(f"no trusted producer run contains {expected_name!r}{detail}")
    if len(candidates) != 1:
        identities = ", ".join(
            f"run {item.artifact.run_id}/artifact {item.artifact.artifact_id}"
            for item in candidates
        )
        raise Ambiguous(
            f"multiple trusted artifacts match {expected_name!r}: {identities}"
        )
    return candidates[0]


def _download_archive(artifact: Artifact) -> bytes:
    endpoint = (
        f"repos/{TRUSTED_REPOSITORY}/actions/artifacts/{artifact.artifact_id}/zip"
    )
    raw = _run_gh(["gh", "api", endpoint])
    if not raw:
        raise InvalidProvenance(f"artifact {artifact.artifact_id} archive is empty")
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise InvalidProvenance(
            f"artifact {artifact.artifact_id} exceeds the archive byte limit"
        )
    if len(raw) != artifact.size_in_bytes:
        raise InvalidProvenance(
            f"artifact {artifact.artifact_id} size does not match GitHub metadata"
        )
    if artifact.digest is not None:
        actual = f"sha256:{hashlib.sha256(raw).hexdigest()}"
        if actual != artifact.digest:
            raise InvalidProvenance(
                f"artifact {artifact.artifact_id} archive digest does not match GitHub metadata"
            )
    return raw


def _safe_member_path(name: str) -> PurePosixPath:
    if (
        not name
        or "\x00" in name
        or "\\" in name
        or any(ord(character) < 32 for character in name)
    ):
        raise InvalidProvenance("artifact archive contains an unsafe member path")
    raw_parts = name.split("/")
    if raw_parts[-1] == "":
        raw_parts.pop()
    windows_path = PureWindowsPath(name)
    path = PurePosixPath(name)
    if (
        not raw_parts
        or path.is_absolute()
        or windows_path.drive
        or windows_path.is_absolute()
        or any(part in {"", ".", ".."} for part in raw_parts)
    ):
        raise InvalidProvenance(f"artifact archive contains unsafe path {name!r}")
    return path


def _inspect_archive(raw: bytes) -> ArchiveHandoff:
    try:
        with zipfile.ZipFile(io.BytesIO(raw), "r") as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise InvalidProvenance("artifact archive contains too many members")
            seen: set[str] = set()
            handoffs: list[ArchiveHandoff] = []
            for member in members:
                path = _safe_member_path(member.orig_filename)
                collision_key = str(path).casefold()
                if collision_key in seen:
                    raise InvalidProvenance(
                        "artifact archive contains duplicate member paths"
                    )
                seen.add(collision_key)
                if member.flag_bits & 0x1:
                    raise InvalidProvenance(
                        "artifact archive contains an encrypted member"
                    )
                unix_mode = (member.external_attr >> 16) & 0xFFFF
                file_type = stat.S_IFMT(unix_mode)
                if file_type == stat.S_IFLNK:
                    raise InvalidProvenance("artifact archive contains a symbolic link")
                if member.is_dir():
                    continue
                if file_type not in {0, stat.S_IFREG}:
                    raise InvalidProvenance(
                        "artifact archive contains a non-regular file"
                    )
                if member.file_size > MAX_HANDOFF_BYTES:
                    raise InvalidProvenance("artifact handoff exceeds the size limit")
                empty_compressed = member.file_size > 0 and member.compress_size == 0
                excessive_ratio = (
                    member.compress_size > 0
                    and member.file_size / member.compress_size > MAX_COMPRESSION_RATIO
                )
                if empty_compressed or excessive_ratio:
                    raise InvalidProvenance(
                        "artifact archive has an unsafe compression ratio"
                    )
                contents = archive.read(member)
                if len(contents) != member.file_size:
                    raise InvalidProvenance(
                        "artifact member size changed during decompression"
                    )
                if path.suffix != ".json":
                    raise InvalidProvenance("artifact archive contains a non-JSON file")
                handoffs.append(ArchiveHandoff(path, contents))
    except (zipfile.BadZipFile, RuntimeError, OSError) as error:
        raise InvalidProvenance(
            f"artifact is not a readable safe ZIP archive: {error}"
        ) from error

    if len(handoffs) != 1:
        raise InvalidProvenance(
            f"artifact archive must contain exactly one handoff JSON; found {len(handoffs)}"
        )
    return handoffs[0]


def _verify_gh_download(
    candidate: TrustedCandidate, expected: ArchiveHandoff, directory: Path
) -> Path:
    directory.mkdir(mode=0o700)
    argv = [
        "gh",
        "run",
        "download",
        str(candidate.artifact.run_id),
        "--repo",
        TRUSTED_REPOSITORY,
        "--name",
        candidate.artifact.name,
        "--dir",
        str(directory),
    ]
    _run_gh(argv)

    files: list[Path] = []
    for path in directory.rglob("*"):
        relative = path.relative_to(directory)
        try:
            metadata = path.lstat()
        except OSError as error:
            raise InputError(
                f"cannot inspect downloaded artifact path {relative}: {error}"
            ) from error
        if stat.S_ISLNK(metadata.st_mode):
            raise InvalidProvenance("downloaded artifact contains a symbolic link")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise InvalidProvenance("downloaded artifact contains a non-regular file")
        files.append(path)
    if len(files) != 1:
        raise InvalidProvenance(
            f"downloaded artifact must contain exactly one file; found {len(files)}"
        )
    selected = files[0]
    try:
        contents = selected.read_bytes()
    except OSError as error:
        raise InputError(f"cannot read downloaded handoff: {error}") from error
    if not selected.name.endswith(".json") or contents != expected.contents:
        raise InvalidProvenance(
            "gh run download contents do not match the verified artifact archive"
        )
    return selected


@lru_cache(maxsize=1)
def _load_handoffctl() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_cosmaneura_handoffctl_for_fetch", HANDOFFCTL_PATH
    )
    if spec is None or spec.loader is None:
        raise InputError(f"cannot load canonical validator at {HANDOFFCTL_PATH}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (OSError, ImportError) as error:
        raise InputError(f"cannot load canonical handoff validator: {error}") from error
    return module


def _validated_handoff(path: Path) -> dict[str, Any]:
    validator = _load_handoffctl()
    try:
        document = validator._read_document(path)
        contract, errors = validator._validate_document(document, "assessment")
    except Exception as error:
        if error.__class__.__name__ == "InputError":
            raise InvalidProvenance(
                f"canonical handoff validation failed: {error}"
            ) from error
        raise
    if contract != "assessment" or errors:
        detail = "; ".join(errors) if errors else "unexpected contract"
        raise InvalidProvenance(f"canonical handoff validation failed: {detail}")
    if not isinstance(document, dict):
        raise InvalidProvenance("validated handoff is not an object")

    claimed_digest = document.get("integrity", {}).get("artifact_digest")
    actual_digest = validator._artifact_digest(document)
    if claimed_digest != actual_digest:
        raise InvalidProvenance("handoff integrity.artifact_digest is invalid")
    return document


def _verify_handoff_binding(
    document: dict[str, Any],
    candidate: TrustedCandidate,
    assessed_sha: str,
    kind: str,
) -> None:
    subject = document.get("subject")
    producer = document.get("producer")
    expected_handoff_id = (
        f"{kind}:{TRUSTED_REPOSITORY}:{assessed_sha}:run-{candidate.artifact.run_id}"
    )
    checks = {
        "kind": (document.get("kind"), kind),
        "handoff_id": (document.get("handoff_id"), expected_handoff_id),
        "subject.repository": (
            subject.get("repository") if isinstance(subject, dict) else None,
            TRUSTED_REPOSITORY,
        ),
        "subject.head_sha": (
            subject.get("head_sha") if isinstance(subject, dict) else None,
            assessed_sha,
        ),
        "subject.workflow_run_id": (
            subject.get("workflow_run_id") if isinstance(subject, dict) else None,
            candidate.artifact.run_id,
        ),
        "producer.policy_sha": (
            producer.get("policy_sha") if isinstance(producer, dict) else None,
            candidate.run["head_sha"],
        ),
    }
    failures = [
        f"{field}={actual!r} (expected {expected!r})"
        for field, (actual, expected) in checks.items()
        if actual != expected
    ]
    if failures:
        raise InvalidProvenance(
            "handoff is not bound to the selected trusted artifact: "
            + "; ".join(failures)
        )


def _verify_handoff_freshness(document: dict[str, Any]) -> None:
    value = document["coverage"]["evidence_fresh_until"]
    try:
        fresh_until = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise InvalidProvenance(
            "handoff coverage.evidence_fresh_until is not a valid timestamp"
        ) from error
    if fresh_until <= datetime.now(timezone.utc):
        raise InvalidProvenance(
            "the exact-SHA assessment evidence has expired; rerun the Foundry PR "
            "Assessment workflow before consuming it"
        )


def _atomic_write(path: Path, contents: bytes) -> None:
    if not path.parent.is_dir():
        raise InputError(f"output directory does not exist: {path.parent}")
    descriptor: int | None = None
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        temporary_path = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = None
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    except OSError as error:
        raise InputError(f"cannot atomically write {path}: {error}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _fetch(arguments: argparse.Namespace) -> int:
    artifact_name = f"cosmaneura-agent-{arguments.kind}-{arguments.sha}"
    candidate = _select_candidate(artifact_name, arguments.run_id)
    archive = _download_archive(candidate.artifact)
    expected_handoff = _inspect_archive(archive)

    with tempfile.TemporaryDirectory(prefix="cosmaneura-agent-handoff-") as temporary:
        download_directory = Path(temporary) / "download"
        downloaded = _verify_gh_download(
            candidate, expected_handoff, download_directory
        )
        document = _validated_handoff(downloaded)
        _verify_handoff_binding(document, candidate, arguments.sha, arguments.kind)
        _verify_handoff_freshness(document)
        current_master_sha = _current_master_sha()
        if candidate.run["head_sha"] != current_master_sha:
            raise InvalidProvenance(
                "the assessment producer policy is no longer current master; "
                "rerun the Foundry PR Assessment workflow before consuming it"
            )
        _verify_current_pull_request(document, arguments.sha, current_master_sha)
        _atomic_write(arguments.output, expected_handoff.contents)

    archive_digest = candidate.artifact.digest or "unavailable"
    print(
        f"FETCHED {arguments.output} run={candidate.artifact.run_id} "
        f"artifact={candidate.artifact.artifact_id} archive_digest={archive_digest}"
    )
    return EXIT_OK


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fetch_agent_handoff",
        description=(
            "Fetch one exact-SHA Foundry assessment from the trusted GitHub Actions "
            "producer, verify its full provenance and current open non-draft PR "
            "binding, then write it atomically."
        ),
        epilog=(
            "Exit codes: 0 success; 2 usage; 3 input/GitHub error; "
            "4 invalid provenance; 5 not found; 6 ambiguous."
        ),
    )
    parser.add_argument("--sha", required=True, help="complete assessed head SHA")
    parser.add_argument("--kind", required=True, choices=ASSESSMENT_KINDS)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--run-id",
        type=int,
        help="optional positive workflow run ID used to resolve an ambiguous rerun",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    if SHA_PATTERN.fullmatch(arguments.sha) is None:
        parser.error("--sha must be a complete lowercase 40- or 64-hex SHA")
    if arguments.run_id is not None and arguments.run_id < 1:
        parser.error("--run-id must be a positive integer")
    try:
        return _fetch(arguments)
    except NotFound as error:
        print(f"NOT_FOUND {error}", file=sys.stderr)
        return EXIT_NOT_FOUND
    except Ambiguous as error:
        print(f"AMBIGUOUS {error}", file=sys.stderr)
        return EXIT_AMBIGUOUS
    except InvalidProvenance as error:
        print(f"INVALID {error}", file=sys.stderr)
        return EXIT_INVALID
    except InputError as error:
        print(f"INPUT_ERROR {error}", file=sys.stderr)
        return EXIT_INPUT


if __name__ == "__main__":
    raise SystemExit(main())
