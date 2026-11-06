#!/usr/bin/env python3
"""Fail closed when staging-scoped paths name live production identity.

Normal pull requests must keep staging config, staging deploy workflows, and
staging-named fixtures pointed only at staging. Live Cosmas hosts and the live
Clerk sign-in instance belong on the founder-gated production promote path
(``.env.production.*``, ``Build & Publish (manual)``, production infra apply),
which is outside this scan set.

This module intentionally contains the forbidden literals so callers and tests
can import them without re-embedding them in staging-scoped fixture files.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

LIVE_APP_HOST = "app.cosmaneura.com"
LIVE_CHAT_HOST = "chat.cosmaneura.com"
LIVE_CLERK_HOST = "clerk.cosmaneura.com"
LIVE_CLERK_ACCOUNTS_HOST = "accounts.cosmaneura.com"
LIVE_PUBLISHABLE_KEY_PREFIX = "pk_live_"
LIVE_SECRET_KEY_PREFIX = "sk_live_"
LIVE_CLERK_VAULT_ITEM = "clerk-production-api"

# Opaque report ids only — never echo live key prefixes or secret-shaped
# literals into logs or CI annotations (CodeQL clear-text logging).
# Negative lookbehind keeps staging-app / staging-chat from matching.
_FORBIDDEN: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "live_app_host",
        re.compile(r"(?<![A-Za-z0-9-])app\.cosmaneura\.com"),
    ),
    (
        "live_chat_host",
        re.compile(r"(?<![A-Za-z0-9-])chat\.cosmaneura\.com"),
    ),
    ("live_clerk_host", re.compile(r"clerk\.cosmaneura\.com")),
    ("live_clerk_accounts_host", re.compile(r"accounts\.cosmaneura\.com")),
    ("live_publishable_key_prefix", re.compile(r"\bpk_live_")),
    ("live_secret_key_prefix", re.compile(r"\bsk_live_")),
    ("live_clerk_vault_item", re.compile(r"clerk-production-api")),
)

_STAGING_WORKFLOWS = frozenset(
    {
        "deploy_staging_candidate.yml",
        "bootstrap_staging_recovery_image.yml",
        "staging_restore_rehearsal.yml",
    }
)

_SELF_RELATIVE = frozenset(
    {
        "scripts/staging_environment_boundary.py",
        "tests/infrastructure/test_staging_environment_boundary.py",
    }
)

_TEXT_SUFFIXES = frozenset(
    {
        ".py",
        ".sh",
        ".yml",
        ".yaml",
        ".json",
        ".toml",
        ".md",
        ".tf",
        ".hcl",
        ".env",
        ".txt",
        ".mjs",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".css",
        ".html",
        ".ini",
        ".cfg",
        ".example",
        ".backend",
        ".frontend",
    }
)

_MAX_FILE_BYTES = 2 * 1024 * 1024


class BoundaryViolation(RuntimeError):
    """One staging-scoped path named a live production identity."""


def repository_root(start: Path | None = None) -> Path:
    here = (start or Path(__file__)).resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".git").exists() and (candidate / "scripts").is_dir():
            return candidate
    raise BoundaryViolation("staging_environment_boundary_refused")


def _is_text_candidate(path: Path) -> bool:
    if path.name.startswith(".env.staging"):
        return True
    suffix = path.suffix.lower()
    if suffix in _TEXT_SUFFIXES:
        return True
    # Extensionless shell helpers under infra-staging.
    return path.suffix == "" and path.name.endswith(".sh")


def iter_scan_paths(root: Path) -> Iterator[Path]:
    """Yield staging-scoped paths that must not name live production identity."""
    for relative in (".env.staging.backend", ".env.staging.frontend"):
        path = root / relative
        if path.is_file():
            yield path

    workflows = root / ".github" / "workflows"
    if workflows.is_dir():
        for name in sorted(_STAGING_WORKFLOWS):
            path = workflows / name
            if path.is_file():
                yield path

    infra_staging = root / "infra-staging"
    if infra_staging.is_dir():
        for path in sorted(infra_staging.rglob("*")):
            if path.is_file() and _is_text_candidate(path):
                yield path

    scripts = root / "scripts"
    if scripts.is_dir():
        for path in sorted(scripts.glob("staging_*")):
            if path.is_file() and _is_text_candidate(path):
                yield path

    tests = root / "tests"
    if tests.is_dir():
        for path in sorted(tests.rglob("*staging*")):
            if path.is_file() and _is_text_candidate(path):
                yield path


def relative_posix(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def scan_file(root: Path, path: Path) -> list[str]:
    relative = relative_posix(root, path)
    if relative in _SELF_RELATIVE:
        return []
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise BoundaryViolation(
            f"staging_environment_boundary_refused: unreadable {relative}"
        ) from error
    if len(raw) > _MAX_FILE_BYTES:
        raise BoundaryViolation(
            f"staging_environment_boundary_refused: oversized {relative}"
        )
    if b"\x00" in raw:
        return []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BoundaryViolation(
            f"staging_environment_boundary_refused: non-utf8 {relative}"
        ) from error

    findings: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for label, pattern in _FORBIDDEN:
            if pattern.search(line):
                findings.append(f"{relative}:{line_number}: forbidden `{label}`")
    return findings


def scan_repository(root: Path | None = None) -> list[str]:
    base = repository_root(root) if root is None else root.resolve()
    findings: list[str] = []
    seen: set[str] = set()
    for path in iter_scan_paths(base):
        relative = relative_posix(base, path)
        if relative in seen:
            continue
        seen.add(relative)
        findings.extend(scan_file(base, path))
    return findings


def _error(message: str) -> None:
    # Only emit workflow commands inside Actions. Unit tests call main() and
    # must not create ::error:: annotations that fail an unrelated CI step.
    prefix = (
        "::error::"
        if os.environ.get("GITHUB_ACTIONS") == "true"
        else "error: "
    )
    print(f"{prefix}{message}", file=sys.stderr)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fail if staging-scoped paths name live production hosts or live Clerk."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (defaults to the checkout that contains this script)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        findings = scan_repository(args.root)
    except BoundaryViolation as error:
        _error(str(error))
        return 1
    if findings:
        _error(
            "Staging-scoped paths named live production hosts or the "
            "live sign-in instance. Keep staging on staging; production identity "
            "belongs only on the founder-gated production promote path."
        )
        for finding in findings:
            _error(finding)
        return 1
    print("staging_environment_boundary_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
