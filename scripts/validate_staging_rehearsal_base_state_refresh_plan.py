#!/usr/bin/env python3
"""Create or revalidate the one-time staging base state-refresh plan."""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from scripts import validate_staging_rehearsal_base_recovery_plan as recovery
except ModuleNotFoundError:  # Direct execution places scripts/ on sys.path.
    import validate_staging_rehearsal_base_recovery_plan as recovery


RefreshPlanError = recovery.RecoveryPlanError
VALIDATOR_PATH = Path(__file__).resolve()
PRE_REFRESH_SERIAL = 8
POST_REFRESH_SERIAL = recovery.EXPECTED_RECOVERY_SERIAL

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
EXPECTED_CHANGE_KEYS = frozenset(
    {
        "actions",
        "before",
        "after",
        "after_unknown",
        "before_sensitive",
        "after_sensitive",
    }
)


def _source_snapshot(expected_commit: str) -> recovery.SourceSnapshot:
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
    if isinstance(before, Sequence) and not isinstance(before, (str, bytes)):
        return set() if before == after else {prefix or "<root>"}
    return set() if before == after else {prefix or "<root>"}


def _contains_true(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, Mapping):
        return any(_contains_true(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_contains_true(item) for item in value)
    return False


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
        if before.get(name) != expected_before or after.get(name) != value:
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
    if expected != after:
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
    if expected != after:
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
        if value.get("before") != before or value.get("after") != after:
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
            if value != expected:
                raise RefreshPlanError("marker-store output refresh is not exact")
        elif value != {
            "actions": ["no-op"],
            "before": before,
            "after": after,
            "after_unknown": False,
            "before_sensitive": False,
            "after_sensitive": False,
        }:
            raise RefreshPlanError("refresh contains a non-no-op existing output")


def _validate_drift(
    plan: Mapping[str, Any],
    *,
    external_attributes: Mapping[str, Mapping[str, Any]],
    embedded_attributes: Mapping[str, Mapping[str, Any]],
) -> None:
    drift = plan.get("resource_drift")
    if not isinstance(drift, Sequence) or isinstance(drift, (str, bytes)):
        raise RefreshPlanError("refresh drift is missing")
    by_address = {
        item.get("address"): item for item in drift if isinstance(item, Mapping)
    }
    if len(by_address) != len(drift) or set(by_address) != EXPECTED_DRIFT_ADDRESSES:
        raise RefreshPlanError("refresh drift address set is not exact")
    if set(external_attributes) != set(recovery.EXPECTED_EXISTING_ADDRESSES):
        raise RefreshPlanError("pre-refresh state inventory is not exact")
    if set(embedded_attributes) != set(recovery.EXPECTED_EXISTING_ADDRESSES):
        raise RefreshPlanError("embedded refreshed inventory is not exact")
    for address in recovery.EXPECTED_EXISTING_ADDRESSES - EXPECTED_DRIFT_ADDRESSES:
        if external_attributes[address] != embedded_attributes[address]:
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
            or item.get("index") != 0
            or item.get("provider_name") != "registry.opentofu.org/hashicorp/azurerm"
            or not isinstance(change, Mapping)
            or set(change) != EXPECTED_CHANGE_KEYS
            or change.get("actions") != ["update"]
            or change.get("before") != expected_before
            or change.get("after") != embedded_attributes[address]
            or _contains_true(change.get("after_unknown"))
        ):
            raise RefreshPlanError("refresh drift is not state-bound and update-only")
        _validate_normalization(address, change)


def _root_addresses(container: Any, *, label: str) -> set[str]:
    if not isinstance(container, Mapping):
        raise RefreshPlanError(f"{label} is missing")
    values = container.get("values", container)
    root = values.get("root_module") if isinstance(values, Mapping) else None
    if not isinstance(root, Mapping):
        raise RefreshPlanError(f"{label} root module is missing")
    return recovery._module_addresses(root)


def _validate_plan(
    plan: Mapping[str, Any],
    *,
    external_state: Mapping[str, Any],
    embedded_state: Mapping[str, Any],
    now: datetime,
) -> str:
    if (
        plan.get("format_version") != recovery.EXPECTED_FORMAT_VERSION
        or plan.get("terraform_version") != recovery.EXPECTED_TOFU_VERSION
        or plan.get("errored") is not False
    ):
        raise RefreshPlanError("refresh plan format or status is invalid")
    for optional in ("applyable", "complete"):
        if optional in plan and plan[optional] is not True:
            raise RefreshPlanError(f"refresh plan reports {optional}=false")
    if plan.get("resource_changes") not in (None, []):
        raise RefreshPlanError("refresh plan contains an Azure resource action")
    if plan.get("deferred_changes") not in (None, []):
        raise RefreshPlanError("refresh plan contains deferred changes")
    if plan.get("action_invocations") not in (None, []):
        raise RefreshPlanError("refresh plan contains action invocations")
    timestamp = recovery._validate_plan_timestamp(plan, now=now)
    recovery._validate_provider_configuration(plan)
    recovery._validate_variables(plan)
    recovery._validate_checks(plan)
    if _root_addresses(plan.get("prior_state"), label="prior state") != set(
        recovery.EXPECTED_EXISTING_ADDRESSES
    ):
        raise RefreshPlanError("refresh prior-state inventory is not exact")
    if _root_addresses(plan.get("planned_values"), label="planned values") != set():
        raise RefreshPlanError("refresh planned inventory is not exact")
    _validate_drift(
        plan,
        external_attributes=_state_attributes(external_state),
        embedded_attributes=_state_attributes(embedded_state),
    )
    _validate_output_changes(
        plan, external_state=external_state, embedded_state=embedded_state
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
    source: recovery.SourceSnapshot,
    backend_metadata_sha256: str,
    tofu: Path,
    data_dir: Path,
    generation_mode: str,
    now: datetime,
    expected_saved_plan_sha256: str | None = None,
) -> Mapping[str, Any]:
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
    if _state_binding_projection(external) != _state_binding_projection(remote):
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
    source_before = _source_snapshot(expected_source_commit)
    tofu = recovery._resolve_and_verify_tofu()
    with tempfile.TemporaryDirectory(
        prefix=".cosmaneura-refresh-tfdata-", dir=saved_plan.parent
    ) as temporary:
        data_dir = Path(temporary)
        recovery._initialize_tofu(tofu=tofu, data_dir=data_dir)
        backend_before = recovery._backend_snapshot(tofu=tofu, data_dir=data_dir)
        remote_before = _pull_remote_state(tofu=tofu, data_dir=data_dir)
        saved_plan_bytes = _generate_saved_plan(
            tofu=tofu, output_parent=saved_plan.parent, data_dir=data_dir
        )
        remote_after = _pull_remote_state(tofu=tofu, data_dir=data_dir)
        if recovery._load_json_bytes(
            remote_before, label="remote state before plan"
        ) != recovery._load_json_bytes(remote_after, label="remote state after plan"):
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
    if execution_dir.parent != saved_plan.parent:
        raise RefreshPlanError("execution directory must share the saved-plan parent")
    recovery._create_protected_directory(execution_dir, label="execution directory")
    source_before = _source_snapshot(expected_source_commit)
    tofu = recovery._resolve_and_verify_tofu()
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
