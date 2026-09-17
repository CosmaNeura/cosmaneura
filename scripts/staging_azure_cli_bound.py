#!/usr/bin/env python3
"""Run one staging Azure CLI command with the exact verified user token.

The token arrives on descriptor 3 and is never placed in argv or the
environment. Azure CLI credential acquisition is replaced in-process so the
requested operation cannot switch to a different principal through its shared
credential cache after verification.
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
STAGING_RESOURCE_GROUP = "cosmaneura-staging"
STAGING_ENVIRONMENT = "cosmaneura-staging-app-environment"
STAGING_REGISTRY = "cosmaneurastagingregistry"
STAGING_REGISTRY_HOST = "cosmaneurastagingregistry.azurecr.io"
STAGING_REPOSITORY = "cosmaneura/cosmaneura"
CANDIDATE_SHA = "866bd4bc01e0e7be32c59eccdaf9dac3f495c7de"
STAGING_CONTAINER_NAMES = {
    "cosmaneura-staging-app",
    "cosmaneura-staging-migrate",
    "cosmaneura-staging-scheduler",
    "cosmaneura-staging-worker",
}
ALLOWED_ACR_COMMANDS = {
    ("acr", "credential", "show"),
    ("acr", "manifest", "show-metadata"),
}
ALLOWED_CONTAINERAPP_COMMANDS = {
    ("containerapp", "show"),
    ("containerapp", "update"),
    ("containerapp", "job", "create"),
    ("containerapp", "job", "delete"),
    ("containerapp", "job", "execution", "show"),
    ("containerapp", "job", "logs", "show"),
    ("containerapp", "job", "show"),
    ("containerapp", "job", "start"),
    ("containerapp", "replica", "list"),
    ("containerapp", "revision", "list"),
    ("containerapp", "revision", "show"),
    ("containerapp", "secret", "remove"),
    ("containerapp", "secret", "set"),
}
ALLOWED_OPTION_TOKENS = {
    "--args",
    "--command",
    "--container",
    "--container-name",
    "--cpu",
    "--environment",
    "--env-vars",
    "--execution",
    "--format",
    "--image",
    "--job-execution-name",
    "--memory",
    "--name",
    "--only-show-errors",
    "--output",
    "--query",
    "--registry",
    "--registry-password",
    "--registry-server",
    "--registry-username",
    "--remove-env-vars",
    "--replica-retry-limit",
    "--replica-timeout",
    "--resource-group",
    "--revision",
    "--revision-suffix",
    "--secret-names",
    "--secrets",
    "--set-env-vars",
    "--subscription",
    "--tail",
    "--trigger-type",
    "--yes",
    "-g",
    "-i",
    "-n",
    "-o",
}


class BoundAzureRefused(RuntimeError):
    """Fixed-message refusal for an invalid token or command."""


def require(condition: object) -> None:
    if not condition:
        raise BoundAzureRefused("staging_azure_cli_bound_refused")


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
    exact_record = cast(dict[str, Any], record)
    require(exact_record.get("subscription") == SUBSCRIPTION)
    require(exact_record.get("tenant") == TENANT)
    require(exact_record.get("tokenType") == "Bearer")
    token_value = exact_record.get("accessToken")
    require(type(token_value) is str and 100 < len(token_value) < 64 * 1024)
    token = cast(str, token_value)
    pieces = token.split(".")
    require(len(pieces) == 3)
    payload = pieces[1] + "=" * (-len(pieces[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload), object_pairs_hook=unique)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise BoundAzureRefused("staging_azure_cli_bound_refused") from None
    require(type(claims) is dict)
    claims = cast(dict[str, Any], claims)
    require(claims.get("tid") == TENANT and claims.get("aud") in audiences)
    require(claims.get("idtyp") == "user")
    expiry_value = claims.get("exp")
    not_before_value = claims.get("nbf")
    current_time = int(time.time()) if now is None else now
    require(type(current_time) is int)
    require(type(expiry_value) is int and expiry_value > current_time + 300)
    require(type(not_before_value) is int and not_before_value <= current_time + 30)
    expiry = cast(int, expiry_value)
    expires_on_value = exact_record.get("expires_on")
    require(
        type(expires_on_value) in {int, str} and not isinstance(expires_on_value, bool)
    )
    expires_on = cast(int | str, expires_on_value)
    try:
        record_expiry = int(expires_on)
    except ValueError:
        raise BoundAzureRefused("staging_azure_cli_bound_refused") from None
    require(abs(record_expiry - expiry) <= 5)
    username = claims.get("email")
    object_id = claims.get("oid")
    require(type(username) is str and sha(username) == USERNAME_SHA256)
    require(type(object_id) is str and sha(object_id) == OBJECT_ID_SHA256)
    return token, expiry


def token_record(descriptor: int, audiences: set[str]) -> tuple[str, int]:
    record: object = None
    try:
        with os.fdopen(descriptor) as stream:
            record = json.load(stream, object_pairs_hook=unique)
        return validate_token_record(record, audiences)
    except BoundAzureRefused:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise BoundAzureRefused("staging_azure_cli_bound_refused") from None
    finally:
        if type(record) is dict:
            record.clear()


def flag_values(argv: list[str], names: set[str]) -> list[str]:
    values: list[str] = []
    for index, argument in enumerate(argv):
        if argument in names:
            require(index + 1 < len(argv))
            values.append(argv[index + 1])
    return values


def command_matches(argv: list[str], allowed: set[tuple[str, ...]]) -> bool:
    return any(argv[: len(prefix)] == list(prefix) for prefix in allowed)


def validate_option_spellings(argv: list[str]) -> None:
    for index, argument in enumerate(argv):
        if not argument.startswith("-"):
            continue
        if argument == "-c":
            require(
                argv[:3] == ["containerapp", "job", "start"]
                and index > 0
                and argv[index - 1] == "--args"
                and argv.count("-c") == 1
                and flag_values(argv, {"--command"}) == ["/opt/venv/bin/python"]
            )
            continue
        require(argument in ALLOWED_OPTION_TOKENS)
        require(argv.count(argument) == 1)


def non_secret_argv(argv: list[str]) -> tuple[str, ...]:
    """Return command material safe for production-name scope checks.

    Secret definitions are still structurally checked, but their values are
    opaque data rather than Azure resource selectors. In particular, staging's
    database credential retains a legacy production-named username.
    """
    visible: list[str] = []
    index = 0
    while index < len(argv):
        argument = argv[index]
        visible.append(argument)
        if argument == "--registry-password":
            require(index + 1 < len(argv))
            index += 2
            continue
        if argument == "--secrets":
            index += 1
            while index < len(argv) and not argv[index].startswith("-"):
                name, separator, _value = argv[index].partition("=")
                require(separator == "=" and bool(name))
                visible.append(name)
                index += 1
            continue
        index += 1
    return tuple(visible)


def validate_staging_scope(argv: list[str]) -> None:
    if argv[0] == "containerapp":
        require(command_matches(argv, ALLOWED_CONTAINERAPP_COMMANDS))
        require(
            flag_values(argv, {"-g", "--resource-group"}) == [STAGING_RESOURCE_GROUP]
        )
        require(
            flag_values(argv, {"-n", "--name"})
            in [[name] for name in STAGING_CONTAINER_NAMES]
        )
        require("--ids" not in argv)
        for image in flag_values(argv, {"-i", "--image"}):
            require(
                re.fullmatch(
                    rf"{re.escape(STAGING_REGISTRY_HOST)}/"
                    rf"{re.escape(STAGING_REPOSITORY)}@sha256:[0-9a-f]{{64}}",
                    image,
                )
                is not None
            )
        for server in flag_values(argv, {"--registry-server"}):
            require(server == STAGING_REGISTRY_HOST)
        for environment in flag_values(argv, {"--environment"}):
            require(environment == STAGING_ENVIRONMENT)
        return

    require(command_matches(argv, ALLOWED_ACR_COMMANDS))
    if argv[:3] == ["acr", "manifest", "show-metadata"]:
        require(not flag_values(argv, {"-n"}))
        require(flag_values(argv, {"--registry"}) == [STAGING_REGISTRY])
        names = flag_values(argv, {"--name"})
        require(
            len(names) == 1
            and re.fullmatch(
                rf"{re.escape(STAGING_REPOSITORY)}"
                rf"(?::{re.escape(CANDIDATE_SHA)}|@sha256:[0-9a-f]{{64}})",
                names[0],
            )
            is not None
        )
    else:
        require(not flag_values(argv, {"--registry"}))
        require(flag_values(argv, {"-n", "--name"}) == [STAGING_REGISTRY])


def validate_argv(argv: list[str]) -> None:
    if argv == ["--verify-only"]:
        return
    require(argv and argv[0] in {"acr", "containerapp"})
    validate_option_spellings(argv)
    require(argv.count("--subscription") == 1)
    index = argv.index("--subscription")
    require(index + 1 < len(argv) and argv[index + 1] == SUBSCRIPTION)
    require(
        not any("cosmaneuraprod" in value.lower() for value in non_secret_argv(argv))
    )
    validate_staging_scope(argv)


def normalized_scopes(scopes: object) -> set[str]:
    if scopes is None:
        return set()
    if isinstance(scopes, str):
        return {scopes}
    require(isinstance(scopes, (list, tuple, set)))
    exact_scopes = cast(list[object] | tuple[object, ...] | set[object], scopes)
    require(all(type(scope) is str for scope in exact_scopes))
    return set(cast(list[str] | tuple[str, ...] | set[str], exact_scopes))


def main() -> int:
    require(sys.flags.isolated and not sys.flags.optimize)
    require(os.getuid() == os.geteuid() != 0)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    argv = sys.argv[1:]
    validate_argv(argv)
    token, expiry = token_record(3, MANAGEMENT_AUDIENCES)
    if argv == ["--verify-only"]:
        print(
            json.dumps(
                {
                    "azure_principal_bound": True,
                    "subscription": SUBSCRIPTION,
                    "tenant": TENANT,
                    "management_token_used_for_mutation": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    registry_required = argv[:3] == ["acr", "manifest", "show-metadata"]
    registry_token = registry_expiry = None
    if registry_required:
        registry_token, registry_expiry = token_record(4, {REGISTRY_AUDIENCE})

    from azure.cli.core import get_default_cli  # pyright: ignore[reportMissingImports]
    from azure.cli.core._profile import Profile  # pyright: ignore[reportMissingImports]
    from azure.core.credentials import AccessToken, AccessTokenInfo

    class StaticCredential:
        def get_token(self, *scopes: str, **_kwargs: Any) -> AccessToken:
            requested = normalized_scopes(scopes)
            if requested and requested.issubset(MANAGEMENT_SCOPES):
                return AccessToken(token, expiry)
            require(
                registry_token is not None
                and registry_expiry is not None
                and requested
                and requested.issubset(REGISTRY_SCOPES)
            )
            return AccessToken(cast(str, registry_token), cast(int, registry_expiry))

        def get_token_info(
            self, *scopes: str, options: dict[str, Any] | None = None
        ) -> AccessTokenInfo:
            del options
            requested = normalized_scopes(scopes)
            if requested and requested.issubset(MANAGEMENT_SCOPES):
                return AccessTokenInfo(token, expiry)
            require(
                registry_token is not None
                and registry_expiry is not None
                and requested
                and requested.issubset(REGISTRY_SCOPES)
            )
            return AccessTokenInfo(
                cast(str, registry_token), cast(int, registry_expiry)
            )

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
        requested_scopes = normalized_scopes(scopes)
        require(subscription in {None, SUBSCRIPTION})
        require(tenant in {None, TENANT})
        require(credential_out is None)
        if resource == REGISTRY_AUDIENCE or (
            requested_scopes and requested_scopes.issubset(REGISTRY_SCOPES)
        ):
            require(registry_token is not None and registry_expiry is not None)
            selected_token, selected_expiry = registry_token, registry_expiry
        else:
            require(resource in {None, *MANAGEMENT_AUDIENCES})
            require(
                not requested_scopes or requested_scopes.issubset(MANAGEMENT_SCOPES)
            )
            selected_token, selected_expiry = token, expiry
        entry = {"accessToken": selected_token, "expires_on": selected_expiry}
        return ("Bearer", selected_token, entry), SUBSCRIPTION, TENANT

    Profile.get_login_credentials = get_login_credentials
    Profile.get_raw_token = get_raw_token
    return int(get_default_cli().invoke(argv) or 0)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BoundAzureRefused:
        print("staging Azure command binding refused", file=sys.stderr)
        raise SystemExit(2) from None
