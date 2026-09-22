#!/usr/bin/env python3
"""Fail closed when declarative DATABASE_URL selection disagrees with Azure.

The external-data input and output contain only non-secret identifiers.  This
guard deliberately permits phase A (declare an unused v2 secret while every
workload still selects the old secret), but it refuses to let OpenTofu or the
image deploy become the mechanism that moves workloads to v2. Selecting v2 is
valid only after the staged cutover operator has already moved all four live
workloads and retained the old rollback secret.
"""

from __future__ import annotations

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
ALLOWED_SECRETS = {"database-url", "database-url-v2"}


class LiveGuardError(RuntimeError):
    """A sanitized live-state guard failure."""


@dataclass(frozen=True)
class Workload:
    label: str
    name: str
    cli_group: tuple[str, ...]


WORKLOADS = (
    Workload("worker", "cosmaneura-prod-worker", ("containerapp",)),
    Workload("scheduler", "cosmaneura-prod-scheduler", ("containerapp",)),
    Workload("api", "cosmaneura-prod-app", ("containerapp",)),
    Workload("migration", "cosmaneura-prod-migrate", ("containerapp", "job")),
)


def _run_az(arguments: tuple[str, ...]) -> str:
    environment = os.environ.copy()
    for name in ("DATABASE_URL", "DATABASE_URL_V2", "COSMANEURA_DATABASE_URL_V2"):
        environment.pop(name, None)
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
    except (OSError, subprocess.SubprocessError) as error:
        raise LiveGuardError("Azure CLI invocation failed") from error
    if result.returncode != 0:
        raise LiveGuardError("Azure CLI returned a failure")
    return result.stdout


def read_query(stream: Any) -> dict[str, str]:
    try:
        payload = json.load(stream)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise LiveGuardError("guard input is invalid JSON") from error
    if not isinstance(payload, dict) or set(payload) != {
        "resource_group",
        "selected_secret",
        "subscription_id",
        "v2_enabled",
    }:
        raise LiveGuardError("guard input fields are invalid")
    if not all(isinstance(value, str) for value in payload.values()):
        raise LiveGuardError("guard input values must be strings")
    return payload


def validate_query(query: dict[str, str]) -> None:
    if not SUBSCRIPTION_RE.fullmatch(query["subscription_id"].lower()):
        raise LiveGuardError("subscription ID is invalid")
    if not RESOURCE_GROUP_RE.fullmatch(query["resource_group"]):
        raise LiveGuardError("resource group is invalid")
    if query["selected_secret"] not in ALLOWED_SECRETS:
        raise LiveGuardError("selected database secret is invalid")
    if query["v2_enabled"] not in {"true", "false"}:
        raise LiveGuardError("v2 enabled flag is invalid")
    if query["selected_secret"] == "database-url-v2" and query["v2_enabled"] != "true":
        raise LiveGuardError("v2 cannot be selected while disabled")


def database_state(resource: dict[str, Any]) -> tuple[str, set[str]]:
    try:
        containers = resource["properties"]["template"]["containers"]
        secrets = resource["properties"]["configuration"]["secrets"]
    except (KeyError, TypeError) as error:
        raise LiveGuardError("workload configuration is incomplete") from error
    if not isinstance(containers, list) or len(containers) != 1:
        raise LiveGuardError("workload must have exactly one container")
    container = containers[0]
    if not isinstance(container, dict):
        raise LiveGuardError("workload container configuration is invalid")
    environment = container.get("env", [])
    if not isinstance(environment, list):
        raise LiveGuardError("workload environment configuration is invalid")
    references = [
        entry.get("secretRef")
        for entry in environment
        if isinstance(entry, dict) and entry.get("name") == "DATABASE_URL"
    ]
    if len(references) != 1 or not isinstance(references[0], str):
        raise LiveGuardError("workload DATABASE_URL reference is invalid")
    if not isinstance(secrets, list):
        raise LiveGuardError("workload secrets are invalid")
    names = {
        entry["name"]
        for entry in secrets
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    }
    if len(names) != len(secrets):
        raise LiveGuardError("workload contains invalid or duplicate secrets")
    return references[0], names


def verify_live_state(
    query: dict[str, str],
    *,
    runner: Any = _run_az,
) -> list[dict[str, str]]:
    validate_query(query)
    selected = query["selected_secret"]
    require_v2 = selected == "database-url-v2"
    states: list[dict[str, str]] = []
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
        except json.JSONDecodeError as error:
            raise LiveGuardError("Azure returned invalid workload JSON") from error
        if not isinstance(resource, dict):
            raise LiveGuardError("Azure returned unexpected workload JSON")
        reference, names = database_state(resource)
        if reference != selected or "database-url" not in names:
            raise LiveGuardError("live DATABASE_URL selection is not adoption-only")
        if require_v2 and "database-url-v2" not in names:
            raise LiveGuardError("live v2 secret is missing")
        states.append({"secret": reference, "workload": workload.label})
    return states


def main() -> int:
    try:
        query = read_query(sys.stdin)
        states = verify_live_state(query)
    except LiveGuardError:
        print(
            json.dumps({"code": "database_url_live_guard_failed", "result": "failed"}),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "result": "verified",
                "selected_secret": query["selected_secret"],
                "workload_count": str(len(states)),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
