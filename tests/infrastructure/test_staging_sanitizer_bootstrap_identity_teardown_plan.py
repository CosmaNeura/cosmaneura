from __future__ import annotations

import hashlib
import io
import json
import zipfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from scripts import (
    validate_staging_sanitizer_bootstrap_identity_teardown_plan as validator,
)

ROOT = Path(__file__).resolve().parents[2]
IDENTITY_ROOT = ROOT / "infra-staging-sanitizer-bootstrap-identity"
NOW = datetime(2026, 9, 8, 15, 0, tzinfo=UTC)
PLAN_TIMESTAMP = "2026-09-08T14:45:00Z"


def _raw_output(value: Any) -> dict[str, Any]:
    return {"value": value, "type": "fixture-type"}


def _raw_uami() -> dict[str, Any]:
    return {
        "mode": "managed",
        "type": "azurerm_user_assigned_identity",
        "name": "sanitizer_bootstrap",
        "provider": 'provider["registry.opentofu.org/hashicorp/azurerm"]',
        "instances": [
            {
                "index_key": 0,
                "schema_version": 1,
                "attributes": validator._uami_values(),
                "sensitive_attributes": [],
                "private": "opaque-provider-state",
                "dependencies": [
                    "data.azurerm_resource_group.control",
                    "terraform_data.activation_guard",
                ],
            }
        ],
    }


def _raw_guard() -> dict[str, Any]:
    old = validator._old_guard_values()
    return {
        "mode": "managed",
        "type": "terraform_data",
        "name": "activation_guard",
        "provider": 'provider["terraform.io/builtin/terraform"]',
        "instances": [
            {
                "index_key": 0,
                "schema_version": 0,
                "attributes": {
                    "id": old["id"],
                    "input": _raw_output(old["input"]),
                    "output": _raw_output(old["output"]),
                    "triggers_replace": _raw_output(old["triggers_replace"]),
                },
                "sensitive_attributes": [],
            }
        ],
    }


def _raw_resource_group() -> dict[str, Any]:
    return {
        "mode": "data",
        "type": "azurerm_resource_group",
        "name": "control",
        "provider": 'provider["registry.opentofu.org/hashicorp/azurerm"]',
        "instances": [
            {
                "index_key": 0,
                "schema_version": 0,
                "attributes": {
                    "id": (
                        f"/subscriptions/{validator.base.EXPECTED_SUBSCRIPTION_ID}"
                        f"/resourceGroups/{validator.base.EXPECTED_RESOURCE_GROUP}"
                    ),
                    "location": validator.base.EXPECTED_LOCATION,
                    "managed_by": "",
                    "name": validator.base.EXPECTED_RESOURCE_GROUP,
                    "tags": dict(validator.base.EXPECTED_CONTROL_GROUP_TAGS),
                    "timeouts": None,
                },
                "sensitive_attributes": [],
            }
        ],
    }


def _raw_state(*, before_refresh: bool) -> dict[str, Any]:
    outputs = {
        "state_boundary": _raw_output(dict(validator.base.EXPECTED_STATE_BOUNDARY)),
        (
            "sanitizer_bootstrap_identity_foundation"
            if before_refresh
            else "sanitizer_bootstrap_identity_teardown"
        ): _raw_output(
            validator._foundation_value()
            if before_refresh
            else validator._teardown_value()
        ),
    }
    resources = [_raw_uami(), _raw_guard()]
    if before_refresh:
        resources.insert(0, _raw_resource_group())
    return {
        "version": 4,
        "terraform_version": validator.base.EXPECTED_TOFU_VERSION,
        "serial": 0 if before_refresh else validator.EXPECTED_STATE_SERIAL,
        "lineage": "" if before_refresh else validator.EXPECTED_STATE_LINEAGE,
        "outputs": outputs,
        "resources": resources,
        "check_results": None,
    }


def _plan_uami() -> dict[str, Any]:
    return {
        "address": "azurerm_user_assigned_identity.sanitizer_bootstrap[0]",
        "mode": "managed",
        "type": "azurerm_user_assigned_identity",
        "name": "sanitizer_bootstrap",
        "index": 0,
        "provider_name": "registry.opentofu.org/hashicorp/azurerm",
        "schema_version": 1,
        "values": validator._uami_values(),
        "sensitive_values": {"tags": {}},
        "depends_on": [
            "data.azurerm_resource_group.control",
            "terraform_data.activation_guard",
        ],
    }


def _plan_guard() -> dict[str, Any]:
    return {
        "address": "terraform_data.activation_guard[0]",
        "mode": "managed",
        "type": "terraform_data",
        "name": "activation_guard",
        "index": 0,
        "provider_name": "terraform.io/builtin/terraform",
        "schema_version": 0,
        "values": validator._old_guard_values(),
        "sensitive_values": {"input": {}, "output": {}, "triggers_replace": {}},
    }


def _teardown_output_record() -> dict[str, Any]:
    return {
        "sensitive": False,
        "value": validator._teardown_value(),
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
    }


def _boundary_output_record() -> dict[str, Any]:
    return {
        "sensitive": False,
        "value": dict(validator.base.EXPECTED_STATE_BOUNDARY),
        "type": [
            "object",
            {
                "container": "string",
                "key": "string",
                "resource_group": "string",
                "storage_account": "string",
            },
        ],
    }


def _change(actions: list[str], before: Any, after: Any) -> dict[str, Any]:
    return {
        "actions": actions,
        "before": before,
        "after": after,
        "after_unknown": False,
        "before_sensitive": False,
        "after_sensitive": False,
    }


def _valid_plan() -> dict[str, Any]:
    old_guard = validator._old_guard_values()
    return {
        "format_version": validator.base.EXPECTED_FORMAT_VERSION,
        "terraform_version": validator.base.EXPECTED_TOFU_VERSION,
        "applyable": None,
        "complete": None,
        "timestamp": PLAN_TIMESTAMP,
        "errored": False,
        "variables": {
            "authorize_sanitizer_bootstrap_identity_teardown": {"value": True},
            "enable_sanitizer_bootstrap_identity": {"value": False},
            "sanitizer_bootstrap_identity_plan_approved": {"value": False},
            "sanitizer_bootstrap_lifetime_hours": {"value": None},
            "sanitizer_bootstrap_rehearsal_id": {"value": None},
            "sanitizer_bootstrap_teardown_evidence_sha256": {
                "value": validator.EXPECTED_EVIDENCE_SHA256
            },
            "sanitizer_bootstrap_teardown_rehearsal_id": {
                "value": validator.EXPECTED_REHEARSAL_ID
            },
        },
        "checks": [
            {
                "address": {"to_display": name},
                "status": "pass",
                "instances": [{"status": "pass"}],
            }
            for name in (
                "terraform_data.activation_guard",
                "var.sanitizer_bootstrap_lifetime_hours",
                "var.sanitizer_bootstrap_rehearsal_id",
                "var.sanitizer_bootstrap_teardown_evidence_sha256",
                "var.sanitizer_bootstrap_teardown_rehearsal_id",
            )
        ],
        "configuration": {
            "provider_config": {
                "azurerm": {
                    "full_name": "registry.opentofu.org/hashicorp/azurerm",
                    "version_constraint": "4.73.0",
                    "expressions": {
                        "features": [{}],
                        "resource_provider_registrations": {"constant_value": "none"},
                        "subscription_id": {
                            "references": ["local.approved_subscription_id"]
                        },
                        "tenant_id": {"references": ["local.approved_tenant_id"]},
                    },
                },
                "terraform": {"full_name": "terraform.io/builtin/terraform"},
            },
            "root_module": {
                "resources": [
                    {
                        "address": address,
                        "count_expression": {"constant_value": count},
                    }
                    for address, count in (
                        ("azurerm_user_assigned_identity.sanitizer_bootstrap", 0),
                        ("terraform_data.activation_guard", 1),
                        ("data.azurerm_resource_group.control", 0),
                    )
                ],
                "variables": {name: {} for name in validator.EXPECTED_VARIABLES},
                "outputs": {
                    "sanitizer_bootstrap_identity_foundation": {},
                    "sanitizer_bootstrap_identity_teardown": {},
                    "state_boundary": {},
                },
            },
        },
        "prior_state": {
            "format_version": "1.0",
            "terraform_version": validator.base.EXPECTED_TOFU_VERSION,
            "values": {
                "outputs": {
                    "sanitizer_bootstrap_identity_teardown": _teardown_output_record(),
                    "state_boundary": _boundary_output_record(),
                },
                "root_module": {"resources": [_plan_uami(), _plan_guard()]},
            },
        },
        "resource_changes": [
            {
                "address": "azurerm_user_assigned_identity.sanitizer_bootstrap[0]",
                "mode": "managed",
                "type": "azurerm_user_assigned_identity",
                "name": "sanitizer_bootstrap",
                "index": 0,
                "provider_name": "registry.opentofu.org/hashicorp/azurerm",
                "action_reason": "delete_because_count_index",
                "change": {
                    "actions": ["delete"],
                    "before": validator._uami_values(),
                    "after": None,
                    "after_unknown": {},
                    "before_sensitive": {"tags": {}},
                    "after_sensitive": False,
                },
            },
            {
                "address": "terraform_data.activation_guard[0]",
                "mode": "managed",
                "type": "terraform_data",
                "name": "activation_guard",
                "index": 0,
                "provider_name": "terraform.io/builtin/terraform",
                "change": {
                    "actions": ["update"],
                    "before": old_guard,
                    "after": validator._new_guard_values(),
                    "after_unknown": {
                        "input": {},
                        "output": True,
                        "triggers_replace": {},
                    },
                    "before_sensitive": {
                        "input": {},
                        "output": {},
                        "triggers_replace": {},
                    },
                    "after_sensitive": {
                        "input": {},
                        "output": {},
                        "triggers_replace": {},
                    },
                },
            },
        ],
        "output_changes": {
            "sanitizer_bootstrap_identity_foundation": _change(
                ["update"], validator._foundation_value(), None
            ),
            "sanitizer_bootstrap_identity_teardown": _change(
                ["create"], None, validator._teardown_value()
            ),
            "state_boundary": _change(
                ["no-op"],
                dict(validator.base.EXPECTED_STATE_BOUNDARY),
                dict(validator.base.EXPECTED_STATE_BOUNDARY),
            ),
        },
    }


def _source_snapshot() -> Any:
    terraform_files = {
        path.name: path.read_bytes() for path in sorted(IDENTITY_ROOT.glob("*.tf"))
    }
    return validator.base.SourceSnapshot(
        commit="a" * 40,
        terraform_files=terraform_files,
        lockfile=(IDENTITY_ROOT / ".terraform.lock.hcl").read_bytes(),
        tree_sha256="b" * 64,
    )


def _saved_plan(previous: dict[str, Any], refreshed: dict[str, Any]) -> bytes:
    source = _source_snapshot()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("tfplan", b"opaque-saved-plan")
        archive.writestr("tfstate-prev", json.dumps(previous))
        archive.writestr("tfstate", json.dumps(refreshed))
        archive.writestr("tfconfig/modules.json", '[{"Key":"","Dir":"."}]')
        archive.writestr(".terraform.lock.hcl", source.lockfile)
        for name, value in source.terraform_files.items():
            archive.writestr(f"tfconfig/m-/{name}", value)
    return output.getvalue()


def _validate(
    *,
    plan: dict[str, Any] | None = None,
    previous: dict[str, Any] | None = None,
    refreshed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    saved = _saved_plan(
        previous or _raw_state(before_refresh=True),
        refreshed or _raw_state(before_refresh=False),
    )
    return dict(
        validator._validate_artifacts(
            saved_plan_bytes=saved,
            expected_saved_plan_sha256=hashlib.sha256(saved).hexdigest(),
            source=_source_snapshot(),
            plan_json_bytes=json.dumps(plan or _valid_plan()).encode(),
            tofu_sha256="c" * 64,
            provider_sha256="d" * 64,
            provider_package_h1=validator.base.EXPECTED_PROVIDER_PACKAGE_H1,
            backend_metadata_sha256="e" * 64,
            now=NOW,
        )
    )


def test_exact_teardown_is_bound_but_never_authorizes_apply() -> None:
    receipt = _validate()

    assert receipt["policy_id"].endswith("teardown-plan-v1")
    assert receipt["state_lineage"] == validator.EXPECTED_STATE_LINEAGE
    assert receipt["state_serial"] == validator.EXPECTED_STATE_SERIAL
    assert receipt["identity_resource_id"] == validator.EXPECTED_RESOURCE_ID
    assert receipt["azure_delete_count"] == 1
    assert receipt["guard_update_count"] == 1
    assert receipt["create_or_replace_count"] == 0
    assert receipt["remote_backend_identity_bound"] is True
    assert receipt["apply_authorized"] is False
    assert receipt["founder_apply_required"] is True
    assert receipt["pass"] is True


def test_cli_string_boolean_is_normalized() -> None:
    plan = _valid_plan()
    plan["variables"]["authorize_sanitizer_bootstrap_identity_teardown"]["value"] = (
        "true"
    )
    assert validator._validate_plan(plan, now=NOW) == PLAN_TIMESTAMP


@pytest.mark.parametrize(
    "mutation",
    (
        lambda plan: plan["variables"][
            "sanitizer_bootstrap_teardown_evidence_sha256"
        ].update(value="sha256:" + "0" * 64),
        lambda plan: plan["resource_changes"][0]["change"]["before"].update(
            id="/subscriptions/wrong"
        ),
        lambda plan: plan["resource_changes"].pop(),
        lambda plan: plan["resource_changes"][0]["change"].update(actions=["create"]),
        lambda plan: plan["prior_state"]["values"]["root_module"]["resources"][0][
            "values"
        ].update(principal_id="00000000-0000-0000-0000-000000000000"),
        lambda plan: plan["output_changes"]["sanitizer_bootstrap_identity_teardown"][
            "after"
        ].update(apply_authorized=True),
    ),
)
def test_security_relevant_plan_mutations_refuse(mutation: Any) -> None:
    plan = _valid_plan()
    mutation(plan)

    with pytest.raises(validator.BootstrapPlanError):
        _validate(plan=plan)


def test_embedded_state_lineage_serial_and_uami_are_exact() -> None:
    for field, value in (
        ("lineage", "11111111-1111-4111-8111-111111111111"),
        ("serial", validator.EXPECTED_STATE_SERIAL),
    ):
        previous = _raw_state(before_refresh=True)
        previous[field] = value
        with pytest.raises(validator.BootstrapPlanError, match="lineage or serial"):
            _validate(previous=previous)

    for field, value in (
        ("lineage", ""),
        ("serial", 0),
    ):
        refreshed = _raw_state(before_refresh=False)
        refreshed[field] = value
        with pytest.raises(validator.BootstrapPlanError, match="lineage or serial"):
            _validate(refreshed=refreshed)

    previous = _raw_state(before_refresh=True)
    previous["resources"][1]["instances"][0]["attributes"]["client_id"] = (
        "00000000-0000-0000-0000-000000000000"
    )
    with pytest.raises(validator.BootstrapPlanError, match="UAMI values"):
        _validate(previous=previous)

    refreshed = _raw_state(before_refresh=False)
    refreshed["resources"][0]["instances"][0]["attributes"]["id"] = (
        "/subscriptions/wrong"
    )
    with pytest.raises(validator.BootstrapPlanError, match="UAMI values"):
        _validate(refreshed=refreshed)


def test_saved_plan_digest_is_exact() -> None:
    saved = _saved_plan(
        _raw_state(before_refresh=True), _raw_state(before_refresh=False)
    )
    with pytest.raises(validator.BootstrapPlanError, match="digest"):
        validator._validate_artifacts(
            saved_plan_bytes=saved,
            expected_saved_plan_sha256="0" * 64,
            source=_source_snapshot(),
            plan_json_bytes=json.dumps(_valid_plan()).encode(),
            tofu_sha256="c" * 64,
            provider_sha256="d" * 64,
            provider_package_h1=validator.base.EXPECTED_PROVIDER_PACKAGE_H1,
            backend_metadata_sha256="e" * 64,
            now=NOW,
        )


def test_backend_metadata_rejects_redirects_and_credentials() -> None:
    config = {**validator.EXPECTED_BACKEND_CONFIG, "access_key": None}
    assert validator._validate_backend_metadata(
        json.dumps({"backend": {"type": "azurerm", "config": config}}).encode()
    )

    for field, value in (
        ("key", "other.tfstate"),
        ("tenant_id", validator.base.EXPECTED_TENANT_ID),
        ("access_key", "secret"),
    ):
        changed = deepcopy(config)
        changed[field] = value
        with pytest.raises(validator.BootstrapPlanError, match="backend"):
            validator._validate_backend_metadata(
                json.dumps({"backend": {"type": "azurerm", "config": changed}}).encode()
            )

    missing_tenant = deepcopy(config)
    del missing_tenant["tenant_id"]
    with pytest.raises(validator.BootstrapPlanError, match="backend"):
        validator._validate_backend_metadata(
            json.dumps(
                {"backend": {"type": "azurerm", "config": missing_tenant}}
            ).encode()
        )


def test_backend_init_omits_only_the_redundant_tenant_selector() -> None:
    arguments = validator._backend_init_arguments(Path("/trusted/tofu"))
    supplied = {
        item.removeprefix("-backend-config=")
        for item in arguments
        if item.startswith("-backend-config=")
    }

    assert supplied == {
        f"{key}={str(value).lower()}"
        for key, value in validator.EXPECTED_BACKEND_CONFIG.items()
        if value is not None
    }
    assert not any(item.startswith("tenant_id=") for item in supplied)


def test_targeted_destroy_missing_guard_and_outputs_refuses() -> None:
    plan = _valid_plan()
    plan["resource_changes"] = plan["resource_changes"][:1]
    plan["output_changes"] = None

    with pytest.raises(validator.BootstrapPlanError, match="action set"):
        validator._validate_plan(plan, now=NOW)


def test_targeted_normal_plan_without_complete_outputs_refuses() -> None:
    plan = _valid_plan()
    plan["output_changes"] = None

    with pytest.raises(validator.BootstrapPlanError, match="output change set"):
        validator._validate_plan(plan, now=NOW)
