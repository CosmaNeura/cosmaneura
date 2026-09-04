#!/usr/bin/env python3
"""Fail closed before Cursor Cloud Agent database commands.

The wrapper exact-matches the repository's approved local URLs, snapshots the
validated environment, and passes that same snapshot to the child process.
It deliberately performs no URL normalization and has no remote override.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence

CANONICAL_DATABASE_URLS = {
    "dev": frozenset(
        {
            "postgresql://root:password@localhost:5432/development",
            "postgresql://root:password@127.0.0.1:5432/development",
            "postgresql://root:password@[::1]:5432/development",
        }
    ),
    "test": frozenset(
        {
            "postgresql://root:password@localhost:5432/test",
            "postgresql://root:password@127.0.0.1:5432/test",
            "postgresql://root:password@[::1]:5432/test",
        }
    ),
}


class LocalDatabaseSafetyError(ValueError):
    """Raised when an automatic database target is not exactly local/dev."""


def _require_exact_url(*, environment_name: str, value: str | None, mode: str) -> str:
    if value not in CANONICAL_DATABASE_URLS[mode]:
        raise LocalDatabaseSafetyError(
            f"{environment_name} is not an exact approved local {mode} URL"
        )
    return value


def validate_database_environment(*, mode: str, environment: Mapping[str, str]) -> str:
    """Return the exact target URL after validating the whole mode boundary."""

    if environment.get("COSMANEURA_CURSOR_CREDENTIAL_NEUTRAL") != "1":
        raise LocalDatabaseSafetyError(
            "credential-neutral Cursor marker must be exactly 1"
        )

    if mode not in CANONICAL_DATABASE_URLS:
        raise LocalDatabaseSafetyError("database mode must be dev or test")

    expected_python_environment = "development" if mode == "dev" else "test"
    if environment.get("PYTHON_ENV") != expected_python_environment:
        raise LocalDatabaseSafetyError(
            f"PYTHON_ENV must be {expected_python_environment} in {mode} mode"
        )

    expected_ci = "" if mode == "dev" else "true"
    if environment.get("CI", "") != expected_ci:
        raise LocalDatabaseSafetyError(f"CI must be {expected_ci!r} in {mode} mode")

    # Test commands run after `CI=true direnv exec .` reloads committed .env.test.
    # Validate both URLs inside that final environment so a divergent override
    # cannot leave any live database pointer available to the child process.
    if mode == "test":
        _require_exact_url(
            environment_name="DATABASE_URL",
            value=environment.get("DATABASE_URL"),
            mode="dev",
        )
        target_name = "TEST_DATABASE_URL"
    else:
        target_name = "DATABASE_URL"

    return _require_exact_url(
        environment_name=target_name,
        value=environment.get(target_name),
        mode=mode,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Authorize an exact local Cursor database environment, then optionally "
            "execute a command with that validated environment snapshot."
        )
    )
    parser.add_argument("--mode", choices=("dev", "test"), required=True)
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="command to execute after validation (place it after --)",
    )
    args = parser.parse_args(argv)

    validated_environment = dict(os.environ)
    try:
        target_url = validate_database_environment(
            mode=args.mode,
            environment=validated_environment,
        )
    except LocalDatabaseSafetyError as exc:
        print(f"Refusing automatic database command: {exc}.", file=sys.stderr)
        return 2

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if command:
        try:
            os.execvpe(command[0], command, validated_environment)
        except OSError as exc:
            print(
                f"Unable to execute validated database command {command[0]!r}: "
                f"{exc.strerror}.",
                file=sys.stderr,
            )
            return 127
        return 0

    print(f"Cursor {args.mode} database target is exact, local, and approved.")
    assert target_url in CANONICAL_DATABASE_URLS[args.mode]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
