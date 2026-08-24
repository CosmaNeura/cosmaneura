#!/usr/bin/env python3
"""Validate the environment-scoped Clerk issuer used by admin capabilities.

The Clerk publishable key encodes the instance's Frontend API hostname.  This
standalone helper derives the corresponding JWT issuer or verifies a resolved
deployment JSON document without importing the application, contacting Clerk,
or emitting any credential value.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import sys
from collections.abc import Mapping
from typing import Literal
from urllib.parse import urlsplit

Environment = Literal["staging", "production"]
MAX_CONFIGURATION_BYTES = 1_048_576


class ConfigurationError(ValueError):
    """A value-minimized deployment configuration failure."""


def _clerk_issuer_from_publishable_key(
    publishable_key: object,
    *,
    environment: Environment,
) -> str:
    if not isinstance(publishable_key, str) or not publishable_key:
        raise ConfigurationError("Clerk publishable key is invalid")
    prefix = "pk_live_" if environment == "production" else "pk_test_"
    if not publishable_key.startswith(prefix):
        raise ConfigurationError("Clerk instance mode does not match environment")
    encoded = publishable_key[len(prefix) :]
    if not encoded or len(encoded) > 4096:
        raise ConfigurationError("Clerk publishable key is invalid")
    padded = encoded + "=" * ((4 - len(encoded) % 4) % 4)
    try:
        decoded = base64.b64decode(
            padded.encode("ascii"),
            altchars=b"-_",
            validate=True,
        ).decode("ascii")
    except (UnicodeEncodeError, UnicodeDecodeError, binascii.Error):
        raise ConfigurationError("Clerk publishable key is invalid") from None
    if not decoded.endswith("$") or decoded.count("$") != 1:
        raise ConfigurationError("Clerk publishable key is invalid")
    frontend_api = decoded[:-1]
    issuer = f"https://{frontend_api}"
    parsed = urlsplit(issuer)
    try:
        port = parsed.port
    except ValueError:
        raise ConfigurationError("Clerk publishable key is invalid") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.netloc != parsed.hostname
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or issuer != issuer.rstrip("/")
    ):
        raise ConfigurationError("Clerk publishable key is invalid")
    return issuer


def validate_resolved_configuration(
    payload: object,
    *,
    environment: Environment,
) -> None:
    if not isinstance(payload, Mapping):
        raise ConfigurationError("deployment configuration is invalid")
    if payload.get("PYTHON_ENV") != environment:
        raise ConfigurationError("runtime environment is invalid")

    configured_issuer = payload.get("AUTHORITY_CLERK_ISSUER")
    expected_issuer = _clerk_issuer_from_publishable_key(
        payload.get("CLERK_DEPLOYMENT_PUBLISHABLE_KEY"),
        environment=environment,
    )
    if not isinstance(configured_issuer, str) or configured_issuer != expected_issuer:
        raise ConfigurationError("configured Clerk issuer does not match instance")

    private_key = payload.get("CLERK_PRIVATE_KEY")
    expected_private_prefix = "sk_live_" if environment == "production" else "sk_test_"
    if (
        not isinstance(private_key, str)
        or not private_key.startswith(expected_private_prefix)
        or len(private_key) <= len(expected_private_prefix)
    ):
        raise ConfigurationError("Clerk private-key mode does not match environment")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument(
        "--environment",
        required=True,
        choices=("staging", "production"),
    )
    parser.add_argument(
        "--derive-issuer",
        action="store_true",
        help="derive the issuer from a publishable key supplied on stdin",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(MAX_CONFIGURATION_BYTES + 1)
        if not raw or len(raw) > MAX_CONFIGURATION_BYTES:
            raise ConfigurationError("deployment configuration is invalid")
        if args.derive_issuer:
            key = raw.decode("utf-8").strip()
            issuer = _clerk_issuer_from_publishable_key(
                key,
                environment=args.environment,
            )
            # This mode is used only inside command substitution. The value is
            # public configuration, but callers must not tee it to logs.
            sys.stdout.write(issuer)
            return 0
        payload = json.loads(raw)
        validate_resolved_configuration(payload, environment=args.environment)
    except (ConfigurationError, UnicodeDecodeError, json.JSONDecodeError):
        print("Superadmin capability configuration is invalid.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
