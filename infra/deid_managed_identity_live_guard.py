#!/usr/bin/env python3
"""Fail closed before the production worker adopts its DeID identity.

The guard is read-only and emits only status/count metadata. It deliberately
does not print Azure resource, client, principal, or subscription identifiers.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any, Callable

SUBSCRIPTION_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
RESOURCE_NAME_RE = re.compile(r"^[A-Za-z0-9._()\-]{1,90}$")
UUID_RE = SUBSCRIPTION_RE
DEID_RESOURCE_TYPE = "Microsoft.HealthDataAIServices/deidServices"
DEID_ROLE = "DeID Realtime Data User"
MANAGED_IDENTITY_CLIENT_ID_ENV = "AZURE_DEID_MANAGED_IDENTITY_CLIENT_ID"
AUTH_MODE_ENV = "AZURE_DEID_AUTH_MODE"


class LiveGuardError(RuntimeError):
    """A sanitized live-state guard failure."""


Runner = Callable[[tuple[str, ...]], str]


def _run_az(arguments: tuple[str, ...]) -> str:
    environment = os.environ.copy()
    for name in (
        "AZURE_DEID_CLIENT_ID",
        "AZURE_DEID_CLIENT_SECRET",
        "AZURE_DEID_TENANT_ID",
        "OP_SERVICE_ACCOUNT_TOKEN",
    ):
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
        query = json.load(stream)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise LiveGuardError("guard input is invalid JSON") from error
    expected_fields = {
        "enabled",
        "identity_name",
        "resource_group",
        "service_name",
        "subscription_id",
        "worker_name",
    }
    if not isinstance(query, dict) or set(query) != expected_fields:
        raise LiveGuardError("guard input fields are invalid")
    if not all(isinstance(value, str) for value in query.values()):
        raise LiveGuardError("guard input values must be strings")
    return query


def validate_query(query: dict[str, str]) -> None:
    if query["enabled"] not in {"true", "false"}:
        raise LiveGuardError("enabled flag is invalid")
    if not SUBSCRIPTION_RE.fullmatch(query["subscription_id"].lower()):
        raise LiveGuardError("subscription ID is invalid")
    for field in ("identity_name", "resource_group", "service_name", "worker_name"):
        if not RESOURCE_NAME_RE.fullmatch(query[field]):
            raise LiveGuardError("Azure resource name is invalid")


def _load_object(raw: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise LiveGuardError(f"Azure returned invalid {label} JSON") from error
    if not isinstance(value, dict):
        raise LiveGuardError(f"Azure returned unexpected {label} JSON")
    return value


def _load_list(raw: str, label: str) -> list[Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise LiveGuardError(f"Azure returned invalid {label} JSON") from error
    if not isinstance(value, list):
        raise LiveGuardError(f"Azure returned unexpected {label} JSON")
    return value


def _same_resource_id(actual: object, expected: str) -> bool:
    return (
        isinstance(actual, str)
        and actual.rstrip("/").lower() == expected.rstrip("/").lower()
    )


def _required_uuid(resource: dict[str, Any], field: str) -> str:
    value = resource.get(field)
    if not isinstance(value, str) or not UUID_RE.fullmatch(value.lower()):
        raise LiveGuardError("Azure identity metadata is invalid")
    return value


def _worker_environment(worker: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        containers = worker["properties"]["template"]["containers"]
    except (KeyError, TypeError) as error:
        raise LiveGuardError("worker configuration is incomplete") from error
    if not isinstance(containers, list) or len(containers) != 1:
        raise LiveGuardError("worker must have exactly one container")
    container = containers[0]
    if not isinstance(container, dict):
        raise LiveGuardError("worker container configuration is invalid")
    environment = container.get("env", [])
    if not isinstance(environment, list) or not all(
        isinstance(entry, dict) for entry in environment
    ):
        raise LiveGuardError("worker environment is invalid")
    return environment


def _one_plain_value(environment: list[dict[str, Any]], name: str) -> str:
    matches = [entry for entry in environment if entry.get("name") == name]
    if (
        len(matches) != 1
        or not isinstance(matches[0].get("value"), str)
        or matches[0].get("secretRef") not in (None, "")
    ):
        raise LiveGuardError("worker identity environment is invalid")
    return matches[0]["value"]


def verify_live_state(
    query: dict[str, str],
    *,
    runner: Runner | None = None,
) -> dict[str, str]:
    validate_query(query)
    if query["enabled"] == "false":
        return {"check_count": "0", "result": "disabled"}
    if runner is None:
        runner = _run_az

    subscription_id = query["subscription_id"]
    resource_group = query["resource_group"]
    identity_resource_id = (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/"
        f"{query['identity_name']}"
    )
    deid_resource_id = (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        f"/providers/{DEID_RESOURCE_TYPE}/{query['service_name']}"
    )

    identity = _load_object(
        runner(
            (
                "identity",
                "show",
                "--name",
                query["identity_name"],
                "--resource-group",
                resource_group,
                "--subscription",
                subscription_id,
                "--output",
                "json",
            )
        ),
        "managed identity",
    )
    if not _same_resource_id(identity.get("id"), identity_resource_id):
        raise LiveGuardError("managed identity resource ID is unexpected")
    client_id = _required_uuid(identity, "clientId")
    principal_id = _required_uuid(identity, "principalId")

    service = _load_object(
        runner(
            (
                "resource",
                "show",
                "--ids",
                deid_resource_id,
                "--subscription",
                subscription_id,
                "--output",
                "json",
            )
        ),
        "DeID resource",
    )
    if (
        not _same_resource_id(service.get("id"), deid_resource_id)
        or service.get("name") != query["service_name"]
        or str(service.get("type", "")).lower() != DEID_RESOURCE_TYPE.lower()
    ):
        raise LiveGuardError("DeID resource identity is unexpected")

    worker = _load_object(
        runner(
            (
                "containerapp",
                "show",
                "--name",
                query["worker_name"],
                "--resource-group",
                resource_group,
                "--subscription",
                subscription_id,
                "--output",
                "json",
            )
        ),
        "worker",
    )
    worker_identity = worker.get("identity")
    if not isinstance(worker_identity, dict):
        raise LiveGuardError("worker identity configuration is invalid")
    assigned_identities = worker_identity.get("userAssignedIdentities", {})
    if not isinstance(assigned_identities, dict) or not any(
        _same_resource_id(candidate, identity_resource_id)
        for candidate in assigned_identities
    ):
        raise LiveGuardError("worker is missing the exact managed identity")
    environment = _worker_environment(worker)
    if _one_plain_value(environment, MANAGED_IDENTITY_CLIENT_ID_ENV) != client_id:
        raise LiveGuardError("worker managed identity client ID is unexpected")
    if _one_plain_value(environment, AUTH_MODE_ENV) not in {
        "client_secret",
        "managed_identity",
    }:
        raise LiveGuardError("worker DeID auth mode is invalid")

    assignments = _load_list(
        runner(
            (
                "role",
                "assignment",
                "list",
                "--assignee-object-id",
                principal_id,
                "--all",
                "--subscription",
                subscription_id,
                "--output",
                "json",
            )
        ),
        "role assignment",
    )
    exact_assignments = [
        assignment
        for assignment in assignments
        if isinstance(assignment, dict)
        and str(assignment.get("principalId", "")).lower() == principal_id.lower()
        and assignment.get("roleDefinitionName") == DEID_ROLE
        and _same_resource_id(assignment.get("scope"), deid_resource_id)
    ]
    if len(assignments) != 1 or len(exact_assignments) != 1:
        raise LiveGuardError(
            "managed identity role assignments exceed exact DeID access"
        )

    return {"check_count": "5", "result": "verified"}


def main() -> int:
    try:
        result = verify_live_state(read_query(sys.stdin))
    except LiveGuardError:
        print(
            json.dumps({"code": "deid_managed_identity_live_guard_failed"}),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
