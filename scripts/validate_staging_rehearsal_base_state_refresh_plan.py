#!/usr/bin/env python3
"""Create or revalidate the one-time staging base state-refresh plan."""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import types
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from scripts.validate_staging_rehearsal_base_recovery_plan import SourceSnapshot


_RECOVERY_VALIDATOR = (
    Path(__file__)
    .resolve()
    .with_name("validate_staging_rehearsal_base_recovery_plan.py")
)
_RECOVERY_VALIDATOR_SHA256 = (
    "937bbace7c66cb3a6918b3deb8ef6c9def6e691a1a28ab535c84f9dbc02113e9"
)
_MAX_BOOTSTRAP_SOURCE_BYTES = 1024 * 1024


def _read_bootstrap_source(path: Path, expected_sha256: str) -> bytes:
    directory: int | None = None
    descriptor: int | None = None
    try:
        directory = os.open(
            path.parent,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        directory_before = os.fstat(directory)
        if (
            not stat.S_ISDIR(directory_before.st_mode)
            or directory_before.st_uid != os.geteuid()
            or stat.S_IMODE(directory_before.st_mode) & 0o022
        ):
            raise RuntimeError("bootstrap source refused")
        directory_identity = (
            directory_before.st_dev,
            directory_before.st_ino,
            directory_before.st_mode,
            directory_before.st_uid,
            directory_before.st_nlink,
            directory_before.st_mtime_ns,
            directory_before.st_ctime_ns,
        )
        descriptor = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o022
            or not 0 < before.st_size <= _MAX_BOOTSTRAP_SOURCE_BYTES
        ):
            raise RuntimeError("bootstrap source refused")
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_uid,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        after = os.fstat(descriptor)
        directory_after = os.fstat(directory)
        if (
            remaining
            or len(value) != before.st_size
            or identity
            != (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_uid,
                after.st_nlink,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            or directory_identity
            != (
                directory_after.st_dev,
                directory_after.st_ino,
                directory_after.st_mode,
                directory_after.st_uid,
                directory_after.st_nlink,
                directory_after.st_mtime_ns,
                directory_after.st_ctime_ns,
            )
            or hashlib.sha256(value).hexdigest() != expected_sha256
        ):
            raise RuntimeError("bootstrap source refused")
        return value
    except OSError as exc:
        raise RuntimeError("bootstrap source refused") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


try:
    recovery_source = _read_bootstrap_source(
        _RECOVERY_VALIDATOR, _RECOVERY_VALIDATOR_SHA256
    )
    recovery = types.ModuleType("validate_staging_rehearsal_base_recovery_plan")
    recovery.__file__ = str(_RECOVERY_VALIDATOR)
    sys.modules[recovery.__name__] = recovery
    exec(
        compile(
            recovery_source,
            str(_RECOVERY_VALIDATOR),
            "exec",
            dont_inherit=True,
        ),
        recovery.__dict__,
    )
except RuntimeError:
    print("state_refresh_plan_refused: bootstrap source refused", file=sys.stderr)
    raise SystemExit(2) from None


RefreshPlanError = recovery.RecoveryPlanError
VALIDATOR_PATH = Path(__file__).resolve()
PRE_REFRESH_SERIAL = 8
POST_REFRESH_SERIAL = recovery.EXPECTED_RECOVERY_SERIAL
EXPECTED_PRE_REFRESH_STATE_SHA256 = (
    "4a62747650a8961cd48f716adc7f65abbed60997ecc7c00920c40346fec0a98b"
)

POLICY_ADDRESS = (
    "azurerm_resource_group_policy_assignment.deny_postgres_firewall_rules[0]"
)
ROLE_ADDRESSES = frozenset(
    {
        "azurerm_role_definition.control_reader[0]",
        "azurerm_role_definition.marker_reader[0]",
        "azurerm_role_definition.source_reader[0]",
        "azurerm_role_definition.target_cleanup[0]",
        "azurerm_role_definition.target_provisioner[0]",
    }
)
SUBNET_ADDRESSES = frozenset(
    {
        "azurerm_subnet.marker_private_endpoint[0]",
        "azurerm_subnet.private_executor_reserved[0]",
    }
)
VNET_ADDRESS = "azurerm_virtual_network.control[0]"
STORAGE_ADDRESS = "azurerm_storage_account.marker[0]"
EXPECTED_DRIFT_ADDRESSES = (
    frozenset({POLICY_ADDRESS, VNET_ADDRESS, STORAGE_ADDRESS})
    | ROLE_ADDRESSES
    | SUBNET_ADDRESSES
)
STORAGE_SECRET_FIELDS = frozenset(
    {
        "primary_access_key",
        "primary_blob_connection_string",
        "primary_connection_string",
        "secondary_access_key",
        "secondary_blob_connection_string",
        "secondary_connection_string",
    }
)
STORAGE_EMPTY_STRING_FIELDS = frozenset(
    {
        "allowed_copy_scope",
        "edge_zone",
        "primary_blob_internet_endpoint",
        "primary_blob_internet_host",
        "primary_blob_microsoft_endpoint",
        "primary_blob_microsoft_host",
        "primary_dfs_internet_endpoint",
        "primary_dfs_internet_host",
        "primary_dfs_microsoft_endpoint",
        "primary_dfs_microsoft_host",
        "primary_file_internet_endpoint",
        "primary_file_internet_host",
        "primary_file_microsoft_endpoint",
        "primary_file_microsoft_host",
        "primary_queue_microsoft_endpoint",
        "primary_queue_microsoft_host",
        "primary_table_microsoft_endpoint",
        "primary_table_microsoft_host",
        "primary_web_internet_endpoint",
        "primary_web_internet_host",
        "primary_web_microsoft_endpoint",
        "primary_web_microsoft_host",
        "provisioned_billing_model_version",
        "secondary_blob_endpoint",
        "secondary_blob_host",
        "secondary_blob_internet_endpoint",
        "secondary_blob_internet_host",
        "secondary_blob_microsoft_endpoint",
        "secondary_blob_microsoft_host",
        "secondary_dfs_endpoint",
        "secondary_dfs_host",
        "secondary_dfs_internet_endpoint",
        "secondary_dfs_internet_host",
        "secondary_dfs_microsoft_endpoint",
        "secondary_dfs_microsoft_host",
        "secondary_file_endpoint",
        "secondary_file_host",
        "secondary_file_internet_endpoint",
        "secondary_file_internet_host",
        "secondary_file_microsoft_endpoint",
        "secondary_file_microsoft_host",
        "secondary_location",
        "secondary_queue_endpoint",
        "secondary_queue_host",
        "secondary_queue_microsoft_endpoint",
        "secondary_queue_microsoft_host",
        "secondary_table_endpoint",
        "secondary_table_host",
        "secondary_table_microsoft_endpoint",
        "secondary_table_microsoft_host",
        "secondary_web_endpoint",
        "secondary_web_host",
        "secondary_web_internet_endpoint",
        "secondary_web_internet_host",
        "secondary_web_microsoft_endpoint",
        "secondary_web_microsoft_host",
    }
)
ACCOUNT_NAME = "cosmaneurarehearsalctl"
CONTROL_GROUP_ID = (
    f"/subscriptions/{recovery.EXPECTED_SUBSCRIPTION_ID}/resourceGroups/"
    "cosmaneura-staging-rehearsal-control"
)
VNET_ID = f"{CONTROL_GROUP_ID}/providers/Microsoft.Network/virtualNetworks/cosmaneura-staging-rehearsal-control-vnet"
EXPECTED_SUBNETS = [
    {
        "address_prefixes": ["10.76.0.0/27"],
        "default_outbound_access_enabled": False,
        "delegation": [],
        "id": f"{VNET_ID}/subnets/marker-private-endpoints",
        "name": "marker-private-endpoints",
        "private_endpoint_network_policies": "Disabled",
        "private_link_service_network_policies_enabled": False,
        "route_table_id": "",
        "security_group": "",
        "service_endpoint_policy_ids": [],
        "service_endpoints": [],
    },
    {
        "address_prefixes": ["10.76.0.32/27"],
        "default_outbound_access_enabled": False,
        "delegation": [],
        "id": f"{VNET_ID}/subnets/private-executor-reserved",
        "name": "private-executor-reserved",
        "private_endpoint_network_policies": "Enabled",
        "private_link_service_network_policies_enabled": True,
        "route_table_id": "",
        "security_group": "",
        "service_endpoint_policy_ids": [],
        "service_endpoints": [],
    },
]
EXPECTED_OUTPUT_NAMES = frozenset(
    {
        "control_plane_enabled",
        "control_resource_group_id",
        "deny_policy_binding",
        "marker_store_binding",
        "private_executor_blocker",
        "private_executor_network_contract",
        "restore_identity_bindings",
        "restore_role_bindings",
        "state_boundary",
        "target_resource_group_id",
        "target_topology_contract",
    }
)
EXPECTED_PLAN_FIELDS = frozenset(
    {
        "checks",
        "configuration",
        "errored",
        "format_version",
        "output_changes",
        "planned_values",
        "prior_state",
        "relevant_attributes",
        "resource_drift",
        "terraform_version",
        "timestamp",
        "variables",
    }
)
OPTIONAL_EMPTY_PLAN_FIELDS = frozenset(
    {"action_invocations", "deferred_changes", "resource_changes"}
)
EXPECTED_RELEVANT_ATTRIBUTES = frozenset(
    {
        ("azapi_resource.marker_container", ()),
        ("azurerm_management_lock.marker[0]", ("id",)),
        ("azurerm_private_dns_zone.marker_blob[0]", ("id",)),
        ("azurerm_private_dns_zone_virtual_network_link.marker_blob[0]", ("id",)),
        ("azurerm_private_endpoint.marker[0]", ("id",)),
        ('azurerm_role_assignment.marker_create["cleanup_cleanup"]', ("id",)),
        ('azurerm_role_assignment.marker_create["provision_intent"]', ("id",)),
        ('azurerm_role_assignment.marker_create["provision_submission"]', ("id",)),
        ("azurerm_role_assignment.marker_provision_cleanup[0]", ("id",)),
        ('azurerm_role_assignment.marker_read["cleanup_intent"]', ("id",)),
        ('azurerm_role_assignment.marker_read["cleanup_submission"]', ("id",)),
        ("azurerm_storage_account.marker[0]", ("id",)),
        ("azurerm_storage_account.marker[0]", ("primary_blob_host",)),
        ("azurerm_storage_container_immutability_policy.marker", ()),
        (
            'azurerm_storage_container_immutability_policy.marker["restore-cleanups-v4"]',
            ("id",),
        ),
        (
            'azurerm_storage_container_immutability_policy.marker["restore-intents-v4"]',
            ("id",),
        ),
        (
            'azurerm_storage_container_immutability_policy.marker["restore-submissions-v4"]',
            ("id",),
        ),
        ("azurerm_subnet.private_executor_reserved[0]", ("id",)),
    }
)
EXPECTED_MARKER_OUTPUT_TYPE = [
    "object",
    {
        "account_host": "string",
        "container_ids": ["object", {}],
        "evidence_immutability_policy_id": "dynamic",
        "immutability_policy_ids": ["map", "dynamic"],
        "legal_hold_readback_proven": "bool",
        "legal_hold_requested": "bool",
        "locked_worm_requested": "bool",
        "management_lock_id": "dynamic",
        "private_dns_link_id": "string",
        "private_dns_zone_id": "string",
        "private_endpoint_id": "dynamic",
        "private_marker_network_posture_proven": "bool",
        "readonly_lock_requested": "bool",
        "reserved_executor_subnet_id": "string",
        "role_assignment_ids": [
            "object",
            {
                "cleanup_intent_read": "dynamic",
                "cleanup_marker": "dynamic",
                "cleanup_submission_read": "dynamic",
                "provision_cleanup": "dynamic",
                "provision_intent": "dynamic",
                "provision_submission": "dynamic",
            },
        ],
        "storage_account_id": "string",
    },
]


def _source_snapshot(expected_commit: str) -> SourceSnapshot:
    source = recovery._source_snapshot(expected_commit)
    relative = VALIDATOR_PATH.relative_to(recovery.REPOSITORY_ROOT)
    if recovery._run_git(["status", "--porcelain=v1", "--", str(relative)]):
        raise RefreshPlanError("state-refresh validator is not clean")
    validator_bytes = VALIDATOR_PATH.read_bytes()
    if recovery._run_git(["show", f"{expected_commit}:{relative}"]) != validator_bytes:
        raise RefreshPlanError("state-refresh validator differs from expected commit")
    digest = hashlib.sha256()
    digest.update(bytes.fromhex(source.tree_sha256))
    digest.update(len(validator_bytes).to_bytes(8, "big"))
    digest.update(validator_bytes)
    return recovery.SourceSnapshot(
        commit=source.commit,
        terraform_files=source.terraform_files,
        lockfile=source.lockfile,
        tree_sha256=digest.hexdigest(),
    )


def _state_attributes(state: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    resources = state.get("resources")
    if not isinstance(resources, Sequence) or isinstance(resources, (str, bytes)):
        raise RefreshPlanError("state resources are invalid")
    observed: dict[str, Mapping[str, Any]] = {}
    for resource in resources:
        if not isinstance(resource, Mapping):
            raise RefreshPlanError("state contains an invalid resource")
        instances = resource.get("instances")
        if not isinstance(instances, Sequence) or isinstance(instances, (str, bytes)):
            raise RefreshPlanError("state contains invalid instances")
        for instance in instances:
            if not isinstance(instance, Mapping):
                raise RefreshPlanError("state contains an invalid instance")
            address = recovery._state_address(resource, instance)
            attributes = instance.get("attributes")
            if address in observed or not isinstance(attributes, Mapping):
                raise RefreshPlanError("state attributes are not exact")
            observed[address] = attributes
    return observed


def _state_output_values(state: Mapping[str, Any]) -> dict[str, Any]:
    outputs = state.get("outputs")
    if not isinstance(outputs, Mapping):
        raise RefreshPlanError("state outputs are invalid")
    result: dict[str, Any] = {}
    for name, record in outputs.items():
        if not isinstance(name, str) or not isinstance(record, Mapping):
            raise RefreshPlanError("state output is invalid")
        if "value" not in record:
            raise RefreshPlanError("state output has no value")
        result[name] = record["value"]
    return result


def _state_binding_projection(state: Mapping[str, Any]) -> Mapping[str, Any]:
    checks = state.get("check_results")
    if checks is not None and (
        not isinstance(checks, Sequence) or isinstance(checks, (str, bytes))
    ):
        raise RefreshPlanError("state check results are invalid")
    return {name: value for name, value in state.items() if name != "check_results"}


def _state_bindings_equal(
    actual: Mapping[str, Any], expected: Mapping[str, Any]
) -> bool:
    return _json_equal(
        _state_binding_projection(actual), _state_binding_projection(expected)
    )


def _validate_pre_refresh_state_digest(state_bytes: bytes) -> None:
    if recovery._sha256_bytes(state_bytes) != EXPECTED_PRE_REFRESH_STATE_SHA256:
        raise RefreshPlanError("pre-refresh state digest is not the reviewed snapshot")


def _json_equal(actual: Any, expected: Any) -> bool:
    try:
        options = {"sort_keys": True, "separators": (",", ":"), "allow_nan": False}
        return json.dumps(actual, **options) == json.dumps(expected, **options)
    except (TypeError, ValueError):
        return False


def _changed_paths(before: Any, after: Any, prefix: str = "") -> set[str]:
    if type(before) is not type(after):
        return {prefix or "<root>"}
    if isinstance(before, Mapping):
        result: set[str] = set()
        for key in set(before) | set(after):
            if not isinstance(key, str):
                raise RefreshPlanError("drift contains a non-string field")
            path = f"{prefix}.{key}" if prefix else key
            if key not in before or key not in after:
                result.add(path)
            else:
                result.update(_changed_paths(before[key], after[key], path))
        return result
    return set() if _json_equal(before, after) else {prefix or "<root>"}


def _contains_true(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, Mapping):
        return any(_contains_true(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_contains_true(item) for item in value)
    return False


def _false_list(value: Any, *, label: str) -> list[bool]:
    if not isinstance(value, list):
        raise RefreshPlanError(f"{label} shape is invalid")
    return [False] * len(value)


def _role_sensitive_mask(value: Mapping[str, Any]) -> Mapping[str, Any]:
    assignable_scopes = _false_list(
        value.get("assignable_scopes"), label="role assignable scopes"
    )
    permissions = value.get("permissions")
    if not isinstance(permissions, list) or len(permissions) != 1:
        raise RefreshPlanError("role permission sensitivity shape is invalid")
    permission = permissions[0]
    if not isinstance(permission, Mapping):
        raise RefreshPlanError("role permission sensitivity shape is invalid")
    permission_mask: dict[str, Any] = {}
    for name in ("actions", "data_actions", "not_actions", "not_data_actions"):
        field = permission.get(name)
        if field is not None:
            permission_mask[name] = _false_list(
                field, label="role permission sensitivity"
            )
    return {
        "assignable_scopes": assignable_scopes,
        "permissions": [permission_mask],
    }


def _storage_sensitive_mask(*, refreshed: bool) -> Mapping[str, Any]:
    result: dict[str, Any] = {
        "azure_files_authentication": [],
        "blob_properties": [
            {
                "container_delete_retention_policy": [],
                "cors_rule": [],
                "delete_retention_policy": [],
                "restore_policy": [],
            }
        ],
        "custom_domain": [],
        "customer_managed_key": [],
        "identity": [],
        "immutability_policy": [],
        "network_rules": [
            {
                "bypass": [False],
                "ip_rules": [],
                "private_link_access": [],
                "virtual_network_subnet_ids": [],
            }
        ],
        "queue_properties": [],
        "routing": [],
        "sas_policy": [],
        "share_properties": (
            [{"cors_rule": [], "retention_policy": [{}], "smb": []}]
            if refreshed
            else []
        ),
        "static_website": [],
        "tags": {},
    }
    result.update({name: True for name in STORAGE_SECRET_FIELDS})
    return result


def _resource_identity(address: str) -> Mapping[str, str]:
    identity = {
        "resource_group_name": "cosmaneura-staging-rehearsal-control",
        "subscription_id": recovery.EXPECTED_SUBSCRIPTION_ID,
    }
    if address == STORAGE_ADDRESS:
        return {"name": ACCOUNT_NAME, **identity}
    if address == VNET_ADDRESS:
        return {"name": "cosmaneura-staging-rehearsal-control-vnet", **identity}
    if address in SUBNET_ADDRESSES:
        return {
            "name": (
                "marker-private-endpoints"
                if address == "azurerm_subnet.marker_private_endpoint[0]"
                else "private-executor-reserved"
            ),
            **identity,
            "virtual_network_name": "cosmaneura-staging-rehearsal-control-vnet",
        }
    raise RefreshPlanError("refresh identity address is invalid")


def _expected_change_metadata(
    address: str, *, before: Mapping[str, Any], after: Mapping[str, Any]
) -> Mapping[str, Any]:
    metadata: dict[str, Any] = {"actions": ["update"], "after_unknown": {}}
    if address == POLICY_ADDRESS:
        metadata.update(
            {
                "before_sensitive": {
                    "identity": [],
                    "non_compliance_message": [],
                    "overrides": [],
                    "resource_selectors": [],
                },
                "after_sensitive": {
                    "identity": [],
                    "non_compliance_message": [],
                    "not_scopes": [],
                    "overrides": [],
                    "resource_selectors": [],
                },
            }
        )
    elif address in ROLE_ADDRESSES:
        metadata.update(
            {
                "before_sensitive": _role_sensitive_mask(before),
                "after_sensitive": _role_sensitive_mask(after),
            }
        )
    elif address == STORAGE_ADDRESS:
        identity = _resource_identity(address)
        metadata.update(
            {
                "before_identity": identity,
                "after_identity": identity,
                "before_sensitive": _storage_sensitive_mask(refreshed=False),
                "after_sensitive": _storage_sensitive_mask(refreshed=True),
            }
        )
    elif address in SUBNET_ADDRESSES:
        identity = _resource_identity(address)
        metadata.update(
            {
                "before_identity": identity,
                "after_identity": identity,
                "before_sensitive": {
                    "address_prefixes": [False],
                    "delegation": [],
                    "ip_address_pool": [],
                },
                "after_sensitive": {
                    "address_prefixes": [False],
                    "delegation": [],
                    "ip_address_pool": [],
                    "service_endpoint_policy_ids": [],
                    "service_endpoints": [],
                },
            }
        )
    elif address == VNET_ADDRESS:
        identity = _resource_identity(address)
        before_mask = {
            "address_space": [False],
            "ddos_protection_plan": [],
            "dns_servers": [],
            "encryption": [],
            "ip_address_pool": [],
            "subnet": [],
            "tags": {},
        }
        after_mask = copy.deepcopy(before_mask)
        after_mask["subnet"] = [
            {
                "address_prefixes": [False],
                "delegation": [],
                "service_endpoint_policy_ids": [],
                "service_endpoints": [],
            },
            {
                "address_prefixes": [False],
                "delegation": [],
                "service_endpoint_policy_ids": [],
                "service_endpoints": [],
            },
        ]
        metadata.update(
            {
                "before_identity": identity,
                "after_identity": identity,
                "before_sensitive": before_mask,
                "after_sensitive": after_mask,
            }
        )
    else:
        raise RefreshPlanError("drift address is outside the refresh envelope")
    return metadata


def _validate_storage_drift(change: Mapping[str, Any]) -> None:
    before = change.get("before")
    after = change.get("after")
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        raise RefreshPlanError("storage drift values are invalid")
    expected = copy.deepcopy(dict(before))
    scalar_updates: dict[str, Any] = {
        "access_tier": "Hot",
        "large_file_share_enabled": False,
        "primary_location": "eastus",
        "primary_blob_endpoint": f"https://{ACCOUNT_NAME}.blob.core.windows.net/",
        "primary_blob_host": f"{ACCOUNT_NAME}.blob.core.windows.net",
        "primary_dfs_endpoint": f"https://{ACCOUNT_NAME}.dfs.core.windows.net/",
        "primary_dfs_host": f"{ACCOUNT_NAME}.dfs.core.windows.net",
        "primary_file_endpoint": f"https://{ACCOUNT_NAME}.file.core.windows.net/",
        "primary_file_host": f"{ACCOUNT_NAME}.file.core.windows.net",
        "primary_queue_endpoint": f"https://{ACCOUNT_NAME}.queue.core.windows.net/",
        "primary_queue_host": f"{ACCOUNT_NAME}.queue.core.windows.net",
        "primary_table_endpoint": f"https://{ACCOUNT_NAME}.table.core.windows.net/",
        "primary_table_host": f"{ACCOUNT_NAME}.table.core.windows.net",
        "primary_web_endpoint": f"https://{ACCOUNT_NAME}.z13.web.core.windows.net/",
        "primary_web_host": f"{ACCOUNT_NAME}.z13.web.core.windows.net",
        "share_properties": [
            {"cors_rule": [], "retention_policy": [{"days": 7}], "smb": []}
        ],
    }
    scalar_updates.update({name: "" for name in STORAGE_EMPTY_STRING_FIELDS})
    changed_fields = set(scalar_updates) | set(STORAGE_SECRET_FIELDS)
    if _changed_paths(before, after) != changed_fields:
        raise RefreshPlanError("storage drift field set is not exact")
    for name, value in scalar_updates.items():
        expected_before = [] if name == "share_properties" else None
        if not _json_equal(before.get(name), expected_before) or not _json_equal(
            after.get(name), value
        ):
            raise RefreshPlanError("storage normalization is not exact")
        expected[name] = value
    for name in STORAGE_SECRET_FIELDS:
        value = after.get(name)
        if before.get(name) is not None or not isinstance(value, str):
            raise RefreshPlanError("storage sensitive hydration is not exact")
        expected[name] = value
    expected_lengths = {
        "primary_access_key": 88,
        "primary_blob_connection_string": 232,
        "primary_connection_string": 197,
        "secondary_access_key": 88,
        "secondary_blob_connection_string": 0,
        "secondary_connection_string": 197,
    }
    if any(
        len(str(after[name])) != length for name, length in expected_lengths.items()
    ):
        raise RefreshPlanError("storage sensitive hydration length is not exact")
    for name in ("primary_access_key", "secondary_access_key"):
        try:
            decoded = base64.b64decode(str(after[name]), validate=True)
        except (ValueError, TypeError) as exc:
            raise RefreshPlanError("storage access-key encoding is invalid") from exc
        if len(decoded) != 64:
            raise RefreshPlanError("storage access-key length is invalid")
    if not _json_equal(expected, after):
        raise RefreshPlanError("storage drift contains an unapproved value change")
    for sensitive in (change.get("before_sensitive"), change.get("after_sensitive")):
        if (
            not isinstance(sensitive, Mapping)
            or any(sensitive.get(name) is not True for name in STORAGE_SECRET_FIELDS)
            or {name for name, value in sensitive.items() if _contains_true(value)}
            != STORAGE_SECRET_FIELDS
        ):
            raise RefreshPlanError("storage sensitive fields are not protected")


def _validate_normalization(address: str, change: Mapping[str, Any]) -> None:
    before = change.get("before")
    after = change.get("after")
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        raise RefreshPlanError("drift values are invalid")
    if address == STORAGE_ADDRESS:
        _validate_storage_drift(change)
        return
    if _contains_true(change.get("before_sensitive")) or _contains_true(
        change.get("after_sensitive")
    ):
        raise RefreshPlanError("non-storage drift unexpectedly contains secrets")
    expected = copy.deepcopy(dict(before))
    if address == POLICY_ADDRESS:
        if before.get("not_scopes") is not None:
            raise RefreshPlanError("policy pre-refresh value is not exact")
        expected["not_scopes"] = []
    elif address in ROLE_ADDRESSES:
        permissions = expected.get("permissions")
        if not isinstance(permissions, list) or len(permissions) != 1:
            raise RefreshPlanError("role permission shape is not exact")
        permission = permissions[0]
        if not isinstance(permission, dict):
            raise RefreshPlanError("role permission entry is not exact")
        for name in ("data_actions", "not_data_actions"):
            if permission.get(name) is None:
                permission[name] = []
        if not all(
            isinstance(permission.get(name), list)
            for name in ("actions", "not_actions", "data_actions", "not_data_actions")
        ):
            raise RefreshPlanError("role permission normalization is not exact")
    elif address in SUBNET_ADDRESSES:
        for name in ("service_endpoint_policy_ids", "service_endpoints"):
            if before.get(name) is not None:
                raise RefreshPlanError("subnet pre-refresh value is not exact")
            expected[name] = []
    elif address == VNET_ADDRESS:
        if before.get("subnet") != []:
            raise RefreshPlanError("virtual-network pre-refresh value is not exact")
        expected["subnet"] = EXPECTED_SUBNETS
    else:
        raise RefreshPlanError("drift address is outside the refresh envelope")
    if not _json_equal(expected, after):
        raise RefreshPlanError("provider normalization is not exact")


def _expected_marker_output() -> Mapping[str, Any]:
    dns = (
        f"{CONTROL_GROUP_ID}/providers/Microsoft.Network/privateDnsZones/"
        "privatelink.blob.core.windows.net"
    )
    return {
        "account_host": f"{ACCOUNT_NAME}.blob.core.windows.net",
        "container_ids": {},
        "evidence_immutability_policy_id": None,
        "immutability_policy_ids": {},
        "legal_hold_readback_proven": False,
        "legal_hold_requested": False,
        "locked_worm_requested": False,
        "management_lock_id": None,
        "private_dns_link_id": (
            f"{dns}/virtualNetworkLinks/cosmaneura-staging-rehearsal-marker-link"
        ),
        "private_dns_zone_id": dns,
        "private_endpoint_id": None,
        "private_marker_network_posture_proven": False,
        "readonly_lock_requested": False,
        "reserved_executor_subnet_id": (f"{VNET_ID}/subnets/private-executor-reserved"),
        "role_assignment_ids": {
            "cleanup_intent_read": None,
            "cleanup_marker": None,
            "cleanup_submission_read": None,
            "provision_cleanup": None,
            "provision_intent": None,
            "provision_submission": None,
        },
        "storage_account_id": (
            f"{CONTROL_GROUP_ID}/providers/Microsoft.Storage/storageAccounts/"
            f"{ACCOUNT_NAME}"
        ),
    }


def _expected_embedded_state(
    external_state: Mapping[str, Any],
    *,
    drift_by_address: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any]:
    expected = copy.deepcopy(dict(external_state))
    expected.pop("check_results", None)
    resources = expected.get("resources")
    outputs = expected.get("outputs")
    if not isinstance(resources, list) or not isinstance(outputs, dict):
        raise RefreshPlanError("pre-refresh state shape is invalid")
    changed: set[str] = set()
    for resource in resources:
        if not isinstance(resource, Mapping):
            raise RefreshPlanError("pre-refresh resource is invalid")
        instances = resource.get("instances")
        if not isinstance(instances, list):
            raise RefreshPlanError("pre-refresh resource instances are invalid")
        for instance in instances:
            if not isinstance(instance, dict):
                raise RefreshPlanError("pre-refresh resource instance is invalid")
            address = recovery._state_address(resource, instance)
            if address in drift_by_address:
                change = drift_by_address[address].get("change")
                if not isinstance(change, Mapping):
                    raise RefreshPlanError("refresh drift change is invalid")
                instance["attributes"] = copy.deepcopy(change.get("after"))
                changed.add(address)
    if changed != EXPECTED_DRIFT_ADDRESSES:
        raise RefreshPlanError("expected embedded-state drift set is not exact")
    outputs["marker_store_binding"] = {
        "value": _expected_marker_output(),
        "type": copy.deepcopy(EXPECTED_MARKER_OUTPUT_TYPE),
    }
    return expected


def _validate_embedded_state(
    external_state: Mapping[str, Any],
    embedded_state: Mapping[str, Any],
    *,
    drift_by_address: Mapping[str, Mapping[str, Any]],
) -> None:
    actual = copy.deepcopy(dict(embedded_state))
    actual.pop("check_results", None)
    if not _json_equal(
        actual,
        _expected_embedded_state(external_state, drift_by_address=drift_by_address),
    ):
        raise RefreshPlanError("embedded refreshed state is not closed-world")


def _validate_output_changes(
    plan: Mapping[str, Any],
    *,
    external_state: Mapping[str, Any],
    embedded_state: Mapping[str, Any],
) -> None:
    changes = plan.get("output_changes")
    if not isinstance(changes, Mapping) or set(changes) != EXPECTED_OUTPUT_NAMES:
        raise RefreshPlanError("refresh output set is not exact")
    before_outputs = _state_output_values(external_state)
    after_outputs = _state_output_values(embedded_state)
    if set(before_outputs) != EXPECTED_OUTPUT_NAMES - {"marker_store_binding"}:
        raise RefreshPlanError("pre-refresh output set is not exact")
    if set(after_outputs) != EXPECTED_OUTPUT_NAMES:
        raise RefreshPlanError("refreshed output set is not exact")
    for name, value in changes.items():
        if not isinstance(value, Mapping):
            raise RefreshPlanError("refresh output change is invalid")
        before = before_outputs.get(name)
        after = after_outputs.get(name)
        if not _json_equal(value.get("before"), before) or not _json_equal(
            value.get("after"), after
        ):
            raise RefreshPlanError("refresh output is not state-bound")
        if name == "marker_store_binding":
            expected = {
                "actions": ["create"],
                "before": None,
                "after": _expected_marker_output(),
                "after_unknown": False,
                "before_sensitive": False,
                "after_sensitive": False,
            }
            if not _json_equal(value, expected):
                raise RefreshPlanError("marker-store output refresh is not exact")
        elif not _json_equal(
            value,
            {
                "actions": ["no-op"],
                "before": before,
                "after": after,
                "after_unknown": False,
                "before_sensitive": False,
                "after_sensitive": False,
            },
        ):
            raise RefreshPlanError("refresh contains a non-no-op existing output")


def _validate_drift(
    plan: Mapping[str, Any],
    *,
    external_attributes: Mapping[str, Mapping[str, Any]],
    embedded_attributes: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Mapping[str, Any]]:
    drift = plan.get("resource_drift")
    if not isinstance(drift, Sequence) or isinstance(drift, (str, bytes)):
        raise RefreshPlanError("refresh drift is missing")
    by_address: dict[str, Mapping[str, Any]] = {
        address: item
        for item in drift
        if isinstance(item, Mapping) and isinstance(address := item.get("address"), str)
    }
    if len(by_address) != len(drift) or set(by_address) != EXPECTED_DRIFT_ADDRESSES:
        raise RefreshPlanError("refresh drift address set is not exact")
    if set(external_attributes) != set(recovery.EXPECTED_EXISTING_ADDRESSES):
        raise RefreshPlanError("pre-refresh state inventory is not exact")
    if set(embedded_attributes) != set(recovery.EXPECTED_EXISTING_ADDRESSES):
        raise RefreshPlanError("embedded refreshed inventory is not exact")
    for address in recovery.EXPECTED_EXISTING_ADDRESSES - EXPECTED_DRIFT_ADDRESSES:
        if not _json_equal(external_attributes[address], embedded_attributes[address]):
            raise RefreshPlanError("unreported state attribute changed during refresh")
    for address, item in by_address.items():
        change = item.get("change")
        expected_before = external_attributes[address]
        if address == STORAGE_ADDRESS:
            normalized = copy.deepcopy(dict(expected_before))
            for name in (
                "queue_properties",
                "routing",
                "share_properties",
                "static_website",
            ):
                if normalized.get(name) is not None:
                    raise RefreshPlanError("storage schema-upgrade input is not exact")
                normalized[name] = []
            expected_before = normalized
        resource_type, name = address.removesuffix("[0]").split(".", 1)
        if (
            set(item)
            != {"address", "change", "index", "mode", "name", "provider_name", "type"}
            or item.get("mode") != "managed"
            or item.get("type") != resource_type
            or item.get("name") != name
            or type(item.get("index")) is not int
            or item.get("index") != 0
            or item.get("provider_name") != "registry.opentofu.org/hashicorp/azurerm"
            or not isinstance(change, Mapping)
            or change.get("actions") != ["update"]
            or not _json_equal(change.get("before"), expected_before)
            or not _json_equal(change.get("after"), embedded_attributes[address])
            or _contains_true(change.get("after_unknown"))
        ):
            raise RefreshPlanError("refresh drift is not state-bound and update-only")
        _validate_normalization(address, change)
        observed_metadata = {
            name: value
            for name, value in change.items()
            if name not in {"before", "after"}
        }
        if not _json_equal(
            observed_metadata,
            _expected_change_metadata(
                address, before=change["before"], after=change["after"]
            ),
        ):
            raise RefreshPlanError("refresh change metadata is not exact")
    return by_address


def _root_addresses(container: Any, *, label: str) -> set[str]:
    if not isinstance(container, Mapping):
        raise RefreshPlanError(f"{label} is missing")
    values = container.get("values", container)
    root = values.get("root_module") if isinstance(values, Mapping) else None
    if not isinstance(root, Mapping):
        raise RefreshPlanError(f"{label} root module is missing")
    return recovery._module_addresses(root)


def _validate_relevant_attributes(value: Any) -> None:
    if not isinstance(value, list) or len(value) != len(EXPECTED_RELEVANT_ATTRIBUTES):
        raise RefreshPlanError("refresh relevant-attribute set is not exact")

    def normalize(
        items: Sequence[Mapping[str, Any]],
    ) -> set[tuple[str, tuple[str, ...]]]:
        result: set[tuple[str, tuple[str, ...]]] = set()
        for item in items:
            if not isinstance(item, Mapping) or set(item) != {"attribute", "resource"}:
                raise RefreshPlanError("refresh relevant attribute is invalid")
            resource = item.get("resource")
            attribute = item.get("attribute")
            if (
                not isinstance(resource, str)
                or not isinstance(attribute, list)
                or any(not isinstance(part, str) for part in attribute)
            ):
                raise RefreshPlanError("refresh relevant attribute is invalid")
            result.add((resource, tuple(attribute)))
        return result

    observed = normalize(value)
    if len(observed) != len(value) or observed != EXPECTED_RELEVANT_ATTRIBUTES:
        raise RefreshPlanError("refresh relevant-attribute set is not exact")


def _validate_plan(
    plan: Mapping[str, Any],
    *,
    external_state: Mapping[str, Any],
    embedded_state: Mapping[str, Any],
    now: datetime,
) -> str:
    if plan.get("resource_changes") not in (None, []):
        raise RefreshPlanError("refresh plan contains an Azure resource action")
    if plan.get("deferred_changes") not in (None, []):
        raise RefreshPlanError("refresh plan contains deferred changes")
    if plan.get("action_invocations") not in (None, []):
        raise RefreshPlanError("refresh plan contains action invocations")
    plan_fields = set(plan)
    if plan_fields - OPTIONAL_EMPTY_PLAN_FIELDS != EXPECTED_PLAN_FIELDS or any(
        plan.get(name) != [] for name in plan_fields & OPTIONAL_EMPTY_PLAN_FIELDS
    ):
        raise RefreshPlanError("refresh plan field set is not exact")
    if (
        plan.get("format_version") != recovery.EXPECTED_FORMAT_VERSION
        or plan.get("terraform_version") != recovery.EXPECTED_TOFU_VERSION
        or plan.get("errored") is not False
    ):
        raise RefreshPlanError("refresh plan format or status is invalid")
    timestamp = recovery._validate_plan_timestamp(plan, now=now)
    recovery._validate_provider_configuration(plan)
    recovery._validate_variables(plan)
    recovery._validate_checks(plan)
    _validate_relevant_attributes(plan.get("relevant_attributes"))
    if _root_addresses(plan.get("prior_state"), label="prior state") != set(
        recovery.EXPECTED_EXISTING_ADDRESSES
    ):
        raise RefreshPlanError("refresh prior-state inventory is not exact")
    if _root_addresses(plan.get("planned_values"), label="planned values") != set():
        raise RefreshPlanError("refresh planned inventory is not exact")
    drift_by_address = _validate_drift(
        plan,
        external_attributes=_state_attributes(external_state),
        embedded_attributes=_state_attributes(embedded_state),
    )
    _validate_output_changes(
        plan, external_state=external_state, embedded_state=embedded_state
    )
    _validate_embedded_state(
        external_state,
        embedded_state,
        drift_by_address=drift_by_address,
    )
    return timestamp


def _pull_remote_state(*, tofu: Path, data_dir: Path) -> bytes:
    result = recovery._run_process(
        [str(tofu), f"-chdir={recovery.REHEARSAL_ROOT}", "state", "pull"],
        cwd=recovery.REPOSITORY_ROOT,
        timeout_seconds=120,
        max_output_bytes=recovery.MAXIMUM_PLAN_JSON_BYTES,
        data_dir=data_dir,
    )
    if result.returncode != 0 or not result.stdout:
        raise RefreshPlanError("remote pre-refresh state could not be read")
    return result.stdout


def _generate_saved_plan(*, tofu: Path, output_parent: Path, data_dir: Path) -> bytes:
    with tempfile.TemporaryDirectory(
        prefix=".cosmaneura-state-refresh-", dir=output_parent
    ) as temporary:
        candidate = Path(temporary) / "candidate.tfplan"
        result = recovery._run_process(
            [
                str(tofu),
                f"-chdir={recovery.REHEARSAL_ROOT}",
                "plan",
                "-refresh-only",
                "-input=false",
                "-lock=true",
                "-lock-timeout=5m",
                "-refresh=true",
                "-detailed-exitcode",
                "-no-color",
                f"-out={candidate}",
                *recovery.PLAN_VARIABLE_ARGUMENTS,
            ],
            cwd=recovery.REPOSITORY_ROOT,
            timeout_seconds=900,
            data_dir=data_dir,
        )
        if result.returncode != 2:
            raise RefreshPlanError(
                "the fixed refresh-only plan did not produce the expected state change"
            )
        return recovery._read_protected_file(
            candidate, label="generated state-refresh plan", required_mode=0o600
        )


def _validate_artifacts(
    *,
    saved_plan_bytes: bytes,
    state_bytes: bytes,
    remote_state_bytes: bytes,
    source: SourceSnapshot,
    backend_metadata_sha256: str,
    tofu: Path,
    data_dir: Path,
    generation_mode: str,
    now: datetime,
    expected_saved_plan_sha256: str | None = None,
) -> Mapping[str, Any]:
    _validate_pre_refresh_state_digest(state_bytes)
    plan_sha256 = recovery._sha256_bytes(saved_plan_bytes)
    if (
        expected_saved_plan_sha256 is not None
        and plan_sha256 != expected_saved_plan_sha256
    ):
        raise RefreshPlanError(
            "saved state-refresh plan digest does not match evidence"
        )
    external = recovery._load_json_bytes(
        state_bytes, label="pre-refresh state snapshot"
    )
    remote = recovery._load_json_bytes(
        remote_state_bytes, label="remote pre-refresh state"
    )
    if not _state_bindings_equal(external, remote):
        raise RefreshPlanError("remote state differs from the protected snapshot")
    recovery._validate_state(
        external, label="pre-refresh state snapshot", expected_serial=PRE_REFRESH_SERIAL
    )
    embedded_bytes, embedded = recovery._inspect_plan_archive(
        saved_plan_bytes, source=source
    )
    recovery._validate_state(
        embedded,
        label="saved refresh plan embedded state",
        expected_serial=PRE_REFRESH_SERIAL,
    )
    plan_json_bytes = recovery._show_saved_plan(
        saved_plan_bytes, tofu=tofu, data_dir=data_dir
    )
    plan = recovery._load_json_bytes(
        plan_json_bytes, label="derived state-refresh plan JSON"
    )
    timestamp = _validate_plan(
        plan,
        external_state=external,
        embedded_state=embedded,
        now=now,
    )
    return {
        "schema_version": 1,
        "policy_id": "cosmaneura-staging-rehearsal-base-state-refresh-plan-v1",
        "generation_mode": generation_mode,
        "source_commit": source.commit,
        "source_tree_sha256": source.tree_sha256,
        "provider_lock_sha256": recovery._sha256_bytes(source.lockfile),
        "backend_metadata_sha256": backend_metadata_sha256,
        "backend_workspace": "default",
        "state_lineage": recovery.EXPECTED_LINEAGE,
        "pre_refresh_state_serial": PRE_REFRESH_SERIAL,
        "expected_post_refresh_state_serial": POST_REFRESH_SERIAL,
        "state_json_sha256": recovery._sha256_bytes(state_bytes),
        "remote_state_sha256": recovery._sha256_bytes(remote_state_bytes),
        "embedded_refreshed_state_sha256": recovery._sha256_bytes(embedded_bytes),
        "saved_plan_sha256": plan_sha256,
        "derived_plan_json_sha256": recovery._sha256_bytes(plan_json_bytes),
        "plan_timestamp": timestamp,
        "open_tofu_version": recovery.EXPECTED_TOFU_VERSION,
        "state_refresh_count": len(EXPECTED_DRIFT_ADDRESSES),
        "azure_resource_action_count": 0,
        "output_create_count": 1,
        "apply_authorized": False,
        "founder_apply_required": True,
        "pass": True,
    }


def create_and_validate_state_refresh_plan(
    *, saved_plan: Path, state_json: Path, expected_source_commit: str
) -> Mapping[str, Any]:
    recovery._reject_symlink_components(
        saved_plan, label="saved state-refresh plan", allow_missing_leaf=True
    )
    if saved_plan.exists():
        raise RefreshPlanError("saved state-refresh plan output already exists")
    recovery._require_protected_directory(
        saved_plan.parent, label="saved state-refresh plan directory"
    )
    state_bytes = recovery._read_protected_file(
        state_json, label="pre-refresh state snapshot", required_mode=0o600
    )
    _validate_pre_refresh_state_digest(state_bytes)
    source_before = _source_snapshot(expected_source_commit)
    with tempfile.TemporaryDirectory(
        prefix=".cosmaneura-refresh-tfdata-", dir=saved_plan.parent
    ) as temporary:
        data_dir = Path(temporary)
        tofu = recovery._snapshot_and_verify_runtime(data_dir=data_dir).tofu
        recovery._initialize_tofu(tofu=tofu, data_dir=data_dir)
        backend_before = recovery._backend_snapshot(tofu=tofu, data_dir=data_dir)
        remote_before = _pull_remote_state(tofu=tofu, data_dir=data_dir)
        saved_plan_bytes = _generate_saved_plan(
            tofu=tofu, output_parent=saved_plan.parent, data_dir=data_dir
        )
        remote_after = _pull_remote_state(tofu=tofu, data_dir=data_dir)
        if not _state_bindings_equal(
            recovery._load_json_bytes(remote_before, label="remote state before plan"),
            recovery._load_json_bytes(remote_after, label="remote state after plan"),
        ):
            raise RefreshPlanError(
                "remote state changed while refresh plan was generated"
            )
        receipt = _validate_artifacts(
            saved_plan_bytes=saved_plan_bytes,
            state_bytes=state_bytes,
            remote_state_bytes=remote_after,
            source=source_before,
            backend_metadata_sha256=backend_before,
            tofu=tofu,
            data_dir=data_dir,
            generation_mode="validator_refresh_only",
            now=datetime.now(UTC),
        )
        if recovery._backend_snapshot(tofu=tofu, data_dir=data_dir) != backend_before:
            raise RefreshPlanError("backend metadata changed during refresh validation")
    if _source_snapshot(expected_source_commit) != source_before:
        raise RefreshPlanError("state-refresh source changed during validation")
    recovery._write_protected_file(
        saved_plan, saved_plan_bytes, label="saved state-refresh plan"
    )
    return receipt


def revalidate_state_refresh_plan(
    *,
    saved_plan: Path,
    state_json: Path,
    execution_dir: Path,
    expected_source_commit: str,
    expected_saved_plan_sha256: str,
) -> Mapping[str, Any]:
    if re.fullmatch(r"[0-9a-f]{64}", expected_saved_plan_sha256) is None:
        raise RefreshPlanError("expected saved-plan digest is invalid")
    plan_bytes = recovery._read_protected_file(
        saved_plan, label="saved state-refresh plan", required_mode=0o400
    )
    state_bytes = recovery._read_protected_file(
        state_json, label="pre-refresh state snapshot", required_mode=0o600
    )
    _validate_pre_refresh_state_digest(state_bytes)
    if execution_dir.parent != saved_plan.parent:
        raise RefreshPlanError("execution directory must share the saved-plan parent")
    recovery._create_protected_directory(execution_dir, label="execution directory")
    source_before = _source_snapshot(expected_source_commit)
    tofu = recovery._snapshot_and_verify_runtime(data_dir=execution_dir).tofu
    recovery._initialize_tofu(tofu=tofu, data_dir=execution_dir)
    backend_before = recovery._backend_snapshot(tofu=tofu, data_dir=execution_dir)
    remote_state = _pull_remote_state(tofu=tofu, data_dir=execution_dir)
    receipt = dict(
        _validate_artifacts(
            saved_plan_bytes=plan_bytes,
            state_bytes=state_bytes,
            remote_state_bytes=remote_state,
            source=source_before,
            backend_metadata_sha256=backend_before,
            tofu=tofu,
            data_dir=execution_dir,
            generation_mode="digest_bound_revalidation",
            now=datetime.now(UTC),
            expected_saved_plan_sha256=expected_saved_plan_sha256,
        )
    )
    receipt["open_tofu_sha256"] = recovery._executable_sha256(tofu, label="OpenTofu")
    receipt["open_tofu_path"] = str(tofu)
    receipt["execution_provider_tree_sha256"] = recovery._provider_tree_sha256(
        execution_dir
    )
    if _source_snapshot(expected_source_commit) != source_before:
        raise RefreshPlanError("state-refresh source changed during revalidation")
    if recovery._backend_snapshot(tofu=tofu, data_dir=execution_dir) != backend_before:
        raise RefreshPlanError("backend metadata changed during revalidation")
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--mode", required=True, choices=("create", "verify"))
    parser.add_argument("--saved-plan", required=True, type=Path)
    parser.add_argument("--state-json", required=True, type=Path)
    parser.add_argument("--execution-dir", type=Path)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--expected-saved-plan-sha256")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.mode == "create":
            if args.execution_dir is not None or args.expected_saved_plan_sha256:
                raise RefreshPlanError(
                    "create mode does not accept execution or digest inputs"
                )
            receipt = create_and_validate_state_refresh_plan(
                saved_plan=args.saved_plan,
                state_json=args.state_json,
                expected_source_commit=args.expected_source_commit,
            )
        else:
            if args.execution_dir is None or args.expected_saved_plan_sha256 is None:
                raise RefreshPlanError(
                    "verify mode requires execution directory and reviewed digest"
                )
            receipt = revalidate_state_refresh_plan(
                saved_plan=args.saved_plan,
                state_json=args.state_json,
                execution_dir=args.execution_dir,
                expected_source_commit=args.expected_source_commit,
                expected_saved_plan_sha256=args.expected_saved_plan_sha256,
            )
    except RefreshPlanError as exc:
        print(f"state_refresh_plan_refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
