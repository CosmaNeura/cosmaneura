#!/usr/bin/env python3
"""Non-disclosing live guard for Recall signing-secret syntax and scope."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

SUBSCRIPTION_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
RESOURCE_GROUP_RE = re.compile(r"^[A-Za-z0-9._()\-]{1,90}$")
ENV_NAME = "RECALL_WEBHOOK_SECRET"
MIN_SIGNING_SECRET_BYTES = 16
SECRET_NAME = "recall-webhook-secret"


class LiveGuardError(RuntimeError):
    """A sanitized guard failure; provider output is never propagated."""


@dataclass(frozen=True)
class Workload:
    label: str
    name: str
    cli_group: tuple[str, ...]
    must_have_secret: bool = False


WORKLOADS = (
    Workload("api", "cosmaneura-prod-app", ("containerapp",), True),
    Workload("worker", "cosmaneura-prod-worker", ("containerapp",)),
    Workload("scheduler", "cosmaneura-prod-scheduler", ("containerapp",)),
    Workload("jmon", "cosmaneura-prod-app-jmon", ("containerapp",)),
    Workload("migration", "cosmaneura-prod-migrate", ("containerapp", "job")),
)


def _run_az(arguments: tuple[str, ...]) -> str:
    environment = os.environ.copy()
    environment.pop(ENV_NAME, None)
    environment.pop("OP_SERVICE_ACCOUNT_TOKEN", None)
    try:
        result = subprocess.run(
            ["az", *arguments, "--only-show-errors"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LiveGuardError("Azure CLI invocation failed") from exc
    if result.returncode != 0:
        raise LiveGuardError("Azure CLI returned a failure")
    return result.stdout


def read_query(stream: Any) -> dict[str, str]:
    try:
        query = json.load(stream)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise LiveGuardError("guard input is invalid JSON") from exc
    if not isinstance(query, dict) or set(query) != {
        "resource_group",
        "subscription_id",
    }:
        raise LiveGuardError("guard input fields are invalid")
    if not all(isinstance(value, str) for value in query.values()):
        raise LiveGuardError("guard input values must be strings")
    return query


def validate_query(query: dict[str, str]) -> None:
    if not SUBSCRIPTION_RE.fullmatch(query["subscription_id"].lower()):
        raise LiveGuardError("subscription ID is invalid")
    if not RESOURCE_GROUP_RE.fullmatch(query["resource_group"]):
        raise LiveGuardError("resource group is invalid")


def validate_signing_secret(secret: str) -> None:
    """Validate only syntax; errors never include the credential value."""

    if secret != secret.strip() or not secret.startswith("whsec_"):
        raise LiveGuardError("Recall signing credential syntax is invalid")
    payload = secret.removeprefix("whsec_")
    try:
        decoded = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise LiveGuardError("Recall signing credential syntax is invalid") from None
    if (
        len(decoded) < MIN_SIGNING_SECRET_BYTES
        or base64.b64encode(decoded).decode("ascii") != payload
    ):
        raise LiveGuardError("Recall signing credential syntax is invalid")


def secret_scope_state(resource: dict[str, Any]) -> tuple[list[dict], list[str]]:
    try:
        containers = resource["properties"]["template"]["containers"]
        secrets = resource["properties"]["configuration"]["secrets"]
    except (KeyError, TypeError) as exc:
        raise LiveGuardError("workload configuration is incomplete") from exc
    if not isinstance(containers, list) or len(containers) != 1:
        raise LiveGuardError("workload must have exactly one container")
    environment = containers[0].get("env", [])
    if not isinstance(environment, list) or not isinstance(secrets, list):
        raise LiveGuardError("workload configuration is invalid")
    entries = [
        entry
        for entry in environment
        if isinstance(entry, dict) and entry.get("name") == ENV_NAME
    ]
    names = [
        entry["name"]
        for entry in secrets
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    ]
    if len(names) != len(secrets) or len(names) != len(set(names)):
        raise LiveGuardError("workload contains invalid or duplicate secret names")
    return entries, names


def verify_live_state(
    query: dict[str, str],
    *,
    runner: Any = _run_az,
    signing_secret: str | None = None,
) -> int:
    validate_query(query)
    validate_signing_secret(
        os.environ.get(ENV_NAME, "") if signing_secret is None else signing_secret
    )
    for workload in WORKLOADS:
        raw = runner(
            (
                *workload.cli_group,
                "show",
                "--name",
                workload.name,
                "--resource-group",
                query["resource_group"],
                "--subscription",
                query["subscription_id"],
                "--output",
                "json",
            )
        )
        try:
            resource = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LiveGuardError("Azure returned invalid workload JSON") from exc
        if not isinstance(resource, dict):
            raise LiveGuardError("Azure returned unexpected workload JSON")
        entries, names = secret_scope_state(resource)
        if workload.must_have_secret:
            if (
                len(entries) != 1
                or entries[0].get("secretRef") != SECRET_NAME
                or entries[0].get("value") not in (None, "")
                or names.count(SECRET_NAME) != 1
            ):
                raise LiveGuardError("API secret reference is invalid")
        elif entries or SECRET_NAME in names:
            raise LiveGuardError("API-only secret leaked to a non-API workload")
    return len(WORKLOADS)


def main() -> int:
    try:
        query = read_query(sys.stdin)
        workload_count = verify_live_state(query)
    except LiveGuardError:
        print(
            json.dumps({"code": "api_only_secret_live_guard_failed"}),
            file=sys.stderr,
        )
        return 1
    print(json.dumps({"result": "verified", "workload_count": workload_count}))
    return 0


def validate_signing_secret_only() -> int:
    """Validate the configured signing key without querying or mutating Azure."""

    try:
        validate_signing_secret(os.environ.get(ENV_NAME, ""))
    except LiveGuardError:
        print(
            json.dumps({"code": "recall_signing_secret_validation_failed"}),
            file=sys.stderr,
        )
        return 1
    print(json.dumps({"result": "verified"}))
    return 0


def cli(arguments: list[str]) -> int:
    if arguments == ["--validate-signing-secret-only"]:
        return validate_signing_secret_only()
    if arguments:
        print(json.dumps({"code": "invalid_guard_arguments"}), file=sys.stderr)
        return 1
    return main()


if __name__ == "__main__":
    raise SystemExit(cli(sys.argv[1:]))
