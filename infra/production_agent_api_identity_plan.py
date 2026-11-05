#!/usr/bin/env python3
"""Plan or verify the production provider Agent API assignment offline.

The command consumes one operator-prepared, read-only Azure/Graph snapshot. It
has no network client and no mutation path. Its stdout is deliberately limited
to fixed state labels and SHA-256 digests; production object identifiers stay
inside the mode-0600 input file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn
from uuid import UUID

SCHEMA_VERSION = 1
MAX_INPUT_BYTES = 262_144
MAX_SNAPSHOT_AGE = timedelta(minutes=30)
MAX_FUTURE_SKEW = timedelta(minutes=1)

EXPECTED_SUBSCRIPTION_ID = "bab2a709-1fcb-4c87-a9fc-802f7477ec13"
EXPECTED_CORE_RESOURCE_GROUP = "cosmaneura-prod"
EXPECTED_CORE_API_CONTAINER_APP = "cosmaneura-prod-app"
EXPECTED_SOVEREIGN_RESOURCE_GROUP = "cosmaneura-sovereign-prod"
EXPECTED_PROVIDER_CONTAINER_APP = "cosmaneura-sovereign-prod-app"
EXPECTED_PUBLIC_CONTAINER_APP = "cosmaneura-web-prod-app"

AGENT_ROLE_VALUE = "AgentApi.Access"
AGENT_ROLE_DISPLAY_NAME = "Agent API access"
AGENT_ROLE_DESCRIPTION = (
    "Allows the provider Sovereign workload to call the governed Agent API."
)
SCRIBE_ROLE_VALUE = "Scribe.Transcript.Write"

SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,126}[a-z0-9])?$")
DIGEST_PATTERN = re.compile(r"^sha256_[0-9a-f]{64}$")


class IdentityPlanRefused(ValueError):
    """A value-free refusal; CLI output never includes the private detail."""


@dataclass(frozen=True, slots=True)
class SnapshotState:
    observed_at: datetime
    application: str
    resource_service_principal: str
    provider_identity: str
    public_identity: str
    provider_service_principal: str
    public_service_principal: str
    provider_container_app: str
    public_container_app: str
    resource_assignments: tuple[dict[str, str], ...]
    provider_outgoing_assignments: tuple[dict[str, str], ...]
    public_outgoing_assignments: tuple[dict[str, str], ...]
    provider_target_count: int


def _fail(message: str) -> NoReturn:
    raise IdentityPlanRefused(message)


def _mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{field} must be an object")
    return value


def _exact_keys(value: Mapping[str, Any], keys: set[str], *, field: str) -> None:
    if set(value) != keys:
        _fail(f"{field} has an unexpected shape")


def _text(value: Any, *, field: str, limit: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > limit
        or any(ord(character) < 32 for character in value)
    ):
        _fail(f"{field} is invalid")
    return value


def _uuid(value: Any, *, field: str) -> str:
    text = _text(value, field=field, limit=36)
    try:
        parsed = UUID(text)
    except (ValueError, AttributeError):
        _fail(f"{field} is invalid")
    if parsed.int == 0 or str(parsed) != text:
        _fail(f"{field} is invalid")
    return text


def _timestamp(value: Any, *, field: str) -> datetime:
    text = _text(value, field=field, limit=32)
    if not text.endswith("Z"):
        _fail(f"{field} is invalid")
    try:
        parsed = datetime.fromisoformat(text.removesuffix("Z") + "+00:00")
    except ValueError:
        _fail(f"{field} is invalid")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(f"{field} is invalid")
    canonical = (
        parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )
    if canonical != text:
        _fail(f"{field} is not canonical")
    return parsed.astimezone(UTC)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: Any) -> str:
    return f"sha256_{hashlib.sha256(_canonical(value).encode()).hexdigest()}"


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            _fail("input file contains duplicate object keys")
        value[key] = item
    return value


def _authority(raw: Any, *, tenant_id: str) -> dict[str, str]:
    value = _mapping(raw, field="authority")
    expected = {
        "tenant_id": tenant_id,
        "subscription_id": EXPECTED_SUBSCRIPTION_ID,
        "core_resource_group": EXPECTED_CORE_RESOURCE_GROUP,
        "core_api_container_app": EXPECTED_CORE_API_CONTAINER_APP,
        "sovereign_resource_group": EXPECTED_SOVEREIGN_RESOURCE_GROUP,
        "provider_container_app": EXPECTED_PROVIDER_CONTAINER_APP,
        "public_container_app": EXPECTED_PUBLIC_CONTAINER_APP,
    }
    if value != expected:
        _fail("production authority or resources do not match")
    return expected


def _identity_binding(raw: Any, *, field: str) -> dict[str, str]:
    value = _mapping(raw, field=field)
    _exact_keys(
        value,
        {"name", "resource_id", "client_id", "principal_id"},
        field=field,
    )
    name = _text(value["name"], field=f"{field}.name", limit=128)
    if not NAME_PATTERN.fullmatch(name):
        _fail(f"{field}.name is invalid")
    resource_id = _text(value["resource_id"], field=f"{field}.resource_id", limit=512)
    expected_resource_id = (
        f"/subscriptions/{EXPECTED_SUBSCRIPTION_ID}"
        f"/resourceGroups/{EXPECTED_SOVEREIGN_RESOURCE_GROUP}"
        "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/"
        f"{name}"
    )
    if resource_id != expected_resource_id:
        _fail(f"{field}.resource_id is outside the production boundary")
    return {
        "name": name,
        "resource_id": resource_id,
        "client_id": _uuid(value["client_id"], field=f"{field}.client_id"),
        "principal_id": _uuid(value["principal_id"], field=f"{field}.principal_id"),
    }


def _bindings(raw: Any) -> dict[str, Any]:
    value = _mapping(raw, field="bindings")
    _exact_keys(
        value,
        {
            "entra_tenant_id",
            "agent_api_application_object_id",
            "agent_api_resource_service_principal_id",
            "agent_api_audience_client_id",
            "agent_api_access_role_id",
            "scribe_transcript_write_role_id",
            "provider_identity",
            "public_identity",
        },
        field="bindings",
    )
    bindings: dict[str, Any] = {
        key: _uuid(value[key], field=f"bindings.{key}")
        for key in (
            "entra_tenant_id",
            "agent_api_application_object_id",
            "agent_api_resource_service_principal_id",
            "agent_api_audience_client_id",
            "agent_api_access_role_id",
            "scribe_transcript_write_role_id",
        )
    }
    bindings["provider_identity"] = _identity_binding(
        value["provider_identity"], field="bindings.provider_identity"
    )
    bindings["public_identity"] = _identity_binding(
        value["public_identity"], field="bindings.public_identity"
    )
    provider = bindings["provider_identity"]
    public = bindings["public_identity"]
    if (
        provider["name"] == public["name"]
        or provider["resource_id"] == public["resource_id"]
        or provider["client_id"] == public["client_id"]
        or provider["principal_id"] == public["principal_id"]
    ):
        _fail("provider and public identities are not isolated")
    unique_ids = {
        bindings["agent_api_application_object_id"],
        bindings["agent_api_resource_service_principal_id"],
        bindings["agent_api_audience_client_id"],
        bindings["agent_api_access_role_id"],
        bindings["scribe_transcript_write_role_id"],
        provider["client_id"],
        provider["principal_id"],
        public["client_id"],
        public["principal_id"],
    }
    # The tenant is an authority boundary rather than an application identity,
    # so it is validated separately and may not be inferred from staging.
    if len(unique_ids) != 9:
        _fail("production identity bindings are ambiguous")
    return bindings


def _app_role(raw: Any, *, field: str) -> dict[str, Any]:
    value = _mapping(raw, field=field)
    _exact_keys(
        value,
        {
            "id",
            "value",
            "display_name",
            "description",
            "is_enabled",
            "allowed_member_types",
        },
        field=field,
    )
    allowed = value["allowed_member_types"]
    if (
        not isinstance(allowed, list)
        or not allowed
        or len(allowed) != len(set(allowed))
        or any(item not in {"Application", "User"} for item in allowed)
    ):
        _fail(f"{field}.allowed_member_types is invalid")
    if not isinstance(value["is_enabled"], bool):
        _fail(f"{field}.is_enabled is invalid")
    return {
        "id": _uuid(value["id"], field=f"{field}.id"),
        "value": _text(value["value"], field=f"{field}.value", limit=128),
        "display_name": _text(
            value["display_name"], field=f"{field}.display_name", limit=256
        ),
        "description": _text(
            value["description"], field=f"{field}.description", limit=1024
        ),
        "is_enabled": value["is_enabled"],
        "allowed_member_types": sorted(allowed),
    }


def _roles(raw: Any, *, field: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw or len(raw) > 256:
        _fail(f"{field} is invalid")
    roles = [
        _app_role(item, field=f"{field}[{index}]") for index, item in enumerate(raw)
    ]
    if len({role["id"] for role in roles}) != len(roles):
        _fail(f"{field} contains duplicate IDs")
    if len({role["value"] for role in roles}) != len(roles):
        _fail(f"{field} contains duplicate values")
    return sorted(roles, key=lambda role: role["id"])


def _role(
    roles: Sequence[dict[str, Any]],
    *,
    role_id: str,
    role_value: str,
    field: str,
) -> dict[str, Any]:
    matches = [
        role for role in roles if role["id"] == role_id or role["value"] == role_value
    ]
    if len(matches) != 1:
        _fail(f"{field} is absent or ambiguous")
    role = matches[0]
    if role["id"] != role_id or role["value"] != role_value:
        _fail(f"{field} binding conflicts")
    return role


def _application(
    raw: Any,
    *,
    object_id: str,
    app_id: str,
    field: str,
) -> tuple[str, list[dict[str, Any]]]:
    value = _mapping(raw, field=field)
    _exact_keys(
        value,
        {"object_id", "app_id", "app_roles", "app_roles_collection_complete"},
        field=field,
    )
    if value["app_roles_collection_complete"] is not True:
        _fail(f"{field}.app_roles is not a complete collection")
    if (
        _uuid(value["object_id"], field=f"{field}.object_id") != object_id
        or _uuid(value["app_id"], field=f"{field}.app_id") != app_id
    ):
        _fail(f"{field} identity mismatch")
    roles = _roles(value["app_roles"], field=f"{field}.app_roles")
    return (
        _canonical(
            {
                "object_id": object_id,
                "app_id": app_id,
                "app_roles_collection_complete": True,
                "app_roles": roles,
            }
        ),
        roles,
    )


def _observed_identity(raw: Any, *, expected: Mapping[str, str], field: str) -> str:
    value = _identity_binding(raw, field=field)
    if value != expected:
        _fail(f"{field} does not match the reviewed binding")
    return _canonical(value)


def _service_principal(
    raw: Any,
    *,
    expected_object_id: str,
    expected_app_id: str,
    field: str,
) -> str:
    value = _mapping(raw, field=field)
    _exact_keys(value, {"object_id", "app_id", "service_principal_type"}, field=field)
    normalized = {
        "object_id": _uuid(value["object_id"], field=f"{field}.object_id"),
        "app_id": _uuid(value["app_id"], field=f"{field}.app_id"),
        "service_principal_type": _text(
            value["service_principal_type"],
            field=f"{field}.service_principal_type",
            limit=64,
        ),
    }
    if (
        normalized["object_id"] != expected_object_id
        or normalized["app_id"] != expected_app_id
        or normalized["service_principal_type"] != "ManagedIdentity"
    ):
        _fail(f"{field} identity mismatch")
    return _canonical(normalized)


def _container_app(
    raw: Any,
    *,
    name: str,
    identity_resource_id: str,
    field: str,
) -> str:
    value = _mapping(raw, field=field)
    _exact_keys(
        value,
        {"resource_group", "name", "user_assigned_identity_resource_ids"},
        field=field,
    )
    identities = value["user_assigned_identity_resource_ids"]
    if (
        value["resource_group"] != EXPECTED_SOVEREIGN_RESOURCE_GROUP
        or value["name"] != name
        or identities != [identity_resource_id]
    ):
        _fail(f"{field} runtime identity mismatch")
    return _canonical(value)


def _public_container_app(
    raw: Any,
    *,
    identity_resource_id: str,
    field: str,
) -> str:
    value = _mapping(raw, field=field)
    _exact_keys(
        value,
        {
            "exists",
            "resource_group",
            "name",
            "user_assigned_identity_resource_ids",
            "configuration_present",
        },
        field=field,
    )
    if (
        type(value["exists"]) is not bool
        or type(value["configuration_present"]) is not bool
        or value["resource_group"] != EXPECTED_SOVEREIGN_RESOURCE_GROUP
        or value["name"] != EXPECTED_PUBLIC_CONTAINER_APP
    ):
        _fail(f"{field} runtime evidence mismatch")
    if value["exists"]:
        if (
            value["user_assigned_identity_resource_ids"] != [identity_resource_id]
            or value["configuration_present"] is not True
        ):
            _fail(f"{field} present runtime binding mismatch")
    elif (
        value["user_assigned_identity_resource_ids"] != []
        or value["configuration_present"] is not False
    ):
        _fail(f"{field} absent runtime contains binding or configuration")
    return _canonical(value)


def _assignments(
    raw: Any,
    *,
    field: str,
    expected_resource_id: str | None = None,
    expected_principal_id: str | None = None,
) -> tuple[dict[str, str], ...]:
    value = _mapping(raw, field=field)
    _exact_keys(value, {"collection_complete", "items"}, field=field)
    if value["collection_complete"] is not True:
        _fail(f"{field} is not a complete collection")
    items = value["items"]
    if not isinstance(items, list) or len(items) > 2048:
        _fail(f"{field}.items is invalid")
    normalized: list[dict[str, str]] = []
    for index, item in enumerate(items):
        assignment = _mapping(item, field=f"{field}.items[{index}]")
        _exact_keys(
            assignment,
            {"id", "principal_id", "resource_id", "app_role_id"},
            field=f"{field}.items[{index}]",
        )
        row = {
            key: _uuid(assignment[key], field=f"{field}.items[{index}].{key}")
            for key in ("id", "principal_id", "resource_id", "app_role_id")
        }
        if (
            expected_resource_id is not None
            and row["resource_id"] != expected_resource_id
        ):
            _fail(f"{field} contains an assignment for another resource")
        if (
            expected_principal_id is not None
            and row["principal_id"] != expected_principal_id
        ):
            _fail(f"{field} contains an assignment for another principal")
        normalized.append(row)
    if len({row["id"] for row in normalized}) != len(normalized):
        _fail(f"{field} contains duplicate assignment IDs")
    triples = {
        (row["principal_id"], row["resource_id"], row["app_role_id"])
        for row in normalized
    }
    if len(triples) != len(normalized):
        _fail(f"{field} contains duplicate assignments")
    return tuple(sorted(normalized, key=lambda row: row["id"]))


def _snapshot(
    raw: Any,
    *,
    bindings: Mapping[str, Any],
    field: str,
    now: datetime,
) -> SnapshotState:
    value = _mapping(raw, field=field)
    _exact_keys(
        value,
        {
            "observed_at",
            "application",
            "resource_service_principal",
            "provider_identity",
            "public_identity",
            "provider_service_principal",
            "public_service_principal",
            "provider_container_app",
            "public_container_app",
            "agent_api_resource_assignments",
            "provider_outgoing_assignments",
            "public_outgoing_assignments",
        },
        field=field,
    )
    observed_at = _timestamp(value["observed_at"], field=f"{field}.observed_at")
    if observed_at < now - MAX_SNAPSHOT_AGE or observed_at > now + MAX_FUTURE_SKEW:
        _fail(f"{field}.observed_at is outside the freshness window")

    application, application_roles = _application(
        value["application"],
        object_id=bindings["agent_api_application_object_id"],
        app_id=bindings["agent_api_audience_client_id"],
        field=f"{field}.application",
    )
    resource_sp, resource_roles = _application(
        value["resource_service_principal"],
        object_id=bindings["agent_api_resource_service_principal_id"],
        app_id=bindings["agent_api_audience_client_id"],
        field=f"{field}.resource_service_principal",
    )
    expected_agent_role = {
        "id": bindings["agent_api_access_role_id"],
        "value": AGENT_ROLE_VALUE,
        "display_name": AGENT_ROLE_DISPLAY_NAME,
        "description": AGENT_ROLE_DESCRIPTION,
        "is_enabled": True,
        "allowed_member_types": ["Application"],
    }
    app_agent_role = _role(
        application_roles,
        role_id=bindings["agent_api_access_role_id"],
        role_value=AGENT_ROLE_VALUE,
        field=f"{field}.application.AgentApi.Access",
    )
    resource_agent_role = _role(
        resource_roles,
        role_id=bindings["agent_api_access_role_id"],
        role_value=AGENT_ROLE_VALUE,
        field=f"{field}.resource_service_principal.AgentApi.Access",
    )
    if (
        app_agent_role != expected_agent_role
        or resource_agent_role != expected_agent_role
    ):
        _fail(f"{field} AgentApi.Access definition mismatch")
    app_scribe_role = _role(
        application_roles,
        role_id=bindings["scribe_transcript_write_role_id"],
        role_value=SCRIBE_ROLE_VALUE,
        field=f"{field}.application.Scribe.Transcript.Write",
    )
    resource_scribe_role = _role(
        resource_roles,
        role_id=bindings["scribe_transcript_write_role_id"],
        role_value=SCRIBE_ROLE_VALUE,
        field=f"{field}.resource_service_principal.Scribe.Transcript.Write",
    )
    if (
        app_scribe_role != resource_scribe_role
        or app_scribe_role["is_enabled"] is not True
        or app_scribe_role["allowed_member_types"] != ["Application"]
    ):
        _fail(f"{field} Scribe.Transcript.Write definition mismatch")

    provider_binding = bindings["provider_identity"]
    public_binding = bindings["public_identity"]
    provider_identity = _observed_identity(
        value["provider_identity"],
        expected=provider_binding,
        field=f"{field}.provider_identity",
    )
    public_identity = _observed_identity(
        value["public_identity"],
        expected=public_binding,
        field=f"{field}.public_identity",
    )
    provider_sp = _service_principal(
        value["provider_service_principal"],
        expected_object_id=provider_binding["principal_id"],
        expected_app_id=provider_binding["client_id"],
        field=f"{field}.provider_service_principal",
    )
    public_sp = _service_principal(
        value["public_service_principal"],
        expected_object_id=public_binding["principal_id"],
        expected_app_id=public_binding["client_id"],
        field=f"{field}.public_service_principal",
    )
    provider_app = _container_app(
        value["provider_container_app"],
        name=EXPECTED_PROVIDER_CONTAINER_APP,
        identity_resource_id=provider_binding["resource_id"],
        field=f"{field}.provider_container_app",
    )
    public_app = _public_container_app(
        value["public_container_app"],
        identity_resource_id=public_binding["resource_id"],
        field=f"{field}.public_container_app",
    )
    assignments = _assignments(
        value["agent_api_resource_assignments"],
        field=f"{field}.agent_api_resource_assignments",
        expected_resource_id=bindings["agent_api_resource_service_principal_id"],
    )
    provider_outgoing = _assignments(
        value["provider_outgoing_assignments"],
        field=f"{field}.provider_outgoing_assignments",
        expected_principal_id=provider_binding["principal_id"],
    )
    public_outgoing = _assignments(
        value["public_outgoing_assignments"],
        field=f"{field}.public_outgoing_assignments",
        expected_principal_id=public_binding["principal_id"],
    )
    provider_targets = [
        row
        for row in assignments
        if row["principal_id"] == provider_binding["principal_id"]
        and row["app_role_id"] == bindings["agent_api_access_role_id"]
    ]
    public_targets = [
        row
        for row in assignments
        if row["principal_id"] == public_binding["principal_id"]
        and row["app_role_id"] == bindings["agent_api_access_role_id"]
    ]
    provider_outgoing_targets = [
        row
        for row in provider_outgoing
        if row["resource_id"] == bindings["agent_api_resource_service_principal_id"]
        and row["app_role_id"] == bindings["agent_api_access_role_id"]
    ]
    public_outgoing_targets = [
        row
        for row in public_outgoing
        if row["resource_id"] == bindings["agent_api_resource_service_principal_id"]
        and row["app_role_id"] == bindings["agent_api_access_role_id"]
    ]
    if len(provider_targets) > 1:
        _fail(f"{field} has duplicate provider AgentApi.Access assignments")
    if tuple(provider_targets) != tuple(provider_outgoing_targets):
        _fail(f"{field} provider assignment views do not match")
    if tuple(public_targets) != tuple(public_outgoing_targets):
        _fail(f"{field} public assignment views do not match")
    if public_targets or public_outgoing_targets:
        _fail(f"{field} gives the public identity AgentApi.Access")
    return SnapshotState(
        observed_at=observed_at,
        application=application,
        resource_service_principal=resource_sp,
        provider_identity=provider_identity,
        public_identity=public_identity,
        provider_service_principal=provider_sp,
        public_service_principal=public_sp,
        provider_container_app=provider_app,
        public_container_app=public_app,
        resource_assignments=assignments,
        provider_outgoing_assignments=provider_outgoing,
        public_outgoing_assignments=public_outgoing,
        provider_target_count=len(provider_targets),
    )


def _core_rollout(raw: Any, *, bindings: Mapping[str, Any]) -> dict[str, str]:
    value = _mapping(raw, field="core_rollout")
    _exact_keys(
        value,
        {"agent_api_audience", "sovereign_client_id"},
        field="core_rollout",
    )
    normalized = {
        "agent_api_audience": _uuid(
            value["agent_api_audience"], field="core_rollout.agent_api_audience"
        ),
        "sovereign_client_id": _uuid(
            value["sovereign_client_id"], field="core_rollout.sovereign_client_id"
        ),
    }
    if (
        normalized["agent_api_audience"] != bindings["agent_api_audience_client_id"]
        or normalized["sovereign_client_id"]
        != bindings["provider_identity"]["client_id"]
    ):
        _fail("Core rollout bindings do not match the reviewed identities")
    return normalized


def _snapshot_evidence(snapshot: SnapshotState) -> dict[str, Any]:
    return {
        "observed_at": snapshot.observed_at.isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        ),
        "application": snapshot.application,
        "resource_service_principal": snapshot.resource_service_principal,
        "provider_identity": snapshot.provider_identity,
        "public_identity": snapshot.public_identity,
        "provider_service_principal": snapshot.provider_service_principal,
        "public_service_principal": snapshot.public_service_principal,
        "provider_container_app": snapshot.provider_container_app,
        "public_container_app": snapshot.public_container_app,
        "resource_assignments": snapshot.resource_assignments,
        "provider_outgoing_assignments": snapshot.provider_outgoing_assignments,
        "public_outgoing_assignments": snapshot.public_outgoing_assignments,
    }


def _plan_sha256(
    *,
    candidate_source_sha: str,
    authority: Mapping[str, str],
    bindings: Mapping[str, Any],
    core_rollout: Mapping[str, str],
    before: SnapshotState,
) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "candidate_source_sha": candidate_source_sha,
        "authority": authority,
        "operation": (
            "create_exact_provider_agent_api_assignment"
            if before.provider_target_count == 0
            else "no_change"
        ),
        "principal_id": bindings["provider_identity"]["principal_id"],
        "resource_id": bindings["agent_api_resource_service_principal_id"],
        "app_role_id": bindings["agent_api_access_role_id"],
        "core_rollout": core_rollout,
        "before_sha256": _digest(_snapshot_evidence(before)),
    }
    return _digest(payload)


def _without_target(
    rows: Sequence[dict[str, str]],
    *,
    principal_id: str,
    bindings: Mapping[str, Any],
) -> tuple[dict[str, str], ...]:
    return tuple(
        row
        for row in rows
        if not (
            row["principal_id"] == principal_id
            and row["resource_id"]
            == bindings["agent_api_resource_service_principal_id"]
            and row["app_role_id"] == bindings["agent_api_access_role_id"]
        )
    )


def _verify_transition(
    before: SnapshotState,
    after: SnapshotState,
    *,
    bindings: Mapping[str, Any],
) -> None:
    if after.observed_at <= before.observed_at:
        _fail("verification snapshot is not newer than the plan snapshot")
    immutable_fields = (
        "application",
        "resource_service_principal",
        "provider_identity",
        "public_identity",
        "provider_service_principal",
        "public_service_principal",
        "provider_container_app",
        "public_container_app",
    )
    if any(getattr(before, name) != getattr(after, name) for name in immutable_fields):
        _fail("identity or role state changed outside the assignment")
    if after.provider_target_count != 1:
        _fail("provider AgentApi.Access assignment did not verify")
    provider_id = bindings["provider_identity"]["principal_id"]
    if _without_target(
        before.resource_assignments,
        principal_id=provider_id,
        bindings=bindings,
    ) != _without_target(
        after.resource_assignments,
        principal_id=provider_id,
        bindings=bindings,
    ):
        _fail("an unrelated Agent API assignment changed")
    if _without_target(
        before.provider_outgoing_assignments,
        principal_id=provider_id,
        bindings=bindings,
    ) != _without_target(
        after.provider_outgoing_assignments,
        principal_id=provider_id,
        bindings=bindings,
    ):
        _fail("an unrelated provider assignment changed")
    if before.public_outgoing_assignments != after.public_outgoing_assignments:
        _fail("a public identity assignment changed")
    if before.provider_target_count == 1 and (
        before.resource_assignments != after.resource_assignments
        or before.provider_outgoing_assignments != after.provider_outgoing_assignments
    ):
        _fail("an already-complete assignment plan changed state")


def evaluate(document: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Validate one private snapshot and return identifier-free evidence."""

    value = _mapping(document, field="document")
    _exact_keys(
        value,
        {
            "schema_version",
            "environment",
            "mode",
            "candidate_source_sha",
            "authority",
            "bindings",
            "core_rollout",
            "before",
            "after",
            "expected_plan_sha256",
        },
        field="document",
    )
    if (
        value["schema_version"] != SCHEMA_VERSION
        or type(value["schema_version"]) is not int
    ):
        _fail("schema_version is invalid")
    if value["environment"] != "production":
        _fail("environment is invalid")
    mode = value["mode"]
    if mode not in {"plan", "verify"}:
        _fail("mode is invalid")
    candidate_source_sha = _text(
        value["candidate_source_sha"], field="candidate_source_sha", limit=40
    )
    if (
        not SHA_PATTERN.fullmatch(candidate_source_sha)
        or candidate_source_sha == "0" * 40
    ):
        _fail("candidate_source_sha is invalid")
    bindings = _bindings(value["bindings"])
    authority = _authority(value["authority"], tenant_id=bindings["entra_tenant_id"])
    core_rollout = _core_rollout(value["core_rollout"], bindings=bindings)
    current = (now or datetime.now(UTC)).astimezone(UTC)
    before = _snapshot(value["before"], bindings=bindings, field="before", now=current)
    plan_sha256 = _plan_sha256(
        candidate_source_sha=candidate_source_sha,
        authority=authority,
        bindings=bindings,
        core_rollout=core_rollout,
        before=before,
    )
    after: SnapshotState | None = None

    if mode == "plan":
        if value["after"] is not None or value["expected_plan_sha256"] is not None:
            _fail("plan input cannot contain verification state")
        action = "create_required" if before.provider_target_count == 0 else "no_change"
    else:
        expected_plan = value["expected_plan_sha256"]
        if not isinstance(expected_plan, str) or not DIGEST_PATTERN.fullmatch(
            expected_plan
        ):
            _fail("expected_plan_sha256 is invalid")
        if expected_plan != plan_sha256:
            _fail("verification does not match the reviewed plan")
        if value["after"] is None:
            _fail("verification snapshot is required")
        after = _snapshot(value["after"], bindings=bindings, field="after", now=current)
        _verify_transition(before, after, bindings=bindings)
        action = "verified"

    return {
        "agent_role": "verified",
        "assignment_action": action,
        "environment": "production",
        "evidence_scope": "operator_supplied_read_only_snapshot",
        "evidence_sha256": _digest(
            {
                "mode": mode,
                "plan_sha256": plan_sha256,
                "after": _snapshot_evidence(after) if after is not None else None,
            }
        ),
        "mode": mode,
        "plan_sha256": plan_sha256,
        "public_assignment": "excluded",
        "schema_version": SCHEMA_VERSION,
        "scribe_grants": "preserved",
        "status": "ready",
    }


def _load_private_input(path_text: str) -> Any:
    path = Path(path_text)
    try:
        path_metadata = path.lstat()
    except OSError as error:
        raise IdentityPlanRefused("input file is unavailable") from error
    if (
        stat.S_ISLNK(path_metadata.st_mode)
        or not stat.S_ISREG(path_metadata.st_mode)
        or path_metadata.st_size <= 0
        or path_metadata.st_size > MAX_INPUT_BYTES
        or stat.S_IMODE(path_metadata.st_mode) != 0o600
        or path_metadata.st_uid != os.geteuid()
    ):
        _fail("input file boundary is invalid")
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened_metadata = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(opened_metadata.st_mode)
                or stat.S_IMODE(opened_metadata.st_mode) != 0o600
                or opened_metadata.st_uid != os.geteuid()
                or (opened_metadata.st_dev, opened_metadata.st_ino)
                != (path_metadata.st_dev, path_metadata.st_ino)
            ):
                _fail("input file boundary changed")
            payload = stream.read(MAX_INPUT_BYTES + 1)
        if not payload or len(payload) > MAX_INPUT_BYTES:
            _fail("input file boundary is invalid")
        return json.loads(
            payload.decode("utf-8"), object_pairs_hook=_unique_json_object
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IdentityPlanRefused("input file is invalid") from error


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 2 or arguments[0] != "--input":
        print("Production Agent API identity plan refused.", file=sys.stderr)
        return 2
    try:
        document = _load_private_input(arguments[1])
        report = evaluate(document)
    except Exception:
        # Parsing/validation errors can contain object IDs or resource paths.
        print("Production Agent API identity plan refused.", file=sys.stderr)
        return 2
    print(_canonical(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
