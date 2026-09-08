#!/usr/bin/env python3
"""Validate the one-shot bootstrap-UAMI teardown plan without authorizing apply."""

from __future__ import annotations

import argparse
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
    from scripts import validate_staging_sanitizer_bootstrap_identity_plan as base
except ModuleNotFoundError:  # Direct execution places scripts/ on sys.path.
    import validate_staging_rehearsal_base_recovery_plan as recovery
    import validate_staging_sanitizer_bootstrap_identity_plan as base

BootstrapPlanError = base.BootstrapPlanError

EXPECTED_REHEARSAL_ID = "21506aaeec3c"
EXPECTED_CLEANUP_NOT_AFTER = "2026-09-08T17:40:01Z"
EXPECTED_EVIDENCE_SHA256 = (
    "sha256:2dbd5f913d2c1ed565939fd6203461cbe93593d48fef9916031f3e1ead0c78c9"
)
EXPECTED_STATE_LINEAGE = "a0c8b939-2fb2-0604-3e70-d925aeee5950"
EXPECTED_STATE_SERIAL = 2
EXPECTED_CLIENT_ID = "1d494ade-7391-479b-b860-c0b13ef98c34"
EXPECTED_PRINCIPAL_ID = "10e9110b-9a64-4961-9210-06ab05b4e5ab"
EXPECTED_GUARD_ID = "6f6ac9c3-68ec-f69b-8e3b-e466d5bf478e"
EXPECTED_RESOURCE_ID = (
    f"/subscriptions/{base.EXPECTED_SUBSCRIPTION_ID}/resourceGroups/"
    f"{base.EXPECTED_RESOURCE_GROUP}/providers/Microsoft.ManagedIdentity/"
    f"userAssignedIdentities/{base.EXPECTED_IDENTITY_NAME}"
)
EXPECTED_BACKEND_CONFIG: Mapping[str, Any] = {
    "resource_group_name": "cosmaneura-tfstate",
    "storage_account_name": "cosmaneuratfstate",
    "container_name": "cosmaneura-tfstate",
    "key": "staging-sanitizer-bootstrap-identity.tfstate",
    "subscription_id": base.EXPECTED_SUBSCRIPTION_ID,
    # Azure CLI 2.89 rejects get-access-token when both subscription and tenant
    # are supplied. The exact subscription selects its tenant; the refreshed
    # UAMI and embedded state are still required to carry EXPECTED_TENANT_ID.
    "tenant_id": None,
    "environment": "public",
    "use_azuread_auth": True,
    "use_cli": True,
}
EXPECTED_VARIABLES = frozenset(
    {
        "authorize_sanitizer_bootstrap_identity_teardown",
        "enable_sanitizer_bootstrap_identity",
        "sanitizer_bootstrap_identity_plan_approved",
        "sanitizer_bootstrap_lifetime_hours",
        "sanitizer_bootstrap_rehearsal_id",
        "sanitizer_bootstrap_teardown_evidence_sha256",
        "sanitizer_bootstrap_teardown_rehearsal_id",
    }
)
EXPECTED_CHANGE_ADDRESSES = frozenset(
    {
        "azurerm_user_assigned_identity.sanitizer_bootstrap[0]",
        "terraform_data.activation_guard[0]",
    }
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _expected_tags() -> dict[str, str]:
    return {
        **base.EXPECTED_BASE_TAGS,
        "cleanup-not-after": EXPECTED_CLEANUP_NOT_AFTER,
        "rehearsal-id": EXPECTED_REHEARSAL_ID,
    }


def _uami_values() -> dict[str, Any]:
    return {
        "client_id": EXPECTED_CLIENT_ID,
        "id": EXPECTED_RESOURCE_ID,
        "isolation_scope": "",
        "location": base.EXPECTED_LOCATION,
        "name": base.EXPECTED_IDENTITY_NAME,
        "principal_id": EXPECTED_PRINCIPAL_ID,
        "resource_group_name": base.EXPECTED_RESOURCE_GROUP,
        "tags": _expected_tags(),
        "tenant_id": base.EXPECTED_TENANT_ID,
        "timeouts": None,
    }


def _old_guard_values() -> dict[str, Any]:
    lifecycle = {
        "cleanup_not_after": EXPECTED_CLEANUP_NOT_AFTER,
        "lifetime_hours": 6,
        "rehearsal_id": EXPECTED_REHEARSAL_ID,
    }
    return {
        "id": EXPECTED_GUARD_ID,
        "input": {"enabled": True, "exact_plan_approved": True, **lifecycle},
        "output": {"enabled": True, "exact_plan_approved": True, **lifecycle},
        "triggers_replace": lifecycle,
    }


def _new_guard_values() -> dict[str, Any]:
    return {
        "id": EXPECTED_GUARD_ID,
        "input": {
            "cleanup_not_after": EXPECTED_CLEANUP_NOT_AFTER,
            "identity_resource_id": EXPECTED_RESOURCE_ID,
            "mode": "teardown-only",
            "rehearsal_id": EXPECTED_REHEARSAL_ID,
            "teardown_authorized": True,
            "teardown_evidence_sha256": EXPECTED_EVIDENCE_SHA256,
        },
        "triggers_replace": {
            "cleanup_not_after": EXPECTED_CLEANUP_NOT_AFTER,
            "lifetime_hours": 6,
            "rehearsal_id": EXPECTED_REHEARSAL_ID,
        },
    }


def _foundation_value() -> dict[str, Any]:
    return {
        "attached_job_resource_ids": [],
        "cleanup_not_after": EXPECTED_CLEANUP_NOT_AFTER,
        "client_id": EXPECTED_CLIENT_ID,
        "database_authority_bound": False,
        "destruction_protected": True,
        "exact_ids_authenticated": False,
        "federated_credential_ids": [],
        "lifecycle": "ephemeral",
        "name": base.EXPECTED_IDENTITY_NAME,
        "principal_id": EXPECTED_PRINCIPAL_ID,
        "provider_authority_bound": False,
        "rehearsal_id": EXPECTED_REHEARSAL_ID,
        "resource_id": EXPECTED_RESOURCE_ID,
        "role_assignment_ids": [],
        "single_use_job_bound": False,
        "tenant_id": base.EXPECTED_TENANT_ID,
        "workflow_binding_implemented": False,
    }


def _teardown_value() -> dict[str, Any]:
    return {
        "apply_authorized": False,
        "cleanup_not_after": EXPECTED_CLEANUP_NOT_AFTER,
        "founder_apply_required": True,
        "identity_resource_id": EXPECTED_RESOURCE_ID,
        "mode": "teardown-only",
        "rehearsal_id": EXPECTED_REHEARSAL_ID,
        "teardown_evidence_sha256": EXPECTED_EVIDENCE_SHA256,
    }


def _raw_output_values(outputs: Any) -> dict[str, Any]:
    if not isinstance(outputs, Mapping):
        raise BootstrapPlanError("embedded state outputs are invalid")
    result: dict[str, Any] = {}
    for name, record in outputs.items():
        if (
            not isinstance(name, str)
            or not isinstance(record, Mapping)
            or set(record) != {"value", "type"}
        ):
            raise BootstrapPlanError("embedded state output shape is not exact")
        result[name] = record["value"]
    return result


def _raw_typed_value(value: Any) -> Any:
    if not isinstance(value, Mapping) or set(value) != {"value", "type"}:
        raise BootstrapPlanError("embedded terraform_data value is not exact")
    return value["value"]


def _validate_raw_resource_group(resource: Any) -> None:
    expected_id = (
        f"/subscriptions/{base.EXPECTED_SUBSCRIPTION_ID}/resourceGroups/"
        f"{base.EXPECTED_RESOURCE_GROUP}"
    )
    if (
        not isinstance(resource, Mapping)
        or set(resource) != {"mode", "type", "name", "provider", "instances"}
        or resource.get("mode") != "data"
        or resource.get("type") != "azurerm_resource_group"
        or resource.get("name") != "control"
        or resource.get("provider")
        != 'provider["registry.opentofu.org/hashicorp/azurerm"]'
    ):
        raise BootstrapPlanError("embedded resource-group identity is not exact")
    instances = resource.get("instances")
    if not isinstance(instances, list) or len(instances) != 1:
        raise BootstrapPlanError("embedded resource-group instance is not exact")
    instance = instances[0]
    expected_attributes = {
        "id": expected_id,
        "location": base.EXPECTED_LOCATION,
        "managed_by": "",
        "name": base.EXPECTED_RESOURCE_GROUP,
        "tags": dict(base.EXPECTED_CONTROL_GROUP_TAGS),
        "timeouts": None,
    }
    if (
        not isinstance(instance, Mapping)
        or set(instance)
        != {"index_key", "schema_version", "attributes", "sensitive_attributes"}
        or instance.get("index_key") != 0
        or instance.get("schema_version") != 0
        or instance.get("attributes") != expected_attributes
        or instance.get("sensitive_attributes") != []
    ):
        raise BootstrapPlanError("embedded resource-group values are not exact")


def _validate_raw_uami(resource: Any) -> None:
    if (
        not isinstance(resource, Mapping)
        or set(resource) != {"mode", "type", "name", "provider", "instances"}
        or resource.get("mode") != "managed"
        or resource.get("type") != "azurerm_user_assigned_identity"
        or resource.get("name") != "sanitizer_bootstrap"
        or resource.get("provider")
        != 'provider["registry.opentofu.org/hashicorp/azurerm"]'
    ):
        raise BootstrapPlanError("embedded UAMI identity is not exact")
    instances = resource.get("instances")
    if not isinstance(instances, list) or len(instances) != 1:
        raise BootstrapPlanError("embedded UAMI instance is not exact")
    instance = instances[0]
    if (
        not isinstance(instance, Mapping)
        or set(instance)
        != {
            "index_key",
            "schema_version",
            "attributes",
            "sensitive_attributes",
            "private",
            "dependencies",
        }
        or instance.get("index_key") != 0
        or instance.get("schema_version") != 1
        or instance.get("attributes") != _uami_values()
        or instance.get("sensitive_attributes") != []
        or not isinstance(instance.get("private"), str)
        or not instance.get("private")
        or instance.get("dependencies")
        != ["data.azurerm_resource_group.control", "terraform_data.activation_guard"]
    ):
        raise BootstrapPlanError("embedded UAMI values are not exact")


def _validate_raw_guard(resource: Any) -> None:
    if (
        not isinstance(resource, Mapping)
        or set(resource) != {"mode", "type", "name", "provider", "instances"}
        or resource.get("mode") != "managed"
        or resource.get("type") != "terraform_data"
        or resource.get("name") != "activation_guard"
        or resource.get("provider") != 'provider["terraform.io/builtin/terraform"]'
    ):
        raise BootstrapPlanError("embedded activation-guard identity is not exact")
    instances = resource.get("instances")
    if not isinstance(instances, list) or len(instances) != 1:
        raise BootstrapPlanError("embedded activation-guard instance is not exact")
    instance = instances[0]
    attributes = instance.get("attributes") if isinstance(instance, Mapping) else None
    if (
        not isinstance(instance, Mapping)
        or set(instance)
        != {"index_key", "schema_version", "attributes", "sensitive_attributes"}
        or instance.get("index_key") != 0
        or instance.get("schema_version") != 0
        or not isinstance(attributes, Mapping)
        or set(attributes) != {"id", "input", "output", "triggers_replace"}
        or attributes.get("id") != EXPECTED_GUARD_ID
        or _raw_typed_value(attributes.get("input")) != _old_guard_values()["input"]
        or _raw_typed_value(attributes.get("output")) != _old_guard_values()["output"]
        or _raw_typed_value(attributes.get("triggers_replace"))
        != _old_guard_values()["triggers_replace"]
        or instance.get("sensitive_attributes") != []
    ):
        raise BootstrapPlanError("embedded activation-guard values are not exact")


def _validate_embedded_state(state: Mapping[str, Any], *, before_refresh: bool) -> None:
    if set(state) != {
        "version",
        "terraform_version",
        "serial",
        "lineage",
        "outputs",
        "resources",
        "check_results",
    }:
        raise BootstrapPlanError("embedded state field set is not exact")
    # OpenTofu 1.12.5 preserves the populated pre-refresh snapshot in
    # tfstate-prev but writes that member with its canonical local envelope
    # header. The refreshed tfstate remains the authenticated remote snapshot.
    expected_lineage = "" if before_refresh else EXPECTED_STATE_LINEAGE
    expected_serial = 0 if before_refresh else EXPECTED_STATE_SERIAL
    if (
        state.get("version") != 4
        or state.get("terraform_version") != base.EXPECTED_TOFU_VERSION
        or type(state.get("serial")) is not int
        or state.get("serial") != expected_serial
        or state.get("lineage") != expected_lineage
    ):
        raise BootstrapPlanError("embedded state lineage or serial is not exact")
    expected_outputs = {
        "state_boundary": dict(base.EXPECTED_STATE_BOUNDARY),
        (
            "sanitizer_bootstrap_identity_foundation"
            if before_refresh
            else "sanitizer_bootstrap_identity_teardown"
        ): _foundation_value() if before_refresh else _teardown_value(),
    }
    if _raw_output_values(state.get("outputs")) != expected_outputs:
        raise BootstrapPlanError("embedded state outputs are not exact")
    resources = state.get("resources")
    if not isinstance(resources, list):
        raise BootstrapPlanError("embedded state resources are invalid")
    by_key = {
        (item.get("mode"), item.get("type"), item.get("name")): item
        for item in resources
        if isinstance(item, Mapping)
    }
    expected_keys = {
        ("managed", "azurerm_user_assigned_identity", "sanitizer_bootstrap"),
        ("managed", "terraform_data", "activation_guard"),
    }
    if before_refresh:
        expected_keys.add(("data", "azurerm_resource_group", "control"))
    if len(by_key) != len(resources) or set(by_key) != expected_keys:
        raise BootstrapPlanError("embedded state resource set is not exact")
    if before_refresh:
        _validate_raw_resource_group(
            by_key[("data", "azurerm_resource_group", "control")]
        )
    _validate_raw_uami(
        by_key[("managed", "azurerm_user_assigned_identity", "sanitizer_bootstrap")]
    )
    _validate_raw_guard(by_key[("managed", "terraform_data", "activation_guard")])


def _validate_embedded_states(
    previous: Mapping[str, Any], refreshed: Mapping[str, Any]
) -> None:
    _validate_embedded_state(previous, before_refresh=True)
    _validate_embedded_state(refreshed, before_refresh=False)


def _validate_backend_metadata(value: bytes) -> str:
    document = base._load_json_bytes(value, label="backend metadata")
    backend = document.get("backend")
    if not isinstance(backend, Mapping) or backend.get("type") != "azurerm":
        raise BootstrapPlanError("backend type is not the isolated AzureRM backend")
    config = backend.get("config")
    if not isinstance(config, Mapping):
        raise BootstrapPlanError("backend configuration is missing")
    for name, expected in EXPECTED_BACKEND_CONFIG.items():
        if name not in config or config[name] != expected:
            raise BootstrapPlanError("backend coordinates are not exact")
    for name, setting in config.items():
        if name not in EXPECTED_BACKEND_CONFIG and setting not in (None, "", False):
            raise BootstrapPlanError("backend configuration contains an override")
    return base._sha256_bytes(value)


def _backend_init_arguments(tofu: Path) -> tuple[str, ...]:
    return (
        str(tofu),
        f"-chdir={base.IDENTITY_ROOT}",
        "init",
        "-backend=true",
        "-input=false",
        "-lockfile=readonly",
        "-reconfigure",
        *(
            f"-backend-config={key}={str(value).lower()}"
            for key, value in EXPECTED_BACKEND_CONFIG.items()
            if value is not None
        ),
    )


def _validate_variables(plan: Mapping[str, Any]) -> None:
    variables = plan.get("variables")
    if not isinstance(variables, Mapping) or set(variables) != EXPECTED_VARIABLES:
        raise BootstrapPlanError("teardown plan variable set is not exact")
    values: dict[str, Any] = {}
    for name, record in variables.items():
        if not isinstance(record, Mapping) or set(record) != {"value"}:
            raise BootstrapPlanError("teardown plan variable shape is not exact")
        values[name] = record["value"]
    authorization = values.pop("authorize_sanitizer_bootstrap_identity_teardown")
    if not base._normalized_bool(authorization, label="teardown authorization"):
        raise BootstrapPlanError("teardown authorization must be true")
    if values != {
        "enable_sanitizer_bootstrap_identity": False,
        "sanitizer_bootstrap_identity_plan_approved": False,
        "sanitizer_bootstrap_lifetime_hours": None,
        "sanitizer_bootstrap_rehearsal_id": None,
        "sanitizer_bootstrap_teardown_evidence_sha256": EXPECTED_EVIDENCE_SHA256,
        "sanitizer_bootstrap_teardown_rehearsal_id": EXPECTED_REHEARSAL_ID,
    }:
        raise BootstrapPlanError("teardown plan variables are not the one-shot values")


def _validate_checks(plan: Mapping[str, Any]) -> None:
    checks = plan.get("checks")
    if not isinstance(checks, Sequence) or isinstance(checks, (str, bytes)):
        raise BootstrapPlanError("teardown plan checks are missing")
    observed: dict[str, tuple[str, tuple[str, ...]]] = {}
    for check in checks:
        address = check.get("address") if isinstance(check, Mapping) else None
        instances = check.get("instances") if isinstance(check, Mapping) else None
        if (
            not isinstance(address, Mapping)
            or not isinstance(address.get("to_display"), str)
            or not isinstance(instances, list)
            or not all(isinstance(instance, Mapping) for instance in instances)
            or any(instance.get("problems") not in (None, []) for instance in instances)
        ):
            raise BootstrapPlanError("teardown plan check result is invalid")
        name = address["to_display"]
        if name in observed:
            raise BootstrapPlanError("teardown plan contains a duplicate check")
        observed[name] = (
            str(check.get("status")),
            tuple(str(instance.get("status")) for instance in instances),
        )
    expected_names = {
        "terraform_data.activation_guard",
        "var.sanitizer_bootstrap_lifetime_hours",
        "var.sanitizer_bootstrap_rehearsal_id",
        "var.sanitizer_bootstrap_teardown_evidence_sha256",
        "var.sanitizer_bootstrap_teardown_rehearsal_id",
    }
    if observed != {name: ("pass", ("pass",)) for name in expected_names}:
        raise BootstrapPlanError("teardown plan check result set is not exact")


def _validate_configuration(plan: Mapping[str, Any]) -> None:
    base._validate_provider_configuration(plan)
    configuration = plan.get("configuration")
    root = (
        configuration.get("root_module") if isinstance(configuration, Mapping) else None
    )
    if not isinstance(root, Mapping):
        raise BootstrapPlanError("teardown root configuration is missing")
    resources = root.get("resources")
    if not isinstance(resources, list):
        raise BootstrapPlanError("teardown root resources are missing")
    by_address = {
        item.get("address"): item for item in resources if isinstance(item, Mapping)
    }
    expected_counts = {
        "azurerm_user_assigned_identity.sanitizer_bootstrap": 0,
        "terraform_data.activation_guard": 1,
        "data.azurerm_resource_group.control": 0,
    }
    if len(by_address) != len(resources) or set(by_address) != set(expected_counts):
        raise BootstrapPlanError("teardown root resource set is not exact")
    for address, count in expected_counts.items():
        if by_address[address].get("count_expression") != {"constant_value": count}:
            raise BootstrapPlanError("teardown root resource counts are not exact")
    if (
        not isinstance(root.get("variables"), Mapping)
        or set(root["variables"]) != EXPECTED_VARIABLES
    ):
        raise BootstrapPlanError("teardown root variable set is not exact")
    if not isinstance(root.get("outputs"), Mapping) or set(root["outputs"]) != {
        "sanitizer_bootstrap_identity_foundation",
        "sanitizer_bootstrap_identity_teardown",
        "state_boundary",
    }:
        raise BootstrapPlanError("teardown root output set is not exact")


def _validate_plan_uami_state(resource: Mapping[str, Any]) -> None:
    if (
        set(resource)
        != {
            "address",
            "mode",
            "type",
            "name",
            "index",
            "provider_name",
            "schema_version",
            "values",
            "sensitive_values",
            "depends_on",
        }
        or resource.get("address")
        != "azurerm_user_assigned_identity.sanitizer_bootstrap[0]"
        or resource.get("mode") != "managed"
        or resource.get("type") != "azurerm_user_assigned_identity"
        or resource.get("name") != "sanitizer_bootstrap"
        or resource.get("index") != 0
        or resource.get("provider_name") != "registry.opentofu.org/hashicorp/azurerm"
        or resource.get("schema_version") != 1
        or resource.get("values") != _uami_values()
        or resource.get("sensitive_values") != {"tags": {}}
        or resource.get("depends_on")
        != ["data.azurerm_resource_group.control", "terraform_data.activation_guard"]
    ):
        raise BootstrapPlanError("teardown prior-state UAMI is not exact")


def _validate_plan_guard_state(resource: Mapping[str, Any]) -> None:
    if (
        set(resource)
        != {
            "address",
            "mode",
            "type",
            "name",
            "index",
            "provider_name",
            "schema_version",
            "values",
            "sensitive_values",
        }
        or resource.get("address") != "terraform_data.activation_guard[0]"
        or resource.get("mode") != "managed"
        or resource.get("type") != "terraform_data"
        or resource.get("name") != "activation_guard"
        or resource.get("index") != 0
        or resource.get("provider_name") != "terraform.io/builtin/terraform"
        or resource.get("schema_version") != 0
        or resource.get("values") != _old_guard_values()
        or resource.get("sensitive_values")
        != {"input": {}, "output": {}, "triggers_replace": {}}
    ):
        raise BootstrapPlanError("teardown prior-state guard is not exact")


def _validate_prior_state(plan: Mapping[str, Any]) -> None:
    prior = plan.get("prior_state")
    if (
        not isinstance(prior, Mapping)
        or set(prior) != {"format_version", "terraform_version", "values"}
        or prior.get("format_version") != "1.0"
        or prior.get("terraform_version") != base.EXPECTED_TOFU_VERSION
        or not isinstance(prior.get("values"), Mapping)
    ):
        raise BootstrapPlanError("teardown plan prior state is missing")
    values = prior["values"]
    root = values.get("root_module")
    outputs = values.get("outputs")
    expected_outputs = {
        "sanitizer_bootstrap_identity_teardown": {
            "sensitive": False,
            "value": _teardown_value(),
            "type": [
                "object",
                {
                    "apply_authorized": "bool",
                    "cleanup_not_after": "string",
                    "founder_apply_required": "bool",
                    "identity_resource_id": "string",
                    "mode": "string",
                    "rehearsal_id": "string",
                    "teardown_evidence_sha256": "string",
                },
            ],
        },
        "state_boundary": {
            "sensitive": False,
            "value": dict(base.EXPECTED_STATE_BOUNDARY),
            "type": [
                "object",
                {
                    "container": "string",
                    "key": "string",
                    "resource_group": "string",
                    "storage_account": "string",
                },
            ],
        },
    }
    if (
        set(values) != {"outputs", "root_module"}
        or outputs != expected_outputs
        or not isinstance(root, Mapping)
        or set(root) != {"resources"}
        or not isinstance(root.get("resources"), list)
        or len(root["resources"]) != 2
    ):
        raise BootstrapPlanError("teardown plan prior-state boundary is not exact")
    by_address = {
        item.get("address"): item
        for item in root["resources"]
        if isinstance(item, Mapping)
    }
    if set(by_address) != {
        "azurerm_user_assigned_identity.sanitizer_bootstrap[0]",
        "terraform_data.activation_guard[0]",
    }:
        raise BootstrapPlanError("teardown plan prior-state resources are not exact")
    _validate_plan_uami_state(
        by_address["azurerm_user_assigned_identity.sanitizer_bootstrap[0]"]
    )
    _validate_plan_guard_state(by_address["terraform_data.activation_guard[0]"])


def _validate_resource_changes(plan: Mapping[str, Any]) -> None:
    changes = plan.get("resource_changes")
    if not isinstance(changes, list):
        raise BootstrapPlanError("teardown plan resource changes are missing")
    by_address = {
        item.get("address"): item for item in changes if isinstance(item, Mapping)
    }
    if len(by_address) != len(changes) or set(by_address) != EXPECTED_CHANGE_ADDRESSES:
        raise BootstrapPlanError("teardown plan action set is not exact")
    uami = by_address["azurerm_user_assigned_identity.sanitizer_bootstrap[0]"]
    guard = by_address["terraform_data.activation_guard[0]"]
    if (
        uami.get("mode") != "managed"
        or uami.get("type") != "azurerm_user_assigned_identity"
        or uami.get("name") != "sanitizer_bootstrap"
        or uami.get("index") != 0
        or uami.get("provider_name") != "registry.opentofu.org/hashicorp/azurerm"
        or uami.get("action_reason") != "delete_because_count_index"
        or uami.get("change")
        != {
            "actions": ["delete"],
            "before": _uami_values(),
            "after": None,
            "after_unknown": {},
            "before_sensitive": {"tags": {}},
            "after_sensitive": False,
        }
    ):
        raise BootstrapPlanError("teardown UAMI delete is not exact")
    old_guard = _old_guard_values()
    new_guard = _new_guard_values()
    if (
        set(guard)
        != {"address", "mode", "type", "name", "index", "provider_name", "change"}
        or guard.get("mode") != "managed"
        or guard.get("type") != "terraform_data"
        or guard.get("name") != "activation_guard"
        or guard.get("index") != 0
        or guard.get("provider_name") != "terraform.io/builtin/terraform"
        or guard.get("change")
        != {
            "actions": ["update"],
            "before": old_guard,
            "after": new_guard,
            "after_unknown": {"input": {}, "output": True, "triggers_replace": {}},
            "before_sensitive": {"input": {}, "output": {}, "triggers_replace": {}},
            "after_sensitive": {"input": {}, "output": {}, "triggers_replace": {}},
        }
    ):
        raise BootstrapPlanError("teardown activation-guard update is not exact")


def _validate_outputs(plan: Mapping[str, Any]) -> None:
    outputs = plan.get("output_changes")
    if not isinstance(outputs, Mapping) or set(outputs) != {
        "sanitizer_bootstrap_identity_foundation",
        "sanitizer_bootstrap_identity_teardown",
        "state_boundary",
    }:
        raise BootstrapPlanError("teardown plan output change set is not exact")
    if outputs["sanitizer_bootstrap_identity_foundation"] != {
        "actions": ["update"],
        "before": _foundation_value(),
        "after": None,
        "after_unknown": False,
        "before_sensitive": False,
        "after_sensitive": False,
    }:
        raise BootstrapPlanError("teardown foundation removal is not exact")
    if outputs["sanitizer_bootstrap_identity_teardown"] != {
        "actions": ["create"],
        "before": None,
        "after": _teardown_value(),
        "after_unknown": False,
        "before_sensitive": False,
        "after_sensitive": False,
    }:
        raise BootstrapPlanError("teardown tombstone output is not exact")
    boundary = {
        "actions": ["no-op"],
        "before": dict(base.EXPECTED_STATE_BOUNDARY),
        "after": dict(base.EXPECTED_STATE_BOUNDARY),
        "after_unknown": False,
        "before_sensitive": False,
        "after_sensitive": False,
    }
    if outputs["state_boundary"] != boundary:
        raise BootstrapPlanError("teardown state-boundary output is not exact")


def _validate_plan(plan: Mapping[str, Any], *, now: datetime) -> str:
    if (
        plan.get("format_version") != base.EXPECTED_FORMAT_VERSION
        or plan.get("terraform_version") != base.EXPECTED_TOFU_VERSION
        or plan.get("errored") is not False
    ):
        raise BootstrapPlanError("teardown plan format or status is invalid")
    for optional in ("applyable", "complete"):
        if plan.get(optional) not in (None, True):
            raise BootstrapPlanError(f"teardown plan reports {optional}=false")
    for name in ("resource_drift", "deferred_changes", "action_invocations"):
        if plan.get(name) not in (None, []):
            raise BootstrapPlanError(f"teardown plan contains unexpected {name}")
    timestamp = plan.get("timestamp")
    if (
        not isinstance(timestamp, str)
        or re.fullmatch(
            r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
            timestamp,
        )
        is None
    ):
        raise BootstrapPlanError("teardown plan timestamp is invalid")
    try:
        parsed = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise BootstrapPlanError("teardown plan timestamp is invalid") from exc
    if parsed > now.astimezone(UTC) + base.MAXIMUM_CLOCK_SKEW:
        raise BootstrapPlanError("teardown plan timestamp is in the future")
    _validate_variables(plan)
    _validate_configuration(plan)
    _validate_checks(plan)
    _validate_prior_state(plan)
    _validate_resource_changes(plan)
    _validate_outputs(plan)
    return timestamp


def _validate_artifacts(
    *,
    saved_plan_bytes: bytes,
    expected_saved_plan_sha256: str,
    source: base.SourceSnapshot,
    plan_json_bytes: bytes,
    tofu_sha256: str,
    provider_sha256: str,
    provider_package_h1: str,
    backend_metadata_sha256: str,
    now: datetime,
) -> Mapping[str, Any]:
    archive = _validate_archive(
        saved_plan_bytes=saved_plan_bytes,
        expected_saved_plan_sha256=expected_saved_plan_sha256,
        source=source,
    )
    return _build_receipt(
        saved_plan_bytes=saved_plan_bytes,
        source=source,
        archive=archive,
        plan_json_bytes=plan_json_bytes,
        tofu_sha256=tofu_sha256,
        provider_sha256=provider_sha256,
        provider_package_h1=provider_package_h1,
        backend_metadata_sha256=backend_metadata_sha256,
        now=now,
    )


def _validate_archive(
    *,
    saved_plan_bytes: bytes,
    expected_saved_plan_sha256: str,
    source: base.SourceSnapshot,
) -> tuple[bytes, Mapping[str, Any], bytes, Mapping[str, Any]]:
    if base._sha256_bytes(saved_plan_bytes) != expected_saved_plan_sha256:
        raise BootstrapPlanError("saved teardown plan digest does not match evidence")
    archive = base._inspect_plan_archive(saved_plan_bytes, source=source)
    _previous_bytes, previous, _refreshed_bytes, refreshed = archive
    _validate_embedded_states(previous, refreshed)
    return archive


def _build_receipt(
    *,
    saved_plan_bytes: bytes,
    source: base.SourceSnapshot,
    archive: tuple[bytes, Mapping[str, Any], bytes, Mapping[str, Any]],
    plan_json_bytes: bytes,
    tofu_sha256: str,
    provider_sha256: str,
    provider_package_h1: str,
    backend_metadata_sha256: str,
    now: datetime,
) -> Mapping[str, Any]:
    previous_bytes, _previous, refreshed_bytes, _refreshed = archive
    plan = base._load_json_bytes(plan_json_bytes, label="derived teardown plan JSON")
    plan_timestamp = _validate_plan(plan, now=now)
    return {
        "schema_version": 1,
        "policy_id": "cosmaneura-staging-sanitizer-bootstrap-identity-teardown-plan-v1",
        "source_commit": source.commit,
        "source_tree_sha256": source.tree_sha256,
        "provider_lock_sha256": base._sha256_bytes(source.lockfile),
        "open_tofu_version": base.EXPECTED_TOFU_VERSION,
        "open_tofu_sha256": tofu_sha256,
        "open_tofu_provider_sha256": provider_sha256,
        "open_tofu_provider_package_h1": provider_package_h1,
        "saved_plan_sha256": base._sha256_bytes(saved_plan_bytes),
        "derived_plan_json_sha256": base._sha256_bytes(plan_json_bytes),
        "embedded_prior_state_sha256": base._sha256_bytes(previous_bytes),
        "embedded_refreshed_state_sha256": base._sha256_bytes(refreshed_bytes),
        "backend_metadata_sha256": backend_metadata_sha256,
        "state_lineage": EXPECTED_STATE_LINEAGE,
        "state_serial": EXPECTED_STATE_SERIAL,
        "state_boundary": dict(base.EXPECTED_STATE_BOUNDARY),
        "plan_timestamp": plan_timestamp,
        "rehearsal_id": EXPECTED_REHEARSAL_ID,
        "cleanup_not_after": EXPECTED_CLEANUP_NOT_AFTER,
        "teardown_evidence_sha256": EXPECTED_EVIDENCE_SHA256,
        "identity_resource_id": EXPECTED_RESOURCE_ID,
        "identity_client_id": EXPECTED_CLIENT_ID,
        "identity_principal_id": EXPECTED_PRINCIPAL_ID,
        "azure_delete_count": 1,
        "guard_update_count": 1,
        "create_or_replace_count": 0,
        "source_archive_bound": True,
        "remote_backend_identity_bound": True,
        "prior_state_identity_bound": True,
        "apply_authorized": False,
        "founder_apply_required": True,
        "pass": True,
    }


def create_and_validate_teardown_plan(
    *, saved_plan: Path, execution_data_dir: Path, expected_source_commit: str
) -> Mapping[str, Any]:
    try:
        if saved_plan.exists() or execution_data_dir.parent != saved_plan.parent:
            raise BootstrapPlanError(
                "saved plan must be absent beside its fresh execution directory"
            )
        recovery._require_protected_directory(
            saved_plan.parent, label="saved plan directory"
        )
        recovery._create_protected_directory(
            execution_data_dir, label="execution data directory"
        )
        source = base._source_snapshot(expected_source_commit)
        tofu = recovery._resolve_and_verify_tofu()
        if (
            recovery._executable_sha256(tofu, label="OpenTofu")
            != base.EXPECTED_TOFU_SHA256
        ):
            raise BootstrapPlanError("the OpenTofu executable digest is not pinned")
        init = recovery._run_process(
            _backend_init_arguments(tofu),
            cwd=base.REPOSITORY_ROOT,
            timeout_seconds=300,
            data_dir=execution_data_dir,
        )
        if init.returncode != 0:
            raise BootstrapPlanError(
                "the exact isolated backend could not be initialized"
            )
        backend_path = execution_data_dir / "terraform.tfstate"
        backend_before = base._read_protected_file(
            backend_path, label="backend metadata", required_mode=0o600
        )
        backend_sha256 = _validate_backend_metadata(backend_before)
        with tempfile.TemporaryDirectory(
            prefix=".bootstrap-teardown-", dir=saved_plan.parent
        ) as temporary:
            candidate = Path(temporary) / "candidate.tfplan"
            result = recovery._run_process(
                [
                    str(tofu),
                    f"-chdir={base.IDENTITY_ROOT}",
                    "plan",
                    "-input=false",
                    "-lock=true",
                    "-lock-timeout=5m",
                    "-refresh=true",
                    "-detailed-exitcode",
                    "-no-color",
                    f"-out={candidate}",
                    "-var=authorize_sanitizer_bootstrap_identity_teardown=true",
                    f"-var=sanitizer_bootstrap_teardown_rehearsal_id={EXPECTED_REHEARSAL_ID}",
                    f"-var=sanitizer_bootstrap_teardown_evidence_sha256={EXPECTED_EVIDENCE_SHA256}",
                ],  # fmt: skip
                cwd=base.REPOSITORY_ROOT,
                timeout_seconds=900,
                data_dir=execution_data_dir,
            )
            if result.returncode != 2:
                raise BootstrapPlanError("the fixed full-refresh teardown plan failed")
            plan_bytes = recovery._read_protected_file(
                candidate, label="generated teardown plan", required_mode=0o600
            )
        archive = _validate_archive(
            saved_plan_bytes=plan_bytes,
            expected_saved_plan_sha256=base._sha256_bytes(plan_bytes),
            source=source,
        )
        provider = (
            execution_data_dir
            / "providers/registry.opentofu.org/hashicorp/azurerm/4.73.0/darwin_arm64"
        )
        decoded = base._decode_saved_plan(
            plan_bytes, source=source, provider_package=provider
        )
        receipt = _build_receipt(
            saved_plan_bytes=plan_bytes, source=source, archive=archive,
            plan_json_bytes=decoded.plan_json, tofu_sha256=decoded.tofu_sha256,
            provider_sha256=decoded.provider_sha256,
            provider_package_h1=decoded.provider_package_h1,
            backend_metadata_sha256=backend_sha256, now=_utc_now(),
        )  # fmt: skip
        if base._source_snapshot(expected_source_commit) != source:
            raise BootstrapPlanError("teardown plan source changed during generation")
        if base._read_protected_file(backend_path, label="backend metadata", required_mode=0o600) != backend_before:  # fmt: skip
            raise BootstrapPlanError("backend metadata changed during plan generation")
        recovery._write_protected_file(
            saved_plan, plan_bytes, label="saved teardown plan"
        )
        return {**receipt, "execution_data_dir": str(execution_data_dir)}
    except recovery.RecoveryPlanError as exc:
        raise BootstrapPlanError(str(exc)) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--saved-plan", required=True, type=Path)
    parser.add_argument("--execution-data-dir", required=True, type=Path)
    parser.add_argument("--expected-source-commit", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        receipt = create_and_validate_teardown_plan(
            saved_plan=args.saved_plan,
            execution_data_dir=args.execution_data_dir,
            expected_source_commit=args.expected_source_commit,
        )
    except BootstrapPlanError as exc:
        print(f"bootstrap_identity_teardown_plan_refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
