from __future__ import annotations

import base64
import copy
import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/validate_staging_rehearsal_base_state_refresh_plan.py"
OUTPUTS = ROOT / "infra-staging-rehearsal/outputs.tf"
NOW = datetime(2026, 9, 9, 5, 30, tzinfo=UTC)
TOFU = Path("/trusted/tofu")
DATA_DIR = Path("/protected/tofu-data")


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("state_refresh_validator", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


refresh = _load_module()
recovery = refresh.recovery


def _storage_values() -> tuple[dict[str, Any], dict[str, Any]]:
    before: dict[str, Any] = {
        "id": refresh.STORAGE_ADDRESS,
        "name": refresh.ACCOUNT_NAME,
        "access_tier": None,
        "large_file_share_enabled": None,
        "primary_location": None,
        "primary_blob_endpoint": None,
        "primary_blob_host": None,
        "primary_dfs_endpoint": None,
        "primary_dfs_host": None,
        "primary_file_endpoint": None,
        "primary_file_host": None,
        "primary_queue_endpoint": None,
        "primary_queue_host": None,
        "primary_table_endpoint": None,
        "primary_table_host": None,
        "primary_web_endpoint": None,
        "primary_web_host": None,
        "queue_properties": [],
        "routing": [],
        "share_properties": [],
        "static_website": [],
    }
    before.update({name: None for name in refresh.STORAGE_EMPTY_STRING_FIELDS})
    before.update({name: None for name in refresh.STORAGE_SECRET_FIELDS})
    after = copy.deepcopy(before)
    after.update({name: "" for name in refresh.STORAGE_EMPTY_STRING_FIELDS})
    after.update(
        {
            "access_tier": "Hot",
            "large_file_share_enabled": False,
            "primary_location": "eastus",
            "primary_blob_endpoint": f"https://{refresh.ACCOUNT_NAME}.blob.core.windows.net/",
            "primary_blob_host": f"{refresh.ACCOUNT_NAME}.blob.core.windows.net",
            "primary_dfs_endpoint": f"https://{refresh.ACCOUNT_NAME}.dfs.core.windows.net/",
            "primary_dfs_host": f"{refresh.ACCOUNT_NAME}.dfs.core.windows.net",
            "primary_file_endpoint": f"https://{refresh.ACCOUNT_NAME}.file.core.windows.net/",
            "primary_file_host": f"{refresh.ACCOUNT_NAME}.file.core.windows.net",
            "primary_queue_endpoint": f"https://{refresh.ACCOUNT_NAME}.queue.core.windows.net/",
            "primary_queue_host": f"{refresh.ACCOUNT_NAME}.queue.core.windows.net",
            "primary_table_endpoint": f"https://{refresh.ACCOUNT_NAME}.table.core.windows.net/",
            "primary_table_host": f"{refresh.ACCOUNT_NAME}.table.core.windows.net",
            "primary_web_endpoint": f"https://{refresh.ACCOUNT_NAME}.z13.web.core.windows.net/",
            "primary_web_host": f"{refresh.ACCOUNT_NAME}.z13.web.core.windows.net",
            "share_properties": [
                {"cors_rule": [], "retention_policy": [{"days": 7}], "smb": []}
            ],
            "primary_access_key": base64.b64encode(b"a" * 64).decode(),
            "primary_blob_connection_string": "b" * 232,
            "primary_connection_string": "c" * 197,
            "secondary_access_key": base64.b64encode(b"d" * 64).decode(),
            "secondary_blob_connection_string": "",
            "secondary_connection_string": "e" * 197,
        }
    )
    return before, after


def _drift_values(address: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if address == refresh.POLICY_ADDRESS:
        before = {"id": address, "not_scopes": None}
        return before, {**before, "not_scopes": []}
    if address in refresh.ROLE_ADDRESSES:
        before = {
            "id": address,
            "assignable_scopes": [refresh.CONTROL_GROUP_ID],
            "permissions": [
                {
                    "actions": ["read"],
                    "data_actions": None,
                    "not_actions": [],
                    "not_data_actions": None,
                }
            ],
        }
        after = copy.deepcopy(before)
        after["permissions"][0]["data_actions"] = []
        after["permissions"][0]["not_data_actions"] = []
        return before, after
    if address in refresh.SUBNET_ADDRESSES:
        before = {
            "id": address,
            "service_endpoint_policy_ids": None,
            "service_endpoints": None,
        }
        return before, {
            **before,
            "service_endpoint_policy_ids": [],
            "service_endpoints": [],
        }
    if address == refresh.VNET_ADDRESS:
        before = {"id": address, "subnet": []}
        return before, {**before, "subnet": copy.deepcopy(refresh.EXPECTED_SUBNETS)}
    assert address == refresh.STORAGE_ADDRESS
    return _storage_values()


def _state(*, refreshed: bool) -> dict[str, Any]:
    resources = []
    for address in sorted(recovery.EXPECTED_EXISTING_ADDRESSES):
        base_address, _, raw_index = address.partition("[")
        resource_type, name = base_address.split(".", 1)
        before, after = (
            _drift_values(address)
            if address in refresh.EXPECTED_DRIFT_ADDRESSES
            else ({"id": address}, {"id": address})
        )
        if address == refresh.STORAGE_ADDRESS and not refreshed:
            for field in ("queue_properties", "routing", "static_website"):
                before[field] = None
            before["share_properties"] = None
        instance: dict[str, Any] = {
            "schema_version": 0,
            "attributes": after if refreshed else before,
        }
        if raw_index:
            instance["index_key"] = json.loads(raw_index[:-1])
        provider = 'provider["registry.opentofu.org/hashicorp/azurerm"]'
        if resource_type == "terraform_data":
            provider = 'provider["terraform.io/builtin/terraform"]'
        resources.append(
            {
                "mode": "managed",
                "type": resource_type,
                "name": name,
                "provider": provider,
                "instances": [instance],
            }
        )
    outputs = {
        name: {"value": f"value:{name}", "type": "string"}
        for name in refresh.EXPECTED_OUTPUT_NAMES - {"marker_store_binding"}
    }
    if refreshed:
        outputs["marker_store_binding"] = {
            "value": refresh._expected_marker_output(),
            "type": copy.deepcopy(refresh.EXPECTED_MARKER_OUTPUT_TYPE),
        }
    return {
        "version": 4,
        "terraform_version": recovery.EXPECTED_TOFU_VERSION,
        "serial": refresh.PRE_REFRESH_SERIAL,
        "lineage": recovery.EXPECTED_LINEAGE,
        "outputs": outputs,
        "resources": resources,
        "check_results": [],
    }


def _providers() -> dict[str, Any]:
    return {
        "azurerm": {
            "full_name": "registry.opentofu.org/hashicorp/azurerm",
            "version_constraint": "4.73.0",
            "expressions": {
                "features": [
                    {"storage": [{"data_plane_available": {"constant_value": False}}]}
                ]
            },
        },
        "azapi": {
            "full_name": "registry.opentofu.org/azure/azapi",
            "version_constraint": "2.1.0",
            "expressions": {},
        },
        "terraform": {
            "full_name": "terraform.io/builtin/terraform",
            "version_constraint": None,
            "expressions": None,
        },
    }


def _plan() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    before_state = _state(refreshed=False)
    after_state = _state(refreshed=True)
    before_attributes = refresh._state_attributes(before_state)
    after_attributes = refresh._state_attributes(after_state)
    drift = []
    for address in sorted(refresh.EXPECTED_DRIFT_ADDRESSES):
        before = copy.deepcopy(before_attributes[address])
        if address == refresh.STORAGE_ADDRESS:
            for field in (
                "queue_properties",
                "routing",
                "share_properties",
                "static_website",
            ):
                before[field] = []
        after = copy.deepcopy(after_attributes[address])
        resource_type, name = address.removesuffix("[0]").split(".", 1)
        metadata = refresh._expected_change_metadata(
            address, before=before, after=after
        )
        drift.append(
            {
                "address": address,
                "mode": "managed",
                "type": resource_type,
                "name": name,
                "index": 0,
                "provider_name": "registry.opentofu.org/hashicorp/azurerm",
                "change": {
                    "before": before,
                    "after": after,
                    **copy.deepcopy(metadata),
                },
            }
        )
    before_outputs = refresh._state_output_values(before_state)
    after_outputs = refresh._state_output_values(after_state)
    output_changes = {}
    for name in sorted(refresh.EXPECTED_OUTPUT_NAMES):
        if name == "marker_store_binding":
            output_changes[name] = {
                "actions": ["create"],
                "before": None,
                "after": refresh._expected_marker_output(),
                "after_unknown": False,
                "before_sensitive": False,
                "after_sensitive": False,
            }
        else:
            output_changes[name] = {
                "actions": ["no-op"],
                "before": before_outputs[name],
                "after": after_outputs[name],
                "after_unknown": False,
                "before_sensitive": False,
                "after_sensitive": False,
            }
    prior_resources = [
        {"address": address} for address in sorted(recovery.EXPECTED_EXISTING_ADDRESSES)
    ]
    plan = {
        "format_version": recovery.EXPECTED_FORMAT_VERSION,
        "terraform_version": recovery.EXPECTED_TOFU_VERSION,
        "timestamp": NOW.isoformat().replace("+00:00", "Z"),
        "errored": False,
        "variables": {
            name: {"value": value}
            for name, value in recovery.EXPECTED_VARIABLES.items()
        },
        "checks": [{"status": "pass", "instances": []}],
        "configuration": {"provider_config": _providers()},
        "prior_state": {"values": {"root_module": {"resources": prior_resources}}},
        "planned_values": {"root_module": {"resources": []}},
        "relevant_attributes": [
            {"resource": resource, "attribute": list(attribute)}
            for resource, attribute in sorted(refresh.EXPECTED_RELEVANT_ATTRIBUTES)
        ],
        "resource_drift": drift,
        "output_changes": output_changes,
    }
    return plan, before_state, after_state


def test_refresh_plan_is_exactly_state_only() -> None:
    plan, before, after = _plan()
    timestamp = refresh._validate_plan(
        plan, external_state=before, embedded_state=after, now=NOW
    )
    assert timestamp == "2026-09-09T05:30:00Z"
    assert plan.get("resource_changes") is None
    assert len(plan["resource_drift"]) == 10


def test_refresh_plan_rejects_resource_actions_and_drift_expansion() -> None:
    plan, before, after = _plan()
    plan["resource_changes"] = [
        {
            "address": "azurerm_storage_account.marker[0]",
            "change": {"actions": ["update"]},
        }
    ]
    with pytest.raises(refresh.RefreshPlanError, match="Azure resource action"):
        refresh._validate_plan(
            plan, external_state=before, embedded_state=after, now=NOW
        )

    plan, before, after = _plan()
    plan["resource_drift"].append(copy.deepcopy(plan["resource_drift"][0]))
    plan["resource_drift"][-1]["address"] = "azurerm_resource_group.forbidden[0]"
    with pytest.raises(refresh.RefreshPlanError, match="address set"):
        refresh._validate_plan(
            plan, external_state=before, embedded_state=after, now=NOW
        )


@pytest.mark.parametrize(
    "target", ["drift", "sensitivity", "unknown", "output", "top_level"]
)
def test_refresh_plan_rejects_unreviewed_metadata(target: str) -> None:
    plan, before, after = _plan()
    if target == "drift":
        plan["resource_drift"][0]["change"]["unreviewed"] = True
    elif target in {"sensitivity", "unknown"}:
        storage = next(
            item
            for item in plan["resource_drift"]
            if item["address"] == refresh.STORAGE_ADDRESS
        )
        if target == "sensitivity":
            storage["change"]["after_sensitive"]["unexpected"] = False
        else:
            storage["change"]["after_unknown"]["unexpected"] = False
    elif target == "output":
        plan["output_changes"]["control_plane_enabled"]["unreviewed"] = True
    else:
        plan["unreviewed"] = False
    with pytest.raises(refresh.RefreshPlanError):
        refresh._validate_plan(
            plan, external_state=before, embedded_state=after, now=NOW
        )


@pytest.mark.parametrize("target", ["instance_private", "output_type", "top_level"])
def test_refresh_plan_rejects_embedded_state_metadata_tampering(target: str) -> None:
    plan, before, after = _plan()
    if target == "instance_private":
        after["resources"][0]["instances"][0]["private"] = "unreviewed"
    elif target == "output_type":
        after["outputs"]["marker_store_binding"]["type"] = "dynamic"
    else:
        after["unreviewed"] = False
    with pytest.raises(refresh.RefreshPlanError, match="embedded refreshed state"):
        refresh._validate_plan(
            plan, external_state=before, embedded_state=after, now=NOW
        )


def test_pre_refresh_state_digest_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state(refreshed=False)
    state_bytes = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    monkeypatch.setattr(
        refresh,
        "EXPECTED_PRE_REFRESH_STATE_SHA256",
        recovery._sha256_bytes(state_bytes),
    )
    refresh._validate_pre_refresh_state_digest(state_bytes)

    unrelated = next(
        resource
        for resource in state["resources"]
        if resource["type"] == "azurerm_resource_group"
    )
    unrelated["instances"][0]["attributes"]["unreviewed"] = True
    changed = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(refresh.RefreshPlanError, match="reviewed snapshot"):
        refresh._validate_pre_refresh_state_digest(changed)


def test_digest_mismatch_precedes_provider_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[bool] = []
    monkeypatch.setattr(recovery, "_reject_symlink_components", lambda *_, **__: None)
    monkeypatch.setattr(recovery, "_require_protected_directory", lambda *_, **__: None)
    monkeypatch.setattr(recovery, "_read_protected_file", lambda *_, **__: b"wrong")
    monkeypatch.setattr(
        recovery, "_resolve_and_verify_tofu", lambda: called.append(True)
    )
    operations = (
        lambda: refresh.create_and_validate_state_refresh_plan(
            saved_plan=tmp_path / "plan",
            state_json=tmp_path / "state",
            expected_source_commit="a" * 40,
        ),
        lambda: refresh.revalidate_state_refresh_plan(
            saved_plan=tmp_path / "plan",
            state_json=tmp_path / "state",
            execution_dir=tmp_path / "execution",
            expected_source_commit="a" * 40,
            expected_saved_plan_sha256="b" * 64,
        ),
    )
    for operation in operations:
        with pytest.raises(refresh.RefreshPlanError, match="reviewed snapshot"):
            operation()
    assert called == []


@pytest.mark.parametrize("target", ["storage", "output", "mask", "embedded"])
def test_boolean_number_coercion_fails_closed(target: str) -> None:
    plan, before, after = _plan()
    if target == "storage":
        storage = next(
            item
            for item in plan["resource_drift"]
            if item["address"] == refresh.STORAGE_ADDRESS
        )
        storage["change"]["after"]["large_file_share_enabled"] = 0
        refresh._state_attributes(after)[refresh.STORAGE_ADDRESS][
            "large_file_share_enabled"
        ] = 0
    elif target == "output":
        plan["output_changes"]["control_plane_enabled"]["after_sensitive"] = 0
    elif target == "mask":
        subnet = next(
            item
            for item in plan["resource_drift"]
            if item["address"] in refresh.SUBNET_ADDRESSES
        )
        subnet["change"]["after_sensitive"]["address_prefixes"][0] = 0
    else:
        before["resources"][0]["instances"][0]["private"] = False
        after["resources"][0]["instances"][0]["private"] = 0
    with pytest.raises(refresh.RefreshPlanError):
        refresh._validate_plan(
            plan, external_state=before, embedded_state=after, now=NOW
        )

    remote = copy.deepcopy(before)
    before["outputs"]["control_plane_enabled"]["value"] = False
    remote["outputs"]["control_plane_enabled"]["value"] = 0
    assert not refresh._json_equal(
        refresh._state_binding_projection(before),
        refresh._state_binding_projection(remote),
    )


@pytest.mark.parametrize("target", ["role", "storage", "output"])
def test_refresh_plan_rejects_normalization_tampering(target: str) -> None:
    plan, before, after = _plan()
    if target == "role":
        item = next(
            item
            for item in plan["resource_drift"]
            if item["address"] == "azurerm_role_definition.control_reader[0]"
        )
        item["change"]["after"]["permissions"][0]["actions"] = ["write"]
        after_attributes = refresh._state_attributes(after)
        after_attributes[item["address"]]["permissions"][0]["actions"] = ["write"]
    elif target == "storage":
        item = next(
            item
            for item in plan["resource_drift"]
            if item["address"] == refresh.STORAGE_ADDRESS
        )
        item["change"]["after"]["access_tier"] = "Cool"
        refresh._state_attributes(after)[refresh.STORAGE_ADDRESS]["access_tier"] = (
            "Cool"
        )
    else:
        plan["output_changes"]["marker_store_binding"]["after"][
            "private_endpoint_id"
        ] = "unexpected"
        refresh._state_output_values(after)["marker_store_binding"][
            "private_endpoint_id"
        ] = "unexpected"
    with pytest.raises(refresh.RefreshPlanError):
        refresh._validate_plan(
            plan, external_state=before, embedded_state=after, now=NOW
        )


def test_state_binding_ignores_only_check_result_serialization() -> None:
    before = _state(refreshed=False)
    after = copy.deepcopy(before)
    after["check_results"] = [{"status": "pass"}]
    project = refresh._state_binding_projection
    assert refresh._json_equal(project(before), project(after))
    after["serial"] = 9
    assert not refresh._json_equal(project(before), project(after))


def test_fixed_generation_uses_refresh_only_and_no_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = b"opaque-refresh-plan"

    def fake_run(
        arguments: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        output = Path(
            next(
                value for value in arguments if value.startswith("-out=")
            ).removeprefix("-out=")
        )
        output.write_bytes(expected)
        output.chmod(0o600)
        assert "-refresh-only" in arguments
        assert "-refresh=true" in arguments
        assert not any(
            value.startswith(("-target", "-replace", "-refresh=false"))
            for value in arguments
        )
        assert kwargs["data_dir"] == DATA_DIR
        return subprocess.CompletedProcess(arguments, 2, b"", b"")

    monkeypatch.setattr(recovery, "_run_process", fake_run)
    assert (
        refresh._generate_saved_plan(
            tofu=TOFU, output_parent=tmp_path, data_dir=DATA_DIR
        )
        == expected
    )


def test_partial_state_output_uses_closed_null_fallbacks() -> None:
    source = OUTPUTS.read_text(encoding="utf-8")
    assert "try(azurerm_private_endpoint.marker[0].id, null)" in source
    for expression in (
        "azurerm_role_assignment.marker_provision_cleanup[0].id",
        'azurerm_role_assignment.marker_read["cleanup_intent"].id',
        'azurerm_role_assignment.marker_read["cleanup_submission"].id',
    ):
        assert f"{expression},\n        null," in source


def test_recovery_gate_requires_post_refresh_serial() -> None:
    assert refresh.PRE_REFRESH_SERIAL == 8
    assert refresh.POST_REFRESH_SERIAL == 9
    assert recovery.EXPECTED_RECOVERY_SERIAL == 9
