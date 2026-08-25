#!/usr/bin/env python3
"""Build a deterministic, sanitized evidence pack from pre-fetched GitHub data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INPUT = 3
EXIT_REJECTED = 4
EXIT_OUTPUT = 5

SCHEMA_VERSION = "cosmaneura.agent-evidence-pack.v1"
POLICY_VERSION = "cosmaneura.agent-trusted-policy.v1"
MAX_PR_BYTES = 128 * 1024
MAX_CHANGES_BYTES = 2 * 1024 * 1024
MAX_CHECKS_BYTES = 1024 * 1024
MAX_POLICY_BYTES = 64 * 1024
MAX_PATCH_BYTES = 64 * 1024
MAX_TOTAL_PATCH_BYTES = 512 * 1024
MAX_PATCH_HUNKS = 1000
MAX_CHANGED_FILES = 300
MAX_CHECK_RUNS = 1000

SHA_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
EVIDENCE_LOCATOR_PATTERN = re.compile(r"[A-Za-z0-9.][A-Za-z0-9._:/-]{0,255}\Z")

FORBIDDEN_PATH_PARTS = {
    ".git",
    ".ssh",
    "backup",
    "backups",
    "credential",
    "credentials",
    "dump",
    "dumps",
    "secret",
    "secrets",
}
FORBIDDEN_FILENAMES = {
    ".env",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "id_dsa",
    "id_ed25519",
    "id_ecdsa",
    "id_rsa",
}
FORBIDDEN_EXTENSIONS = {
    ".7z",
    ".avi",
    ".bak",
    ".bmp",
    ".crt",
    ".csv",
    ".db",
    ".der",
    ".doc",
    ".docx",
    ".dump",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".jks",
    ".key",
    ".keystore",
    ".log",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".p12",
    ".parquet",
    ".pdf",
    ".pem",
    ".pfx",
    ".png",
    ".sqlite",
    ".sqlite3",
    ".tar",
    ".tsv",
    ".wav",
    ".webm",
    ".webp",
    ".xls",
    ".xlsx",
    ".zip",
}
RAW_LOG_FIELDS = {
    "annotations",
    "log",
    "logs",
    "output",
    "raw_log",
    "raw_logs",
    "stderr",
    "stdout",
}

SECRET_PATTERNS = (
    (
        "private key",
        re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----", re.IGNORECASE),
    ),
    (
        "GitHub token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    ),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    (
        "service secret",
        re.compile(r"\b(?:sk|rk)_(?:live|test|proj)_[A-Za-z0-9_-]{12,}\b"),
    ),
    (
        "bearer token",
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE),
    ),
    (
        "JSON web token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ),
    (
        "credential-bearing URL",
        re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:]+:[^\s/@]+@", re.IGNORECASE),
    ),
    (
        "Azure account key",
        re.compile(r"\bAccountKey=[A-Za-z0-9+/=]{16,}", re.IGNORECASE),
    ),
    (
        "SAS signature",
        re.compile(r"(?:^|[?&])sig=[A-Za-z0-9%+/=]{16,}", re.IGNORECASE),
    ),
    (
        "quoted credential literal",
        re.compile(
            r"\b(?:password|passwd|secret|client_secret|api[_-]?key|"
            r"access[_-]?token|refresh[_-]?token)\b\s*[:=]\s*"
            r"(?:[\"'][^\"'\r\n]{8,}[\"']|[A-Za-z0-9+/=_-]{12,})",
            re.IGNORECASE,
        ),
    ),
)
PHI_PATTERNS = (
    (
        "email address",
        re.compile(
            r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9.-])"
        ),
    ),
    ("US Social Security number", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    (
        "US phone number",
        re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)"),
    ),
    (
        "date of birth literal",
        re.compile(
            r"\b(?:dob|date[ _-]?of[ _-]?birth)\b\s*[:=]\s*[\"']?"
            r"\d{1,4}[-/]\d{1,2}[-/]\d{1,4}",
            re.IGNORECASE,
        ),
    ),
    (
        "medical record number literal",
        re.compile(
            r"\b(?:mrn|medical[ _-]?record[ _-]?number)\b\s*[:=]\s*[\"']?[A-Za-z0-9-]{4,}",
            re.IGNORECASE,
        ),
    ),
    (
        "patient name literal",
        re.compile(
            r"\bpatient[ _-]?name\b\s*[:=]\s*[\"'][A-Za-z][A-Za-z .'-]{2,}[\"']",
            re.IGNORECASE,
        ),
    ),
)

SCANNER_PATTERNS = (
    ("CodeQL/SAST", re.compile(r"codeql|\bsast\b|semgrep|static.?analysis", re.I)),
    (
        "dependency",
        re.compile(
            r"dependabot|dependency.?review|pip.?audit|npm.?audit|\bosv\b|snyk", re.I
        ),
    ),
    ("secret", re.compile(r"secret.?scann|gitleaks|trufflehog", re.I)),
    ("IaC", re.compile(r"\biac\b|checkov|tfsec|terrascan|trivy.?config", re.I)),
    ("SBOM", re.compile(r"\bsbom\b|\bsyft\b", re.I)),
    (
        "container",
        re.compile(r"container.?scan|image.?scan|\btrivy\b|\bgrype\b", re.I),
    ),
)

TIER_2_EXACT_PATHS = {
    "app/configuration/authority.py",
    "app/configuration/clerk.py",
    "app/configuration/service_auth.py",
}
DESTRUCTIVE_MIGRATION_PATTERN = re.compile(
    r"(?:\bDROP\s+(?:COLUMN|CONSTRAINT|INDEX|TABLE|TYPE)\b|"
    r"\bRENAME\s+(?:COLUMN|TABLE)\b|"
    r"\bALTER\s+(?:COLUMN\s+)?[^\n;]*\bTYPE\b|"
    r"\bop\.(?:drop_|rename_|alter_column\s*\())",
    re.IGNORECASE,
)
HUNK_HEADER_PATTERN = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$"
)
NO_NEWLINE_MARKER = r"\ No newline at end of file"


class InputError(Exception):
    """A source could not be read as bounded strict JSON."""


class RejectedEvidence(Exception):
    """A source violated the evidence-pack safety contract."""


class OutputError(Exception):
    """The sanitized pack could not be written."""


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant {value!r}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate object key")
        value[key] = item
    return value


def _read_bounded_json(path: Path, limit: int, label: str) -> Any:
    try:
        if path.is_symlink():
            raise RejectedEvidence(f"{label}: symbolic-link input is forbidden")
        metadata = path.stat()
    except OSError as error:
        raise InputError(f"{label}: cannot stat {path}: {error}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise InputError(f"{label}: input is not a regular file: {path}")
    if metadata.st_size > limit:
        raise RejectedEvidence(
            f"{label}: input exceeds {limit} bytes ({metadata.st_size} bytes supplied)"
        )
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise InputError(f"{label}: cannot read {path}: {error}") from error
    if len(raw) > limit:
        raise RejectedEvidence(f"{label}: input exceeds {limit} bytes")
    if b"\x00" in raw:
        raise RejectedEvidence(f"{label}: binary/NUL input is forbidden")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RejectedEvidence(f"{label}: input must be valid UTF-8 text") from error
    try:
        return json.loads(
            text,
            parse_constant=_reject_non_json_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise InputError(f"{label}: invalid strict JSON: {error}") from error


def _require_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RejectedEvidence(f"{path}: expected object")
    return value


def _require_exact_keys(
    value: dict[str, Any], required: set[str], optional: set[str], path: str
) -> None:
    missing = sorted(required - value.keys())
    unknown = sorted(value.keys() - required - optional)
    if missing:
        raise RejectedEvidence(
            f"{path}: missing required field(s): {', '.join(missing)}"
        )
    if unknown:
        raw_logs = sorted(set(unknown) & RAW_LOG_FIELDS)
        if raw_logs:
            raise RejectedEvidence(
                f"{path}: raw log/output field(s) forbidden: {', '.join(raw_logs)}"
            )
        raise RejectedEvidence(
            f"{path}: {len(unknown)} unexpected field(s) are not allowed"
        )


def _require_string(value: Any, path: str, *, maximum: int = 1000) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise RejectedEvidence(
            f"{path}: expected non-empty string no longer than {maximum} characters"
        )
    _check_text(value, path)
    return value


def _require_integer(value: Any, path: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise RejectedEvidence(f"{path}: expected integer >= {minimum}")
    return value


def _require_sha(value: Any, path: str) -> str:
    if not isinstance(value, str) or SHA_PATTERN.fullmatch(value) is None:
        raise RejectedEvidence(f"{path}: expected complete lowercase 40- or 64-hex SHA")
    return value


def _require_timestamp_or_null(value: Any, path: str) -> str | None:
    if value is None:
        return None
    text = _require_string(value, path, maximum=80)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise RejectedEvidence(f"{path}: expected RFC 3339 date-time") from error
    if parsed.tzinfo is None:
        raise RejectedEvidence(f"{path}: timestamp must include timezone")
    return text


def _check_text(value: str, path: str) -> None:
    if any(ord(character) < 32 and character not in "\n\r\t" for character in value):
        raise RejectedEvidence(f"{path}: binary/control characters are forbidden")
    for label, pattern in SECRET_PATTERNS:
        if pattern.search(value):
            raise RejectedEvidence(f"{path}: rejected possible {label}")
    for label, pattern in PHI_PATTERNS:
        if pattern.search(value):
            raise RejectedEvidence(f"{path}: rejected possible PHI ({label})")


def _repository_path(value: Any, path: str) -> str:
    text = _require_string(value, path, maximum=1000)
    if "\\" in text or text.startswith("/"):
        raise RejectedEvidence(f"{path}: path must be repository-relative POSIX text")
    pure = PurePosixPath(text)
    if not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise RejectedEvidence(
            f"{path}: path traversal or empty segments are forbidden"
        )
    lowered_parts = tuple(part.casefold() for part in pure.parts)
    filename = lowered_parts[-1]
    if (
        any(part in FORBIDDEN_PATH_PARTS for part in lowered_parts)
        or filename in FORBIDDEN_FILENAMES
        or filename.startswith(".env.")
        or filename.startswith("secrets.")
        or filename.startswith("credentials.")
        or PurePosixPath(filename).suffix in FORBIDDEN_EXTENSIONS
    ):
        raise RejectedEvidence(f"{path}: forbidden sensitive or binary path")
    return text


def _git_ref(value: Any, path: str) -> str:
    text = _require_string(value, path, maximum=255)
    if (
        text.startswith(("/", "."))
        or text.endswith(("/", ".", ".lock"))
        or ".." in text
        or "//" in text
        or "@{" in text
        or "\\" in text
        or any(character in text for character in " ~^:?*[")
    ):
        raise RejectedEvidence(f"{path}: invalid Git ref name")
    return text


def _validate_binding(
    envelope: dict[str, Any], repository: str, base_sha: str, head_sha: str, path: str
) -> None:
    if envelope["repository"] != repository:
        raise RejectedEvidence(f"{path}.repository: does not match PR repository")
    if envelope["base_sha"] != base_sha:
        raise RejectedEvidence(f"{path}.base_sha: does not match PR base SHA")
    if envelope["head_sha"] != head_sha:
        raise RejectedEvidence(f"{path}.head_sha: does not match PR head SHA")


def _structure_patch(
    patch: str,
    additions: int,
    deletions: int,
    path: str,
) -> tuple[dict[str, Any], bool]:
    """Parse a GitHub unified patch and retain no source-line content."""

    lines = patch.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines:
        raise RejectedEvidence(f"{path}: empty textual diff is forbidden")

    hunks: list[dict[str, int]] = []
    total_additions = 0
    total_deletions = 0
    destructive_migration_signal = False
    index = 0
    while index < len(lines):
        header = HUNK_HEADER_PATTERN.fullmatch(lines[index])
        if header is None:
            raise RejectedEvidence(
                f"{path}: unsupported or malformed unified-diff structure"
            )
        if len(hunks) >= MAX_PATCH_HUNKS:
            raise RejectedEvidence(f"{path}: exceeds {MAX_PATCH_HUNKS} diff hunks")

        old_start = int(header.group(1))
        old_lines = int(header.group(2)) if header.group(2) is not None else 1
        new_start = int(header.group(3))
        new_lines = int(header.group(4)) if header.group(4) is not None else 1
        index += 1
        added_lines = 0
        deleted_lines = 0
        context_lines = 0
        while index < len(lines) and not lines[index].startswith("@@"):
            line = lines[index]
            if line == NO_NEWLINE_MARKER:
                index += 1
                continue
            if not line or line[0] not in {" ", "+", "-"}:
                raise RejectedEvidence(
                    f"{path}: unsupported or malformed unified-diff line"
                )
            if line[0] == "+":
                added_lines += 1
                if DESTRUCTIVE_MIGRATION_PATTERN.search(line[1:]):
                    destructive_migration_signal = True
            elif line[0] == "-":
                deleted_lines += 1
            else:
                context_lines += 1
            index += 1

        if old_lines != deleted_lines + context_lines:
            raise RejectedEvidence(
                f"{path}: old-line count does not match the diff hunk header"
            )
        if new_lines != added_lines + context_lines:
            raise RejectedEvidence(
                f"{path}: new-line count does not match the diff hunk header"
            )
        total_additions += added_lines
        total_deletions += deleted_lines
        hunks.append(
            {
                "old_start": old_start,
                "old_lines": old_lines,
                "new_start": new_start,
                "new_lines": new_lines,
                "added_lines": added_lines,
                "deleted_lines": deleted_lines,
                "context_lines": context_lines,
            }
        )

    if total_additions != additions or total_deletions != deletions:
        raise RejectedEvidence(
            f"{path}: diff line counts do not match changed-file metadata; "
            "truncated input is forbidden"
        )

    return (
        {
            "format": "unified-diff-structure.v1",
            "content_redacted": True,
            "content_digest": (
                f"sha256:{hashlib.sha256(patch.encode('utf-8')).hexdigest()}"
            ),
            "hunks": hunks,
        },
        destructive_migration_signal,
    )


def _normalize_pr(document: Any, default_branch: str) -> dict[str, Any]:
    value = _require_object(document, "$.pr")
    required = {
        "repository",
        "number",
        "base_sha",
        "head_sha",
        "base_ref",
        "state",
        "draft",
    }
    _require_exact_keys(value, required, set(), "$.pr")
    repository = _require_string(value["repository"], "$.pr.repository", maximum=201)
    if REPOSITORY_PATTERN.fullmatch(repository) is None:
        raise RejectedEvidence("$.pr.repository: expected OWNER/REPOSITORY")
    base_sha = _require_sha(value["base_sha"], "$.pr.base_sha")
    head_sha = _require_sha(value["head_sha"], "$.pr.head_sha")
    if base_sha == head_sha:
        raise RejectedEvidence("$.pr.head_sha: must differ from base_sha for a PR")
    base_ref = _require_string(value["base_ref"], "$.pr.base_ref", maximum=255)
    if base_ref != default_branch:
        raise RejectedEvidence(
            f"$.pr.base_ref: expected configured default branch {default_branch!r}"
        )
    state = value["state"]
    if state not in {"open", "closed"}:
        raise RejectedEvidence("$.pr.state: expected 'open' or 'closed'")
    if not isinstance(value["draft"], bool):
        raise RejectedEvidence("$.pr.draft: expected boolean")
    return {
        "repository": repository,
        "number": _require_integer(value["number"], "$.pr.number", minimum=1),
        "base_sha": base_sha,
        "head_sha": head_sha,
        "base_ref": base_ref,
        "state": state,
        "draft": value["draft"],
    }


def _normalize_changes(
    document: Any, repository: str, base_sha: str, head_sha: str
) -> dict[str, Any]:
    value = _require_object(document, "$.changes")
    _require_exact_keys(
        value,
        {"repository", "base_sha", "head_sha", "complete", "files"},
        set(),
        "$.changes",
    )
    for key in ("repository", "base_sha", "head_sha"):
        _require_string(value[key], f"$.changes.{key}", maximum=201)
    _validate_binding(value, repository, base_sha, head_sha, "$.changes")
    if value["complete"] is not True:
        raise RejectedEvidence(
            "$.changes.complete: all changed-file pages are required for a safe risk floor"
        )
    files = value["files"]
    if not isinstance(files, list):
        raise RejectedEvidence("$.changes.files: expected array")
    if len(files) > MAX_CHANGED_FILES:
        raise RejectedEvidence(
            f"$.changes.files: exceeds {MAX_CHANGED_FILES} changed files"
        )

    normalized: list[dict[str, Any]] = []
    total_patch_bytes = 0
    paths: set[str] = set()
    for index, raw_file in enumerate(files):
        item_path = f"$.changes.files[{index}]"
        item = _require_object(raw_file, item_path)
        _require_exact_keys(
            item,
            {"path", "status", "additions", "deletions", "changes", "patch"},
            {"previous_path"},
            item_path,
        )
        changed_path = _repository_path(item["path"], f"{item_path}.path")
        if changed_path in paths:
            raise RejectedEvidence(f"{item_path}.path: duplicate changed path")
        paths.add(changed_path)
        status = item["status"]
        if status not in {
            "added",
            "modified",
            "removed",
            "renamed",
            "copied",
            "changed",
        }:
            raise RejectedEvidence(
                f"{item_path}.status: unsupported GitHub file status"
            )
        previous_path = item.get("previous_path")
        if status == "renamed" and previous_path is None:
            raise RejectedEvidence(
                f"{item_path}.previous_path: required for renamed file"
            )
        if previous_path is not None:
            previous_path = _repository_path(
                previous_path, f"{item_path}.previous_path"
            )
        additions = _require_integer(item["additions"], f"{item_path}.additions")
        deletions = _require_integer(item["deletions"], f"{item_path}.deletions")
        changes = _require_integer(item["changes"], f"{item_path}.changes")
        if changes != additions + deletions:
            raise RejectedEvidence(
                f"{item_path}.changes: must equal additions plus deletions"
            )
        patch = _require_string(item["patch"], f"{item_path}.patch", maximum=100_000)
        patch = patch.replace("\r\n", "\n").replace("\r", "\n")
        patch_bytes = len(patch.encode("utf-8"))
        if patch_bytes > MAX_PATCH_BYTES:
            raise RejectedEvidence(
                f"{item_path}.patch: exceeds {MAX_PATCH_BYTES} UTF-8 bytes"
            )
        total_patch_bytes += patch_bytes
        if total_patch_bytes > MAX_TOTAL_PATCH_BYTES:
            raise RejectedEvidence(
                f"$.changes.files: patches exceed {MAX_TOTAL_PATCH_BYTES} UTF-8 bytes"
            )
        diff_structure, destructive_migration_signal = _structure_patch(
            patch,
            additions,
            deletions,
            f"{item_path}.patch",
        )
        normalized_item: dict[str, Any] = {
            "path": changed_path,
            "status": status,
            "additions": additions,
            "deletions": deletions,
            "changes": changes,
            "diff": diff_structure,
            "destructive_migration_signal": destructive_migration_signal,
        }
        if previous_path is not None:
            normalized_item["previous_path"] = previous_path
        normalized.append(normalized_item)
    normalized.sort(key=lambda item: (item["path"], item.get("previous_path", "")))
    return {
        "repository": repository,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "complete": True,
        "files": normalized,
    }


def _normalize_checks(
    document: Any, repository: str, base_sha: str, head_sha: str
) -> dict[str, Any]:
    value = _require_object(document, "$.checks")
    _require_exact_keys(
        value,
        {"repository", "base_sha", "head_sha", "complete", "check_runs"},
        set(),
        "$.checks",
    )
    for key in ("repository", "base_sha", "head_sha"):
        _require_string(value[key], f"$.checks.{key}", maximum=201)
    _validate_binding(value, repository, base_sha, head_sha, "$.checks")
    if not isinstance(value["complete"], bool):
        raise RejectedEvidence("$.checks.complete: expected boolean")
    check_runs = value["check_runs"]
    if not isinstance(check_runs, list):
        raise RejectedEvidence("$.checks.check_runs: expected array")
    if len(check_runs) > MAX_CHECK_RUNS:
        raise RejectedEvidence(
            f"$.checks.check_runs: exceeds {MAX_CHECK_RUNS} check runs"
        )

    normalized: list[dict[str, Any]] = []
    identifiers: set[int] = set()
    for index, raw_check in enumerate(check_runs):
        item_path = f"$.checks.check_runs[{index}]"
        item = _require_object(raw_check, item_path)
        _require_exact_keys(
            item,
            {
                "id",
                "name",
                "head_sha",
                "status",
                "conclusion",
                "started_at",
                "completed_at",
            },
            set(),
            item_path,
        )
        identifier = _require_integer(item["id"], f"{item_path}.id", minimum=1)
        if identifier in identifiers:
            raise RejectedEvidence(f"{item_path}.id: duplicate check-run ID")
        identifiers.add(identifier)
        name = _require_string(item["name"], f"{item_path}.name", maximum=240)
        check_sha = _require_sha(item["head_sha"], f"{item_path}.head_sha")
        if check_sha != head_sha:
            raise RejectedEvidence(f"{item_path}.head_sha: does not match PR head SHA")
        status = item["status"]
        if status not in {
            "queued",
            "in_progress",
            "completed",
            "pending",
            "requested",
            "waiting",
        }:
            raise RejectedEvidence(f"{item_path}.status: unsupported check-run status")
        conclusion = item["conclusion"]
        allowed_conclusions = {
            None,
            "success",
            "failure",
            "neutral",
            "cancelled",
            "skipped",
            "timed_out",
            "action_required",
            "startup_failure",
            "stale",
        }
        if conclusion not in allowed_conclusions:
            raise RejectedEvidence(f"{item_path}.conclusion: unsupported conclusion")
        if status == "completed" and conclusion is None:
            raise RejectedEvidence(
                f"{item_path}.conclusion: completed check requires a conclusion"
            )
        if status != "completed" and conclusion is not None:
            raise RejectedEvidence(
                f"{item_path}.conclusion: non-completed check must use null"
            )
        started_at = _require_timestamp_or_null(
            item["started_at"], f"{item_path}.started_at"
        )
        completed_at = _require_timestamp_or_null(
            item["completed_at"], f"{item_path}.completed_at"
        )
        if status == "completed" and started_at is None:
            raise RejectedEvidence(
                f"{item_path}.started_at: completed check requires timestamp"
            )
        if status == "completed" and completed_at is None:
            raise RejectedEvidence(
                f"{item_path}.completed_at: completed check requires timestamp"
            )
        if status != "completed" and completed_at is not None:
            raise RejectedEvidence(
                f"{item_path}.completed_at: non-completed check must use null"
            )
        if (
            started_at is not None
            and completed_at is not None
            and datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
            < datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        ):
            raise RejectedEvidence(
                f"{item_path}.completed_at: must not precede started_at"
            )
        normalized.append(
            {
                "id": identifier,
                "name": name,
                "head_sha": check_sha,
                "status": status,
                "conclusion": conclusion,
                "started_at": started_at,
                "completed_at": completed_at,
            }
        )
    normalized.sort(key=lambda item: (item["name"].casefold(), item["id"]))
    return {
        "repository": repository,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "complete": value["complete"],
        "check_runs": normalized,
    }


def _normalize_policy(
    document: Any, repository: str, default_branch: str
) -> dict[str, Any]:
    value = _require_object(document, "$.policy")
    _require_exact_keys(
        value,
        {
            "policy_version",
            "repository",
            "policy_sha",
            "default_branch",
            "deploy_workflow_path",
            "environment_branches",
            "migration_playbook_path",
            "branch_protection_enforced",
            "production_reviewer_enforced",
        },
        set(),
        "$.policy",
    )
    if value["policy_version"] != POLICY_VERSION:
        raise RejectedEvidence(
            f"$.policy.policy_version: expected constant {POLICY_VERSION!r}"
        )
    policy_repository = _require_string(
        value["repository"], "$.policy.repository", maximum=201
    )
    if REPOSITORY_PATTERN.fullmatch(policy_repository) is None:
        raise RejectedEvidence("$.policy.repository: expected OWNER/REPOSITORY")
    if policy_repository != repository:
        raise RejectedEvidence("$.policy.repository: does not match PR repository")
    policy_sha = _require_sha(value["policy_sha"], "$.policy.policy_sha")
    policy_default_branch = _git_ref(value["default_branch"], "$.policy.default_branch")
    if policy_default_branch != default_branch:
        raise RejectedEvidence(
            "$.policy.default_branch: does not match configured default branch"
        )
    deploy_workflow_path = _repository_path(
        value["deploy_workflow_path"], "$.policy.deploy_workflow_path"
    )
    if not deploy_workflow_path.startswith(".github/workflows/") or not (
        deploy_workflow_path.endswith(".yml") or deploy_workflow_path.endswith(".yaml")
    ):
        raise RejectedEvidence(
            "$.policy.deploy_workflow_path: expected a .github/workflows YAML path"
        )
    migration_playbook_path = _repository_path(
        value["migration_playbook_path"], "$.policy.migration_playbook_path"
    )
    if not migration_playbook_path.startswith("docs/") or not (
        migration_playbook_path.casefold().endswith(".md")
    ):
        raise RejectedEvidence(
            "$.policy.migration_playbook_path: expected a docs Markdown path"
        )
    environment_branches = _require_object(
        value["environment_branches"], "$.policy.environment_branches"
    )
    _require_exact_keys(
        environment_branches,
        {"production", "staging"},
        set(),
        "$.policy.environment_branches",
    )
    normalized_branches = {
        environment: _git_ref(
            environment_branches[environment],
            f"$.policy.environment_branches.{environment}",
        )
        for environment in ("production", "staging")
    }
    for field in ("branch_protection_enforced", "production_reviewer_enforced"):
        if not isinstance(value[field], bool):
            raise RejectedEvidence(f"$.policy.{field}: expected boolean")
    return {
        "policy_version": POLICY_VERSION,
        "repository": policy_repository,
        "policy_sha": policy_sha,
        "default_branch": policy_default_branch,
        "deploy_workflow_path": deploy_workflow_path,
        "environment_branches": normalized_branches,
        "migration_playbook_path": migration_playbook_path,
        "branch_protection_enforced": value["branch_protection_enforced"],
        "production_reviewer_enforced": value["production_reviewer_enforced"],
    }


def _tier_for_file(path: str, destructive_migration_signal: bool) -> tuple[str, str]:
    lowered = path.casefold()
    pure = PurePosixPath(lowered)
    parts = pure.parts
    filename = pure.name
    if lowered in TIER_2_EXACT_PATHS or lowered.startswith("app/security/"):
        return "T2", "AGENTS.md security/identity adapter path"
    if any("stripe" in part or "billing" in part for part in parts):
        return "T2", "AGENTS.md Stripe/billing path"
    if lowered.startswith(("app/models/", "app/schemas/", "migrations/")) and any(
        marker in lowered
        for marker in (
            "patient",
            "appointment",
            "transcript",
            "visit_note",
            "doctor_note",
        )
    ):
        return "T2", "AGENTS.md patient-data schema path"
    if lowered.startswith("app/") and any(
        re.search(r"(?:^|[_-])(?:session|token)(?:[_-]|\.|$)", part) for part in parts
    ):
        return "T2", "AGENTS.md session/token logic path"
    if lowered.startswith("migrations/") and destructive_migration_signal:
        return "T2", "AGENTS.md destructive migration"
    if lowered.startswith("migrations/"):
        return "T1", "AGENTS.md additive migration floor"
    if lowered.startswith(("app/", "web/", "infra/", "infra-staging/", ".github/")):
        return "T1", "conservative code/CI/infrastructure path floor"
    if filename in {"pyproject.toml", "uv.lock", "package.json", "pnpm-lock.yaml"}:
        return "T1", "dependency/build path floor"
    if lowered == "agents.md":
        return "T1", "deployment policy path floor"
    if lowered.startswith("docs/") or pure.suffix in {
        ".md",
        ".rst",
        ".txt",
        ".css",
        ".scss",
        ".less",
    }:
        return "T0", "AGENTS.md documentation/copy/style path"
    return "T1", "conservative unclassified changed-path floor"


def _risk_floor(files: list[dict[str, Any]]) -> tuple[str, list[dict[str, str]]]:
    highest = "T0"
    levels = {"T0": 0, "T1": 1, "T2": 2}
    reasons: list[dict[str, str]] = []
    for item in files:
        tier, rule = _tier_for_file(item["path"], item["destructive_migration_signal"])
        reasons.append({"tier": tier, "path": item["path"], "rule": rule})
        if levels[tier] > levels[highest]:
            highest = tier
    return highest, reasons


def _scanner_coverage(
    checks: list[dict[str, Any]], collection_complete: bool
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for scanner, pattern in SCANNER_PATTERNS:
        matching = [item for item in checks if pattern.search(item["name"])]
        locators = [f"check-run:{item['id']}" for item in matching]
        # A check name is attacker-influenced discovery metadata, not scanner
        # attestation. Until the broker supplies and verifies an allowlisted app
        # identity/workflow provenance contract, matching checks remain unusable.
        if matching or not collection_complete:
            status = "unavailable"
        else:
            status = "not_configured"
        if not locators:
            locators = [
                "check-run-collection:no-match"
                if collection_complete
                else "check-run-collection:incomplete"
            ]
        result.append(
            {
                "scanner": scanner,
                "status": status,
                "evidence_locators": locators,
            }
        )
    return result


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _build_evidence_policy(
    pr: dict[str, Any],
    checks: dict[str, Any],
    scanner_coverage: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    manifest: dict[str, dict[str, Any]] = {}

    def register(locator: str, availability: str, required: bool) -> None:
        existing = manifest.get(locator)
        candidate = {
            "locator": locator,
            "availability": availability,
            "required_for_deployment": required,
        }
        if existing is not None and existing != candidate:
            raise RejectedEvidence(
                "$.evidence: conflicting evidence-policy facts for one locator"
            )
        manifest[locator] = candidate

    for item in evidence:
        identifier = item["id"]
        item_type = item["type"]
        availability = "available"
        if item_type == "check_run_collection" and not item["complete"]:
            availability = "unavailable"
        elif item_type in {"semantic_source_coverage", "migration_rehearsal"}:
            availability = "unavailable"
        elif item_type == "scanner_coverage" and item["status"] != "available":
            availability = "unavailable"
        elif item_type == "repository_policy_coverage":
            availability = "unavailable"
        required = item_type in {
            "pull_request_metadata",
            "repository_policy",
            "repository_policy_coverage",
            "changed_file_collection",
            "code_diff",
            "check_run_collection",
        }
        register(identifier, availability, required)
        locator_aliases: list[Any] = []
        if item_type == "code_diff":
            locator_aliases.extend((item.get("path"), item.get("previous_path")))
        elif item_type == "repository_policy":
            locator_aliases.extend(
                (item.get("deploy_workflow_path"), item.get("migration_playbook_path"))
            )
        for alias in locator_aliases:
            if (
                isinstance(alias, str)
                and EVIDENCE_LOCATOR_PATTERN.fullmatch(alias) is not None
            ):
                register(alias, availability, required)

    for scanner in scanner_coverage:
        for locator in scanner["evidence_locators"]:
            if (
                locator not in manifest
                and EVIDENCE_LOCATOR_PATTERN.fullmatch(locator) is not None
            ):
                register(locator, "unavailable", False)

    scanner_policy = []
    for scanner in scanner_coverage:
        scanner_id = re.sub(r"[^a-z0-9]+", "-", scanner["scanner"].casefold()).strip(
            "-"
        )
        scanner_policy.append(
            {
                "locator": f"scanner-coverage:{scanner_id}",
                "scanner": scanner["scanner"],
                "status": scanner["status"],
            }
        )

    return {
        "allowed_action_evidence_locators": sorted(manifest),
        "evidence_manifest": [manifest[locator] for locator in sorted(manifest)],
        "pull_request": {
            "locator": f"pull-request:{pr['number']}",
            "state": pr["state"],
            "draft": pr["draft"],
        },
        "checks": [
            {
                "locator": f"check-run:{item['id']}",
                "check_run_id": item["id"],
                "name": item["name"],
                # Names and conclusions are not enough to prove which trusted
                # workflow/app produced a check. Keep every observed check
                # informational until an attested provenance allowlist exists.
                "required": False,
                "status": item["status"],
                "conclusion": item["conclusion"],
            }
            for item in checks["check_runs"]
        ],
        "scanner_coverage": scanner_policy,
        "semantic_source": {
            "locator": "semantic-source:unavailable",
            "status": "unavailable",
        },
        "migration_rehearsal": {
            "locator": "migration-rehearsal:unavailable",
            "status": "unavailable",
        },
    }


def _build_pack(
    pr: dict[str, Any],
    changes: dict[str, Any],
    checks: dict[str, Any],
    policy: dict[str, Any] | None,
) -> dict[str, Any]:
    source = {"pr": pr, "changes": changes, "checks": checks}
    if policy is not None:
        source["policy"] = policy
    input_digest = f"sha256:{hashlib.sha256(_canonical_bytes(source)).hexdigest()}"
    floor, risk_reasons = _risk_floor(changes["files"])

    evidence: list[dict[str, Any]] = [
        {
            "id": f"pull-request:{pr['number']}",
            "type": "pull_request_metadata",
            "trust": "untrusted",
            "repository": pr["repository"],
            "pull_request": pr["number"],
            "base_ref": pr["base_ref"],
            "base_sha": pr["base_sha"],
            "head_sha": pr["head_sha"],
            "state": pr["state"],
            "draft": pr["draft"],
        },
    ]
    if policy is not None:
        evidence.append(
            {
                "id": f"repository-policy:{policy['policy_sha']}",
                "type": "repository_policy",
                "trust": "trusted",
                **policy,
            }
        )
    else:
        evidence.append(
            {
                "id": "repository-policy:unavailable",
                "type": "repository_policy_coverage",
                "trust": "trusted",
                "repository": pr["repository"],
                "head_sha": pr["head_sha"],
                "status": "unavailable",
            }
        )
    evidence.append(
        {
            "id": "changed-file-collection",
            "type": "changed_file_collection",
            "trust": "untrusted",
            "repository": pr["repository"],
            "base_sha": pr["base_sha"],
            "head_sha": pr["head_sha"],
            "complete": changes["complete"],
            "file_count": len(changes["files"]),
        }
    )
    for item in changes["files"]:
        evidence.append(
            {
                "id": f"changed-file:{hashlib.sha256(item['path'].encode()).hexdigest()[:16]}",
                "type": "code_diff",
                "trust": "untrusted",
                "repository": pr["repository"],
                "base_sha": pr["base_sha"],
                "head_sha": pr["head_sha"],
                **item,
            }
        )
    evidence.append(
        {
            "id": "check-run-collection",
            "type": "check_run_collection",
            "trust": "untrusted",
            "repository": pr["repository"],
            "head_sha": pr["head_sha"],
            "complete": checks["complete"],
            "check_run_count": len(checks["check_runs"]),
        }
    )
    for item in checks["check_runs"]:
        evidence.append(
            {
                "id": f"check-run:{item['id']}",
                "type": "check_run",
                "trust": "untrusted",
                "repository": pr["repository"],
                "check_run_id": item["id"],
                "name": item["name"],
                "head_sha": item["head_sha"],
                "status": item["status"],
                "conclusion": item["conclusion"],
                "started_at": item["started_at"],
                "completed_at": item["completed_at"],
            }
        )

    scanner_coverage = _scanner_coverage(checks["check_runs"], checks["complete"])
    for item in scanner_coverage:
        scanner_id = re.sub(r"[^a-z0-9]+", "-", item["scanner"].casefold()).strip("-")
        evidence.append(
            {
                "id": f"scanner-coverage:{scanner_id}",
                "type": "scanner_coverage",
                "trust": "untrusted",
                "repository": pr["repository"],
                "head_sha": pr["head_sha"],
                **item,
            }
        )
    evidence.extend(
        [
            {
                "id": "semantic-source:unavailable",
                "type": "semantic_source_coverage",
                "trust": "trusted",
                "repository": pr["repository"],
                "head_sha": pr["head_sha"],
                "status": "unavailable",
            },
            {
                "id": "migration-rehearsal:unavailable",
                "type": "migration_rehearsal",
                "trust": "trusted",
                "repository": pr["repository"],
                "head_sha": pr["head_sha"],
                "status": "unavailable",
            },
        ]
    )
    evidence_policy = _build_evidence_policy(pr, checks, scanner_coverage, evidence)

    return {
        "schema_version": SCHEMA_VERSION,
        "subject": {
            "repository": pr["repository"],
            "pull_request": pr["number"],
            "base_ref": pr["base_ref"],
            "base_sha": pr["base_sha"],
            "head_sha": pr["head_sha"],
        },
        "risk_tier_floor": floor,
        "risk_reasons": risk_reasons,
        "scanner_coverage": scanner_coverage,
        "evidence_policy": evidence_policy,
        "evidence": evidence,
        "sanitization": {
            "omitted_fields": [
                "pull request title, body, author, comments, labels, and URLs",
                "check output, annotations, logs, app identity, and URLs",
                "changed-file blob, raw, and contents URLs",
                "all diff line content, comments, literals, and hunk labels",
            ],
            "rejected_classes": [
                "secret-like literals",
                "likely PHI literals",
                "raw logs",
                "sensitive paths",
                "binary content",
                "oversize content",
                "mismatched repository or SHA evidence",
                "unsupported, malformed, or truncated unified diffs",
            ],
            "diff_representation": (
                "numeric unified-diff hunk structure plus a SHA-256 content digest; "
                "no source-line text"
            ),
            "scanner_provenance": (
                "check names are untrusted discovery hints and never establish "
                "scanner availability"
            ),
        },
        "integrity": {"input_digest": input_digest},
    }


def _atomic_write(path: Path, document: dict[str, Any]) -> None:
    if not path.parent.is_dir():
        raise OutputError(f"output directory does not exist: {path.parent}")
    serialized = (
        json.dumps(
            document, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True
        )
        + "\n"
    )
    descriptor: int | None = None
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True
        )
        temporary_path = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            descriptor = None
            output.write(serialized)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    except OSError as error:
        raise OutputError(f"cannot atomically write {path}: {error}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a deterministic agent evidence pack from strict, pre-fetched "
            "GitHub metadata. No repository code is executed."
        ),
        epilog=(
            "Exit codes: 0 success; 2 usage; 3 unreadable/invalid JSON input; "
            "4 rejected unsafe or inconsistent evidence; 5 output failure."
        ),
    )
    parser.add_argument(
        "--pr", required=True, type=Path, help="normalized PR metadata JSON"
    )
    parser.add_argument(
        "--changes", required=True, type=Path, help="bound changed-file and patch JSON"
    )
    parser.add_argument(
        "--checks", required=True, type=Path, help="bound check-run metadata JSON"
    )
    parser.add_argument(
        "--policy",
        type=Path,
        help="optional strict trusted default-branch policy JSON",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--default-branch", default="master")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        default_branch = _require_string(
            arguments.default_branch, "--default-branch", maximum=255
        )
        pr = _normalize_pr(
            _read_bounded_json(arguments.pr, MAX_PR_BYTES, "PR metadata"),
            default_branch,
        )
        changes = _normalize_changes(
            _read_bounded_json(arguments.changes, MAX_CHANGES_BYTES, "changed files"),
            pr["repository"],
            pr["base_sha"],
            pr["head_sha"],
        )
        checks = _normalize_checks(
            _read_bounded_json(arguments.checks, MAX_CHECKS_BYTES, "check runs"),
            pr["repository"],
            pr["base_sha"],
            pr["head_sha"],
        )
        policy = None
        if arguments.policy is not None:
            policy = _normalize_policy(
                _read_bounded_json(
                    arguments.policy, MAX_POLICY_BYTES, "trusted policy"
                ),
                pr["repository"],
                default_branch,
            )
        pack = _build_pack(pr, changes, checks, policy)
        _atomic_write(arguments.output, pack)
    except InputError as error:
        print(f"INPUT_ERROR {error}", file=sys.stderr)
        return EXIT_INPUT
    except RejectedEvidence as error:
        print(f"REJECTED {error}", file=sys.stderr)
        return EXIT_REJECTED
    except OutputError as error:
        print(f"OUTPUT_ERROR {error}", file=sys.stderr)
        return EXIT_OUTPUT
    print(
        f"BUILT {arguments.output} {pr['repository']}@{pr['head_sha']} "
        f"{pack['integrity']['input_digest']} risk_floor={pack['risk_tier_floor']}"
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
