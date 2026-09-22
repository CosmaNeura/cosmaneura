#!/usr/bin/env python3
"""Run one exact staging ACR publication command with verified user tokens.

The management and registry tokens arrive on descriptors 3 and 4. They are
never placed in argv or the environment. This publisher-only wrapper cannot
invoke Container Apps, deployment, migration, credential export, or production.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import resource
import sys
import time
from typing import Any, cast

SUBSCRIPTION = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"
TENANT = "d51754d0-bba5-4810-820e-afac542844d6"
USERNAME_SHA256 = "acf2c107f43fa6950918e5f089e1b865ba56188295b88d3d72ad342d742e616a"
OBJECT_ID_SHA256 = "0839f64960f7e378762f2f1aab2d324b197c4c0d2198f87d9f595df4eb0b1f93"
MANAGEMENT_AUDIENCES = {
    "https://management.azure.com/",
    "https://management.core.windows.net/",
}
MANAGEMENT_SCOPES = {
    "https://management.azure.com/.default",
    "https://management.azure.com//.default",
    "https://management.core.windows.net/.default",
    "https://management.core.windows.net//.default",
}
REGISTRY_AUDIENCE = "https://containerregistry.azure.net"
REGISTRY_SCOPES = {
    "https://containerregistry.azure.net/.default",
    "https://containerregistry.azure.net//.default",
}
REGISTRY = "cosmaneurastagingregistry"
REPOSITORY = "cosmaneura/cosmaneura"
CANDIDATE = "866bd4bc01e0e7be32c59eccdaf9dac3f495c7de"


class BoundAcrRefused(RuntimeError):
    """Fixed-message refusal for an invalid token or command."""


def require(condition: object) -> None:
    if not condition:
        raise BoundAcrRefused("staging_acr_cli_bound_refused")


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        require(key not in value)
        value[key] = item
    return value


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def validate_token_record(
    record: object, audiences: set[str], *, now: int | None = None
) -> tuple[str, int]:
    require(type(record) is dict)
    exact = cast(dict[str, Any], record)
    require(exact.get("subscription") == SUBSCRIPTION)
    require(exact.get("tenant") == TENANT)
    require(exact.get("tokenType") == "Bearer")
    token_value = exact.get("accessToken")
    require(type(token_value) is str and 100 < len(token_value) < 64 * 1024)
    token = cast(str, token_value)
    pieces = token.split(".")
    require(len(pieces) == 3)
    payload = pieces[1] + "=" * (-len(pieces[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload), object_pairs_hook=unique)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise BoundAcrRefused("staging_acr_cli_bound_refused") from None
    require(type(claims) is dict)
    claims = cast(dict[str, Any], claims)
    require(claims.get("tid") == TENANT and claims.get("aud") in audiences)
    require(claims.get("idtyp") == "user")
    expiry_value, not_before_value = claims.get("exp"), claims.get("nbf")
    current_time = int(time.time()) if now is None else now
    require(type(current_time) is int)
    require(type(expiry_value) is int and expiry_value > current_time + 300)
    require(type(not_before_value) is int and not_before_value <= current_time + 30)
    expiry = cast(int, expiry_value)
    recorded_expiry = exact.get("expires_on")
    require(
        type(recorded_expiry) in {int, str} and not isinstance(recorded_expiry, bool)
    )
    try:
        require(abs(int(cast(int | str, recorded_expiry)) - expiry) <= 5)
    except ValueError:
        raise BoundAcrRefused("staging_acr_cli_bound_refused") from None
    username, object_id = claims.get("email"), claims.get("oid")
    require(type(username) is str and sha(username) == USERNAME_SHA256)
    require(type(object_id) is str and sha(object_id) == OBJECT_ID_SHA256)
    return token, expiry


def token_record(descriptor: int, audiences: set[str]) -> tuple[str, int]:
    record: object = None
    try:
        with os.fdopen(descriptor) as stream:
            record = json.load(stream, object_pairs_hook=unique)
        return validate_token_record(record, audiences)
    except BoundAcrRefused:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise BoundAcrRefused("staging_acr_cli_bound_refused") from None
    finally:
        if type(record) is dict:
            record.clear()


def normalized_scopes(scopes: object) -> set[str]:
    if scopes is None:
        return set()
    if isinstance(scopes, str):
        return {scopes}
    require(isinstance(scopes, (list, tuple, set)))
    exact = cast(list[object] | tuple[object, ...] | set[object], scopes)
    require(all(type(scope) is str for scope in exact))
    return set(cast(list[str] | tuple[str, ...] | set[str], exact))


def validate_argv(argv: list[str]) -> None:
    require(argv[-2:] == ["--subscription", SUBSCRIPTION])
    command = argv[:-2]
    exact_commands = (
        [
            "acr",
            "login",
            "--name",
            REGISTRY,
            "--expose-token",
            "--only-show-errors",
            "--output",
            "json",
        ],
        [
            "acr",
            "repository",
            "show-tags",
            "--name",
            REGISTRY,
            "--repository",
            REPOSITORY,
            "--query",
            f"[?@=='{CANDIDATE}']",
            "--output",
            "json",
        ],
        [
            "acr",
            "manifest",
            "show-metadata",
            "--registry",
            REGISTRY,
            "--name",
            f"{REPOSITORY}:{CANDIDATE}",
            "--query",
            "digest",
            "--output",
            "tsv",
        ],
        [
            "acr",
            "repository",
            "update",
            "--name",
            REGISTRY,
            "--image",
            f"{REPOSITORY}:{CANDIDATE}",
            "--write-enabled",
            "false",
            "--delete-enabled",
            "false",
            "--only-show-errors",
            "--output",
            "none",
        ],
        [
            "acr",
            "repository",
            "show",
            "--name",
            REGISTRY,
            "--image",
            f"{REPOSITORY}:{CANDIDATE}",
            "--output",
            "json",
        ],
    )
    if command in exact_commands:
        return
    require(len(command) == 13)
    require(
        command[:6] == ["acr", "import", "--name", REGISTRY, "--registry", REGISTRY]
        and command[6] == "--source"
        and re.fullmatch(rf"{re.escape(REPOSITORY)}@sha256:[0-9a-f]{{64}}", command[7])
        is not None
        and command[8:]
        == [
            "--image",
            f"{REPOSITORY}:{CANDIDATE}",
            "--only-show-errors",
            "--output",
            "none",
        ]
    )


def main() -> int:
    require(sys.flags.isolated and not sys.flags.optimize)
    require(os.getuid() == os.geteuid() != 0)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    argv = sys.argv[1:]
    validate_argv(argv)
    management_token, management_expiry = token_record(3, MANAGEMENT_AUDIENCES)
    registry_token, registry_expiry = token_record(4, {REGISTRY_AUDIENCE})

    from azure.cli.core import get_default_cli  # pyright: ignore[reportMissingImports]
    from azure.cli.core._profile import Profile  # pyright: ignore[reportMissingImports]
    from azure.core.credentials import (  # pyright: ignore[reportMissingImports]
        AccessToken,
        AccessTokenInfo,
    )

    class StaticCredential:
        def select(self, scopes: object) -> tuple[str, int]:
            requested = normalized_scopes(scopes)
            require(requested)
            if requested.issubset(MANAGEMENT_SCOPES):
                return management_token, management_expiry
            require(requested.issubset(REGISTRY_SCOPES))
            return registry_token, registry_expiry

        def get_token(self, *scopes: str, **_kwargs: Any) -> AccessToken:
            token, expiry = self.select(scopes)
            return AccessToken(token, expiry)

        def get_token_info(
            self, *scopes: str, options: dict[str, Any] | None = None
        ) -> AccessTokenInfo:
            del options
            token, expiry = self.select(scopes)
            return AccessTokenInfo(token, expiry)

    credential = StaticCredential()

    def get_login_credentials(
        _profile,
        subscription_id=None,
        aux_subscriptions=None,
        aux_tenants=None,
        sdk_credential=True,
    ):
        require(subscription_id in {None, SUBSCRIPTION})
        require(not aux_subscriptions and not aux_tenants and sdk_credential is True)
        return credential, SUBSCRIPTION, TENANT

    def get_raw_token(
        _profile,
        resource=None,
        scopes=None,
        subscription=None,
        tenant=None,
        credential_out=None,
    ):
        requested = normalized_scopes(scopes)
        require(subscription in {None, SUBSCRIPTION})
        require(tenant in {None, TENANT})
        require(credential_out is None)
        if resource == REGISTRY_AUDIENCE or (
            requested and requested.issubset(REGISTRY_SCOPES)
        ):
            token, expiry = registry_token, registry_expiry
        else:
            require(resource in {None, *MANAGEMENT_AUDIENCES})
            require(not requested or requested.issubset(MANAGEMENT_SCOPES))
            token, expiry = management_token, management_expiry
        entry = {"accessToken": token, "expires_on": expiry}
        return ("Bearer", token, entry), SUBSCRIPTION, TENANT

    Profile.get_login_credentials = get_login_credentials
    Profile.get_raw_token = get_raw_token
    return int(get_default_cli().invoke(argv) or 0)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BoundAcrRefused:
        print("staging ACR command binding refused", file=sys.stderr)
        raise SystemExit(2) from None
