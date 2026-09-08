from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import zipfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/validate_staging_sanitizer_bootstrap_identity_plan.py"
IDENTITY_ROOT = ROOT / "infra-staging-sanitizer-bootstrap-identity"
NOW = datetime(2026, 9, 8, 4, 15, tzinfo=UTC)
PLAN_TIMESTAMP = "2026-09-08T04:01:49Z"
CLEANUP = "2026-09-08T10:01:49Z"
REHEARSAL_ID = "abcdef123456"
LINEAGE = "a0c8b939-2fb2-0604-3e70-d925aeee5950"
SERIAL = 1
TOFU = Path("/trusted/tofu")


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bootstrap_plan_validator", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validator = _load_module()


def _source_snapshot() -> Any:
    terraform_files = {
        path.name: path.read_bytes() for path in sorted(IDENTITY_ROOT.glob("*.tf"))
    }
    lockfile = (IDENTITY_ROOT / ".terraform.lock.hcl").read_bytes()
    files = {
        **terraform_files,
        ".terraform.lock.hcl": lockfile,
        str(SCRIPT.relative_to(ROOT)): SCRIPT.read_bytes(),
    }
    return validator.SourceSnapshot(
        commit="a" * 40,
        terraform_files=terraform_files,
        lockfile=lockfile,
        tree_sha256=validator._canonical_file_set_sha256(files),
    )


def _empty_state() -> dict[str, Any]:
    return {
        "version": 4,
        "terraform_version": validator.EXPECTED_TOFU_VERSION,
        "serial": SERIAL,
        "lineage": LINEAGE,
        "outputs": {},
        "resources": [],
        "check_results": None,
    }


def _state_boundary(*, plan_json: bool) -> dict[str, Any]:
    value: dict[str, Any] = {
        "value": dict(validator.EXPECTED_STATE_BOUNDARY),
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
    if plan_json:
        value["sensitive"] = False
    return value


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
                        "/subscriptions/"
                        f"{validator.EXPECTED_SUBSCRIPTION_ID}/resourceGroups/"
                        f"{validator.EXPECTED_RESOURCE_GROUP}"
                    ),
                    "location": validator.EXPECTED_LOCATION,
                    "managed_by": "",
                    "name": validator.EXPECTED_RESOURCE_GROUP,
                    "tags": dict(validator.EXPECTED_CONTROL_GROUP_TAGS),
                    "timeouts": None,
                },
                "sensitive_attributes": [],
            }
        ],
    }


def _refreshed_state() -> dict[str, Any]:
    state = _empty_state()
    state["outputs"] = {"state_boundary": _state_boundary(plan_json=False)}
    state["resources"] = [_raw_resource_group()]
    return state


def _plan_resource_group() -> dict[str, Any]:
    raw = _raw_resource_group()
    instance = raw["instances"][0]
    return {
        "address": "data.azurerm_resource_group.control[0]",
        "mode": "data",
        "type": "azurerm_resource_group",
        "name": "control",
        "index": 0,
        "provider_name": "registry.opentofu.org/hashicorp/azurerm",
        "schema_version": 0,
        "values": deepcopy(instance["attributes"]),
        "sensitive_values": {"tags": {}},
    }


def _expected_tags() -> dict[str, str]:
    return {
        **validator.EXPECTED_BASE_TAGS,
        "cleanup-not-after": CLEANUP,
        "rehearsal-id": REHEARSAL_ID,
    }


def _valid_plan() -> dict[str, Any]:
    uami_after = {
        "isolation_scope": None,
        "location": validator.EXPECTED_LOCATION,
        "name": validator.EXPECTED_IDENTITY_NAME,
        "resource_group_name": validator.EXPECTED_RESOURCE_GROUP,
        "tags": _expected_tags(),
        "timeouts": None,
    }
    lifecycle = {
        "cleanup_not_after": CLEANUP,
        "lifetime_hours": 6,
        "rehearsal_id": REHEARSAL_ID,
    }
    foundation_after = {
        "attached_job_resource_ids": [],
        "cleanup_not_after": CLEANUP,
        "database_authority_bound": False,
        "destruction_protected": True,
        "exact_ids_authenticated": False,
        "federated_credential_ids": [],
        "lifecycle": "ephemeral",
        "name": validator.EXPECTED_IDENTITY_NAME,
        "provider_authority_bound": False,
        "rehearsal_id": REHEARSAL_ID,
        "role_assignment_ids": [],
        "single_use_job_bound": False,
        "workflow_binding_implemented": False,
    }
    root_variables = {name: {"type": "string"} for name in validator.EXPECTED_VARIABLES}
    root_outputs = {
        "sanitizer_bootstrap_identity_foundation": {"expression": {}},
        "state_boundary": {"expression": {}},
    }
    return {
        "format_version": validator.EXPECTED_FORMAT_VERSION,
        "terraform_version": validator.EXPECTED_TOFU_VERSION,
        "applyable": True,
        "complete": True,
        "timestamp": PLAN_TIMESTAMP,
        "errored": False,
        "variables": {
            "enable_sanitizer_bootstrap_identity": {"value": "true"},
            "sanitizer_bootstrap_identity_plan_approved": {"value": "true"},
            "sanitizer_bootstrap_lifetime_hours": {"value": "6"},
            "sanitizer_bootstrap_rehearsal_id": {"value": REHEARSAL_ID},
        },
        "checks": [
            {
                "address": {
                    "to_display": ("azurerm_user_assigned_identity.sanitizer_bootstrap")
                },
                "status": "unknown",
                "instances": [{"status": "unknown"}],
            },
            {
                "address": {"to_display": "terraform_data.activation_guard"},
                "status": "pass",
                "instances": [{"status": "pass"}],
            },
            {
                "address": {"to_display": "var.sanitizer_bootstrap_lifetime_hours"},
                "status": "pass",
                "instances": [{"status": "pass"}],
            },
            {
                "address": {"to_display": "var.sanitizer_bootstrap_rehearsal_id"},
                "status": "pass",
                "instances": [{"status": "pass"}],
            },
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
                "terraform": {
                    "full_name": "terraform.io/builtin/terraform",
                },
            },
            "root_module": {
                "resources": [
                    {"address": ("azurerm_user_assigned_identity.sanitizer_bootstrap")},
                    {"address": "terraform_data.activation_guard"},
                    {"address": "data.azurerm_resource_group.control"},
                ],
                "variables": root_variables,
                "outputs": root_outputs,
            },
        },
        "prior_state": {
            "format_version": "1.0",
            "terraform_version": validator.EXPECTED_TOFU_VERSION,
            "values": {
                "outputs": {"state_boundary": _state_boundary(plan_json=True)},
                "root_module": {"resources": [_plan_resource_group()]},
            },
        },
        "resource_changes": [
            {
                "address": ("azurerm_user_assigned_identity.sanitizer_bootstrap[0]"),
                "mode": "managed",
                "type": "azurerm_user_assigned_identity",
                "name": "sanitizer_bootstrap",
                "index": 0,
                "provider_name": "registry.opentofu.org/hashicorp/azurerm",
                "change": {
                    "actions": ["create"],
                    "before": None,
                    "after": uami_after,
                    "after_unknown": {
                        "client_id": True,
                        "id": True,
                        "principal_id": True,
                        "tags": {},
                        "tenant_id": True,
                    },
                    "before_sensitive": False,
                    "after_sensitive": {"tags": {}},
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
                    "actions": ["create"],
                    "before": None,
                    "after": {
                        "input": {
                            "enabled": True,
                            "exact_plan_approved": True,
                            **lifecycle,
                        },
                        "triggers_replace": lifecycle,
                    },
                    "after_unknown": {
                        "id": True,
                        "input": {},
                        "output": True,
                        "triggers_replace": {},
                    },
                    "before_sensitive": False,
                    "after_sensitive": {
                        "input": {},
                        "output": {},
                        "triggers_replace": {},
                    },
                },
            },
        ],
        "output_changes": {
            "sanitizer_bootstrap_identity_foundation": {
                "actions": ["create"],
                "before": None,
                "after": foundation_after,
                "after_unknown": {
                    "attached_job_resource_ids": [],
                    "client_id": True,
                    "federated_credential_ids": [],
                    "principal_id": True,
                    "resource_id": True,
                    "role_assignment_ids": [],
                    "tenant_id": True,
                },
                "before_sensitive": False,
                "after_sensitive": False,
            },
            "state_boundary": {
                "actions": ["create"],
                "before": None,
                "after": dict(validator.EXPECTED_STATE_BOUNDARY),
                "after_unknown": False,
                "before_sensitive": False,
                "after_sensitive": False,
            },
        },
    }


def _saved_plan_bytes(
    *,
    source: Any | None = None,
    previous: dict[str, Any] | None = None,
    refreshed: dict[str, Any] | None = None,
    extra_member: bool = False,
) -> bytes:
    snapshot = source or _source_snapshot()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("tfplan", b"opaque-open-tofu-plan")
        archive.writestr("tfstate", json.dumps(refreshed or _refreshed_state()))
        archive.writestr("tfstate-prev", json.dumps(previous or _empty_state()))
        archive.writestr("tfconfig/modules.json", '[{"Key":"","Dir":"."}]')
        archive.writestr(".terraform.lock.hcl", snapshot.lockfile)
        for name, value in snapshot.terraform_files.items():
            archive.writestr(f"tfconfig/m-/{name}", value)
        if extra_member:
            archive.writestr("unexpected", b"x")
    return output.getvalue()


def _validate(
    *,
    plan: dict[str, Any] | None = None,
    plan_bytes: bytes | None = None,
    source: Any | None = None,
    previous: dict[str, Any] | None = None,
    refreshed: dict[str, Any] | None = None,
    expected_digest: str | None = None,
    now: datetime = NOW,
) -> dict[str, Any]:
    snapshot = source or _source_snapshot()
    saved = plan_bytes or _saved_plan_bytes(
        source=snapshot, previous=previous, refreshed=refreshed
    )
    return dict(
        validator._validate_artifacts(
            saved_plan_bytes=saved,
            expected_saved_plan_sha256=expected_digest
            or hashlib.sha256(saved).hexdigest(),
            source=snapshot,
            plan_json_bytes=json.dumps(plan or _valid_plan()).encode(),
            expected_state_lineage=LINEAGE,
            expected_state_serial=SERIAL,
            tofu_sha256="c" * 64,
            provider_sha256="b" * 64,
            now=now,
        )
    )


def test_exact_plan_is_bound_but_never_authorizes_apply() -> None:
    receipt = _validate()

    assert receipt == {
        "schema_version": 1,
        "policy_id": "cosmaneura-staging-sanitizer-bootstrap-identity-plan-v1",
        "source_commit": "a" * 40,
        "source_tree_sha256": _source_snapshot().tree_sha256,
        "provider_lock_sha256": hashlib.sha256(_source_snapshot().lockfile).hexdigest(),
        "open_tofu_version": validator.EXPECTED_TOFU_VERSION,
        "open_tofu_sha256": "c" * 64,
        "open_tofu_provider_sha256": "b" * 64,
        "saved_plan_sha256": receipt["saved_plan_sha256"],
        "derived_plan_json_sha256": hashlib.sha256(
            json.dumps(_valid_plan()).encode()
        ).hexdigest(),
        "embedded_prior_state_sha256": receipt["embedded_prior_state_sha256"],
        "embedded_refreshed_state_sha256": receipt["embedded_refreshed_state_sha256"],
        "state_lineage": LINEAGE,
        "state_serial": SERIAL,
        "plan_timestamp": PLAN_TIMESTAMP,
        "cleanup_not_after": CLEANUP,
        "lifetime_hours": 6,
        "rehearsal_id": REHEARSAL_ID,
        "create_count": 2,
        "azure_create_count": 1,
        "update_count": 0,
        "destroy_or_replace_count": 0,
        "source_archive_bound": True,
        "plan_artifact_bound": True,
        "live_remote_master_bound": False,
        "live_backend_readback_bound": False,
        "live_resource_readback_bound": False,
        "apply_authorized": False,
        "pass": True,
    }


def test_digest_and_archive_source_are_exact() -> None:
    with pytest.raises(validator.BootstrapPlanError, match="digest"):
        _validate(expected_digest="0" * 64)

    source = _source_snapshot()
    tampered = dict(source.terraform_files)
    tampered["main.tf"] += b"\n# tampered\n"
    archive_source = validator.SourceSnapshot(
        commit=source.commit,
        terraform_files=tampered,
        lockfile=source.lockfile,
        tree_sha256=source.tree_sha256,
    )
    with pytest.raises(validator.BootstrapPlanError, match="source differs"):
        _validate(plan_bytes=_saved_plan_bytes(source=archive_source), source=source)

    extra = _saved_plan_bytes(source=source, extra_member=True)
    with pytest.raises(validator.BootstrapPlanError, match="contents differ"):
        _validate(plan_bytes=extra, source=source)


def test_json_rejects_duplicate_keys_and_nonfinite_numbers() -> None:
    for raw in (b'{"key":1,"key":1}', b'{"key":NaN}'):
        with pytest.raises(validator.BootstrapPlanError, match="JSON"):
            validator._load_json_bytes(raw, label="test")


def test_embedded_states_must_be_exact_empty_then_read_only_refresh() -> None:
    previous = _empty_state()
    previous["resources"] = [_raw_resource_group()]
    with pytest.raises(validator.BootstrapPlanError, match="prior state is not empty"):
        _validate(previous=previous)

    refreshed = _refreshed_state()
    refreshed["resources"].append({"mode": "managed"})
    with pytest.raises(validator.BootstrapPlanError, match="unexpected resource"):
        _validate(refreshed=refreshed)

    wrong_lineage = _refreshed_state()
    wrong_lineage["lineage"] = "11111111-1111-4111-8111-111111111111"
    with pytest.raises(validator.BootstrapPlanError, match="identity is not exact"):
        _validate(refreshed=wrong_lineage)

    extra = _refreshed_state()
    extra["resources"][0]["instances"][0]["attributes"]["unexpected"] = True
    with pytest.raises(validator.BootstrapPlanError, match="attributes are not exact"):
        _validate(refreshed=extra)


def test_prior_state_shape_is_closed() -> None:
    for target in (
        lambda plan: plan["prior_state"],
        lambda plan: plan["prior_state"]["values"]["root_module"]["resources"][0],
        lambda plan: plan["prior_state"]["values"]["root_module"]["resources"][0][
            "values"
        ],
    ):
        plan = _valid_plan()
        target(plan)["unexpected"] = True
        with pytest.raises(
            validator.BootstrapPlanError, match="prior state|boundary|resource group"
        ):
            _validate(plan=plan)


def test_decoder_initialization_is_backend_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    provider = (
        data_dir
        / "providers/registry.opentofu.org/hashicorp/azurerm/4.73.0/darwin_arm64"
        / "terraform-provider-azurerm"
    )
    provider.parent.mkdir(parents=True)
    provider.write_bytes(b"provider")
    provider.chmod(0o700)

    def fake_run(
        arguments: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        assert "init" in arguments
        assert "-backend=false" in arguments
        assert "plan" not in arguments and "apply" not in arguments
        assert kwargs["data_dir"] == data_dir
        return subprocess.CompletedProcess(arguments, 0, b"initialized", b"")

    monkeypatch.setattr(validator, "_run_process", fake_run)
    monkeypatch.setattr(
        validator,
        "_require_pinned_executable",
        lambda *_, expected, **__: expected,
    )
    monkeypatch.setattr(
        validator,
        "EXPECTED_PROVIDER_SHA256",
        hashlib.sha256(b"provider").hexdigest(),
    )
    assert (
        validator._initialize_decoder(tofu=TOFU, data_dir=data_dir)
        == hashlib.sha256(b"provider").hexdigest()
    )


def test_tool_digests_and_decoder_environment_are_pinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(validator.shutil, "which", lambda _: str(TOFU))
    monkeypatch.setattr(validator, "_trusted_executable", lambda path, **_: path)
    monkeypatch.setattr(validator, "_executable_sha256", lambda *_, **__: "0" * 64)
    monkeypatch.setattr(
        validator,
        "_run_process",
        lambda *_, **__: pytest.fail("an unpinned OpenTofu binary was executed"),
    )
    with pytest.raises(validator.BootstrapPlanError, match="digest is not pinned"):
        validator._resolve_and_verify_tofu()

    for name in ("HTTPS_PROXY", "http_proxy", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        monkeypatch.setenv(name, "attacker-controlled")
    environment = validator._normalized_environment()
    assert not any(
        name.upper() in {key.upper() for key in environment}
        for name in ("HTTPS_PROXY", "http_proxy", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")
    )


def test_checkout_reader_rejects_link_substitution(tmp_path: Path) -> None:
    source = tmp_path / "source.tf"
    source.write_bytes(b"reviewed")
    source.chmod(0o644)
    assert validator._read_checkout_file(source, label="source") == b"reviewed"
    link = tmp_path / "linked.tf"
    link.symlink_to(source)
    with pytest.raises(validator.BootstrapPlanError, match="safely"):
        validator._read_checkout_file(link, label="source")
