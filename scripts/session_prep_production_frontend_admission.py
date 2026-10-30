#!/usr/bin/env python3
"""Admit a reviewed manual production build's Session Prep frontend flag.

This public receipt binds build inputs to the already protected founder workflow.
It is neither independent review nor permission to activate records, IAM or the
backend. No application import, credential resolution, network or cloud call runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import cast

from superadmin_capability_config_preflight import (
    ConfigurationError,
    _clerk_issuer_from_publishable_key,
)

FLAG = "VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED"
PUBLIC_KEYS = frozenset(
    {
        "NODE_ENV",
        "OPENAPI_JSON_PATH",
        "VITE_CLERK_PUBLIC_KEY",
        "VITE_INTAKE_CHAT_DOMAIN",
        "VITE_POSTHOG_HOST",
        "VITE_POSTHOG_KEY",
        "VITE_PYTHON_URL",
        "VITE_SENTRY_DSN",
        FLAG,
        "VITE_SOVEREIGN_PROVIDER_URL",
    }
)
WORKFLOW_REF = (
    "CosmaNeura/cosmaneura/.github/workflows/"
    "build_and_publish_local.yml@refs/heads/master"
)
MAX_BYTES = 16384
REFUSAL = "production_session_prep_frontend_admission_refused"


class AdmissionRefused(ValueError):
    """A fixed, configuration-free refusal."""


def require(value: object) -> None:
    if not value:
        raise AdmissionRefused(REFUSAL)


def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def decode(raw: bytes) -> dict[str, object]:
    require(0 < len(raw) <= MAX_BYTES)
    value = json.loads(raw, object_pairs_hook=unique)
    require(type(value) is dict)
    return cast(dict[str, object], value)


def read_file(path: Path) -> bytes:
    # Both files are run-local, never links to an arbitrary credential file.
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode))
        raw = stream.read(MAX_BYTES + 1)
    require(0 < len(raw) <= MAX_BYTES)
    return raw


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def configuration(raw: bytes) -> dict[str, str]:
    value = decode(raw)
    require(set(value) == PUBLIC_KEYS)
    require(
        all(
            type(item) is str
            and 0 < len(item) <= 4096
            and item.isprintable()
            and item == item.strip()
            and not item.startswith("op://")
            for item in value.values()
        )
    )
    require(value[FLAG] in {"true", "false"})
    return cast(dict[str, str], value)


def production_configuration(config: dict[str, str]) -> None:
    require(config["NODE_ENV"] == "production")
    require(config["OPENAPI_JSON_PATH"] == "openapi.json")
    require(config["VITE_PYTHON_URL"] == "https://app.cosmaneura.com/")
    require(config["VITE_INTAKE_CHAT_DOMAIN"] == "chat.cosmaneura.com")
    require(config["VITE_SOVEREIGN_PROVIDER_URL"] == "https://sovereign.cosmaneura.com")
    # The environment's existing public key supplies the production instance.
    # Runtime issuer alignment is still enforced by the separate Core preflight.
    issuer = _clerk_issuer_from_publishable_key(
        config["VITE_CLERK_PUBLIC_KEY"], environment="production"
    )
    require(re.fullmatch(r"https://[a-z0-9]+(?:[.-][a-z0-9]+)+", issuer))
    require("staging" not in issuer and not issuer.endswith(".accounts.dev"))


def context(source_sha: str) -> dict[str, str]:
    require(re.fullmatch(r"[0-9a-f]{40}", source_sha))
    expected = {
        "GITHUB_ACTOR": "JohnClem",
        "GITHUB_TRIGGERING_ACTOR": "JohnClem",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/master",
        "GITHUB_REPOSITORY": "CosmaNeura/cosmaneura",
        "GITHUB_WORKFLOW_REF": WORKFLOW_REF,
        "GITHUB_SHA": source_sha,
    }
    require(all(os.environ.get(key) == value for key, value in expected.items()))
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    require(re.fullmatch(r"[1-9][0-9]{0,19}", run_id))
    expected["GITHUB_RUN_ID"] = run_id
    return expected


def receipt(config: dict[str, str], source_sha: str) -> dict[str, object]:
    return {
        "version": 1,
        "purpose": "session-prep-frontend-build-only",
        "source_sha": source_sha,
        "public_configuration_sha256": digest(config),
        "manual_workflow_context": context(source_sha),
    }


def prepare(
    config_path: Path,
    admission_path: Path,
    enabled: str,
    source_sha: str,
    reviewed_source_sha: str,
) -> dict[str, object]:
    require(enabled in {"true", "false"})
    require(config_path != admission_path and not admission_path.exists())
    config = configuration(read_file(config_path))
    require(config[FLAG] == "false")  # The resolver's forced-dark projection.
    if enabled == "false":
        return {"enabled": False}
    require(reviewed_source_sha == source_sha)
    production_configuration(config)
    config[FLAG] = "true"
    admission = receipt(config, source_sha)
    # A failed/partial preparation never enters the build. The workflow scrubs
    # these two fixed run-local paths on success and failure.
    with os.fdopen(
        os.open(
            admission_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        ),
        "wb",
    ) as stream:
        stream.write(canonical(admission) + b"\n")
    with tempfile.NamedTemporaryFile(dir=config_path.parent, delete=False) as stream:
        replacement = Path(stream.name)
        try:
            stream.write(canonical(config) + b"\n")
            stream.flush()
            os.replace(replacement, config_path)
        finally:
            replacement.unlink(missing_ok=True)
    return {
        "enabled": True,
        "source_sha": source_sha,
        "public_configuration_sha256": digest(config),
        "admission_sha256": digest(admission),
    }


def verify(raw: bytes, admission_path: Path, source_sha: str) -> None:
    config = configuration(raw)
    require(config[FLAG] == "true")
    production_configuration(config)
    require(
        canonical(decode(read_file(admission_path)))
        == canonical(receipt(config, source_sha))
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subcommands.add_parser("prepare")
    prepare_parser.add_argument("--configuration", required=True, type=Path)
    prepare_parser.add_argument("--enabled", required=True)
    prepare_parser.add_argument("--reviewed-source-sha", required=True)
    verify_parser = subcommands.add_parser("verify")
    for command in (prepare_parser, verify_parser):
        command.add_argument("--admission", required=True, type=Path)
        command.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            summary = prepare(
                args.configuration,
                args.admission,
                args.enabled,
                args.source_sha,
                args.reviewed_source_sha,
            )
            print(canonical(summary).decode("utf-8"))
        else:
            verify(
                sys.stdin.buffer.read(MAX_BYTES + 1), args.admission, args.source_sha
            )
    except (ValueError, OSError, UnicodeError, RecursionError, ConfigurationError):
        print(REFUSAL, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
