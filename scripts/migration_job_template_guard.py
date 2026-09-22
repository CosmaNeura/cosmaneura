#!/usr/bin/env python3
"""Validate the value-minimized Azure migration job template contract."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from typing import Any

ALEMBIC_ARGS = ["/opt/venv/bin/alembic upgrade head"]
# The pinned staging image predates the migrations/env.py ConfigParser fix.
# Patch only that boundary before Alembic imports the application: startup and
# all other database clients must continue to receive the original URL.
STAGING_ALEMBIC_BOOTSTRAP = (
    "from alembic.config import Config,CommandLine;"
    "original_set=Config.set_main_option;"
    "Config.set_main_option=lambda self,name,value:original_set("
    'self,name,value.replace("%","%%") if name=="sqlalchemy.url" else value);'
    'CommandLine().main(argv=["upgrade","head"])'
)
STAGING_ALEMBIC_ARGS = [
    "/opt/venv/bin/python -c " + shlex.quote(STAGING_ALEMBIC_BOOTSTRAP)
]


def _names(value: str) -> set[str]:
    return {name for name in value.split(",") if name}


def _fail(reason: str) -> int:
    print(f"Migration job template validation failed: {reason}.", file=sys.stderr)
    return 1


def validate_template(
    payload: Any,
    *,
    expected_name: str,
    expected_image: str,
    database_secret_name: str,
    raw_database_secret_name: str | None,
    secret_env_names: set[str],
    public_env_names: set[str],
    staging_alembic_compatibility: bool = False,
) -> str | None:
    if raw_database_secret_name is None:
        containers = payload
    else:
        if raw_database_secret_name == database_secret_name:
            return "database secret identities"
        if not isinstance(payload, dict) or set(payload) != {
            "containers",
            "secretNames",
        }:
            return "provider response shape"
        secret_names = payload["secretNames"]
        if not isinstance(secret_names, list) or not all(
            isinstance(name, str) and name for name in secret_names
        ):
            return "secret inventory shape"
        if (
            secret_names.count(database_secret_name) != 1
            or secret_names.count(raw_database_secret_name) != 1
        ):
            return "database secret inventory"
        containers = payload["containers"]
    if not isinstance(containers, list) or len(containers) != 1:
        return "container cardinality"

    container = containers[0]
    if not isinstance(container, dict):
        return "container shape"
    if container.get("name") != expected_name:
        return "container identity"
    if container.get("image") != expected_image:
        return "image identity"
    if container.get("command") not in (None, []):
        return "persistent command"
    expected_args = (
        STAGING_ALEMBIC_ARGS if staging_alembic_compatibility else ALEMBIC_ARGS
    )
    if container.get("args") != expected_args:
        return "persistent arguments"

    env = container.get("env")
    if not isinstance(env, list):
        return "environment shape"
    if not all(isinstance(item, dict) for item in env):
        return "environment entry shape"

    env_names = [item.get("name") for item in env]
    if not all(isinstance(name, str) and name for name in env_names):
        return "environment identity"
    if len(env_names) != len(set(env_names)):
        return "duplicate environment identity"

    allowed = secret_env_names | public_env_names
    if set(env_names) - allowed:
        return "unexpected environment identity"

    by_name = {item["name"]: item for item in env}
    database = by_name.get("DATABASE_URL")
    if database is None or database.get("secretRef") != database_secret_name:
        return "database secret reference"

    for name in secret_env_names:
        item = by_name.get(name)
        if item is None:
            continue
        expected_secret = (
            database_secret_name
            if name == "DATABASE_URL"
            else name.lower().replace("_", "-")
        )
        if item.get("secretRef") != expected_secret:
            return "secret environment reference"

    for name in public_env_names:
        item = by_name.get(name)
        if item is not None and item.get("secretRef") not in (None, ""):
            return "public environment reference"

    return None


def main() -> int:
    if sys.argv[1:] == ["--staging-alembic-command"]:
        print(STAGING_ALEMBIC_ARGS[0])
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-name", required=True)
    parser.add_argument("--expected-image", required=True)
    parser.add_argument("--database-secret-name", required=True)
    parser.add_argument("--raw-database-secret-name")
    parser.add_argument("--secret-env-names", required=True)
    parser.add_argument("--public-env-names", default="")
    parser.add_argument("--staging-alembic-compatibility", action="store_true")
    args = parser.parse_args()

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _fail("invalid provider response")

    reason = validate_template(
        payload,
        expected_name=args.expected_name,
        expected_image=args.expected_image,
        database_secret_name=args.database_secret_name,
        raw_database_secret_name=args.raw_database_secret_name,
        secret_env_names=_names(args.secret_env_names),
        public_env_names=_names(args.public_env_names),
        staging_alembic_compatibility=args.staging_alembic_compatibility,
    )
    return _fail(reason) if reason else 0


if __name__ == "__main__":
    raise SystemExit(main())
