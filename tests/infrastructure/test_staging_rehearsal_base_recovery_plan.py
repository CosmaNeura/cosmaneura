from __future__ import annotations

import hashlib
import importlib.util
import inspect
import io
import json
import os
import pwd
import shutil
import subprocess
import sys
import zipfile
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/validate_staging_rehearsal_base_recovery_plan.py"
REHEARSAL_ROOT = ROOT / "infra-staging-rehearsal"
NOW = datetime(2026, 9, 5, 2, 45, tzinfo=UTC)
BACKEND_SHA256 = "c" * 64
TOFU = Path("/trusted/tofu")
AZURE_CLI = Path("/trusted/az")
DATA_DIR = Path("/protected/tofu-data")


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("recovery_plan_validator", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


recovery = _load_module()


def _state_resource(address: str) -> dict[str, Any]:
    base, separator, raw_key = address.partition("[")
    resource_type, name = base.split(".", 1)
    provider, schema_version, resource_id = recovery.EXPECTED_STATE_IDENTITIES[address]
    instance: dict[str, Any] = {
        "schema_version": schema_version,
        "attributes": {"id": resource_id},
    }
    if separator:
        assert raw_key.endswith("]")
        instance["index_key"] = json.loads(raw_key[:-1])
    return {
        "mode": "managed",
        "type": resource_type,
        "name": name,
        "provider": provider,
        "instances": [instance],
    }


def _valid_state() -> dict[str, Any]:
    return {
        "version": 4,
        "terraform_version": recovery.EXPECTED_TOFU_VERSION,
        "serial": recovery.EXPECTED_RECOVERY_SERIAL,
        "lineage": recovery.EXPECTED_LINEAGE,
        "outputs": {},
        "resources": [
            _state_resource(address)
            for address in sorted(recovery.EXPECTED_EXISTING_ADDRESSES)
        ],  # fmt: skip
    }


def _valid_state_projection() -> dict[str, Any]:
    resources = []
    for address in sorted(recovery.EXPECTED_EXISTING_ADDRESSES):
        metadata = dict(recovery._resource_change_metadata(address))
        _, schema_version, resource_id = recovery.EXPECTED_STATE_IDENTITIES[address]
        resources.append({**metadata, "schema_version": schema_version, "values": {"id": resource_id}, "sensitive_values": {}})  # fmt: skip
    return {"format_version": "1.0", "terraform_version": recovery.EXPECTED_TOFU_VERSION, "values": {"root_module": {"resources": resources}}}  # fmt: skip


def _valid_plan(*, state_projection: dict[str, Any] | None = None) -> dict[str, Any]:
    projected = state_projection or _valid_state_projection()
    projected_by_address = {item["address"]: item for item in projected["values"]["root_module"]["resources"]}  # fmt: skip

    # fmt: off
    def no_op(address: str) -> dict[str, Any]:
        resource = projected_by_address[address]
        values, sensitive = deepcopy(resource["values"]), deepcopy(resource["sensitive_values"])
        change = {"actions": ["no-op"], "before": values, "after": deepcopy(values), "after_unknown": {}, "before_sensitive": sensitive, "after_sensitive": deepcopy(sensitive)}  # fmt: skip
        if "identity" in resource:
            change.update(before_identity=deepcopy(resource["identity"]), after_identity=deepcopy(resource["identity"]))  # fmt: skip
        return {**recovery._resource_change_metadata(address), "change": change}

    changes = [no_op(address) for address in sorted(recovery.EXPECTED_EXISTING_ADDRESSES)]
    # fmt: on
    changes.extend(
        deepcopy(recovery._expected_create_change(address))
        for address in sorted(recovery.EXPECTED_CREATE_ADDRESSES)
    )
    providers = json.loads(
        '{"azurerm":{"full_name":"registry.opentofu.org/hashicorp/azurerm","version_constraint":"4.73.0","expressions":{"features":[{"storage":[{"data_plane_available":{"constant_value":false}}]}]}},"azapi":{"full_name":"registry.opentofu.org/azure/azapi","version_constraint":"2.1.0","expressions":{}},"terraform":{"full_name":"terraform.io/builtin/terraform","version_constraint":null,"expressions":null}}'
    )
    return {
        "format_version": recovery.EXPECTED_FORMAT_VERSION,
        "terraform_version": recovery.EXPECTED_TOFU_VERSION,
        "timestamp": NOW.isoformat().replace("+00:00", "Z"),
        "errored": False,
        "variables": {
            name: {"value": value}
            for name, value in recovery.EXPECTED_VARIABLES.items()
        },  # fmt: skip
        "checks": [{"status": "pass", "instances": []}],
        "configuration": {"provider_config": providers},
        "prior_state": deepcopy(projected),
        "resource_changes": changes,
    }


def _source_snapshot() -> Any:
    terraform_files = {
        path.name: path.read_bytes() for path in sorted(REHEARSAL_ROOT.glob("*.tf"))
    }
    lockfile = (REHEARSAL_ROOT / ".terraform.lock.hcl").read_bytes()
    files = {**terraform_files, ".terraform.lock.hcl": lockfile}
    return recovery.SourceSnapshot(
        commit="a" * 40,
        terraform_files=terraform_files,
        lockfile=lockfile,
        tree_sha256=recovery._canonical_file_set_sha256(files),
    )


def _runtime_snapshot() -> Any:
    return recovery.RuntimeSnapshot(TOFU, AZURE_CLI, recovery.EXPECTED_TOFU_SHA256, recovery.EXPECTED_AZURE_CLI_SHA256, recovery.EXPECTED_AZURE_CLI_VERSION_SHA256, "f" * 64, "1" * 64)  # fmt: skip


def _saved_plan_bytes(
    *, state: dict[str, Any] | None = None, source: Any | None = None
) -> bytes:
    snapshot = source or _source_snapshot()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("tfplan", b"opaque-open-tofu-plan")
        archive.writestr("tfstate", json.dumps(state or _valid_state()).encode())
        archive.writestr("tfstate-prev", b"{}")
        archive.writestr("tfconfig/modules.json", b'[{"Key":"","Dir":"."}]')
        archive.writestr(".terraform.lock.hcl", snapshot.lockfile)
        for name, value in snapshot.terraform_files.items():
            archive.writestr(f"tfconfig/m-/{name}", value)
    return output.getvalue()


def _install_plan_show(
    monkeypatch: pytest.MonkeyPatch,
    *,
    expected_plan_bytes: bytes,
    plan: dict[str, Any] | None = None,
    state_projection: dict[str, Any] | None = None,
) -> None:
    projection = state_projection or _valid_state_projection()
    plan_json = json.dumps(plan or _valid_plan(state_projection=projection)).encode()

    def fake_show(value: bytes, *, tofu: Path, data_dir: Path) -> bytes:
        assert value == expected_plan_bytes
        assert tofu == TOFU and data_dir.is_absolute()
        return plan_json

    monkeypatch.setattr(recovery, "_show_saved_plan", fake_show)

    def fake_show_state(value: bytes, *, tofu: Path, data_dir: Path) -> bytes:
        assert value
        assert tofu == TOFU and data_dir.is_absolute()
        return json.dumps(projection).encode()

    monkeypatch.setattr(recovery, "_show_state", fake_show_state)


def _validate(
    monkeypatch: pytest.MonkeyPatch,
    *,
    state: dict[str, Any] | None = None,
    embedded_state: dict[str, Any] | None = None,
    plan: dict[str, Any] | None = None,
    state_projection: dict[str, Any] | None = None,
    source: Any | None = None,
    archive_source: Any | None = None,
    expected_digest: str | None = None,
    saved_plan_bytes: bytes | None = None,
) -> dict[str, Any]:
    source_snapshot = source or _source_snapshot()
    state_value = state or _valid_state()
    plan_bytes = saved_plan_bytes or _saved_plan_bytes(
        state=embedded_state or deepcopy(state_value),
        source=archive_source or source_snapshot,
    )
    _install_plan_show(
        monkeypatch,
        expected_plan_bytes=plan_bytes,
        plan=plan or _valid_plan(state_projection=state_projection),
        state_projection=state_projection,
    )
    return dict(
        recovery._validate_captured_artifacts(
            saved_plan_bytes=plan_bytes,
            state_bytes=json.dumps(state_value).encode(),
            source=source_snapshot,
            runtime=_runtime_snapshot(),
            data_dir=DATA_DIR,
            generation_mode="validator_full_refresh",
            now=NOW,
            backend_metadata_sha256=BACKEND_SHA256,
            expected_saved_plan_sha256=expected_digest,
        )
    )


def _protected_file(path: Path, value: bytes, *, mode: int = 0o600) -> Path:
    path.write_bytes(value)
    path.chmod(mode)
    return path


def test_receipt_binds_exact_artifact_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source_snapshot()
    state_bytes = json.dumps(_valid_state()).encode()
    state_projection_bytes = json.dumps(_valid_state_projection()).encode()
    plan_bytes = _saved_plan_bytes(source=source)
    plan_json_bytes = json.dumps(_valid_plan()).encode()
    receipt = _validate(monkeypatch, source=source, saved_plan_bytes=plan_bytes)

    expected_facts = {"state_lineage": recovery.EXPECTED_LINEAGE, "state_serial": recovery.EXPECTED_RECOVERY_SERIAL, "generation_mode": "validator_full_refresh", "full_refresh_generated_here": True, "validated_nonempty_change_set": True, "artifact_policy_complete": True, "live_readback_bound": False, "apply_authorized": False, "existing_resource_count": 28, "create_count": 11, "update_count": 0, "destroy_or_replace_count": 0, "pass": True}  # fmt: skip
    assert {name: receipt[name] for name in expected_facts} == expected_facts
    digest = recovery._sha256_bytes
    expected_hashes = {"state_json_sha256": digest(state_bytes), "state_semantic_sha256": digest(recovery._canonical_json_bytes(_valid_state())), "state_projection_sha256": digest(state_projection_bytes), "embedded_state_sha256": digest(state_bytes), "saved_plan_sha256": digest(plan_bytes), "derived_plan_json_sha256": digest(plan_json_bytes), "source_tree_sha256": source.tree_sha256, "provider_lock_sha256": digest(source.lockfile), "backend_metadata_sha256": BACKEND_SHA256, "open_tofu_sha256": recovery.EXPECTED_TOFU_SHA256, "azure_cli_sha256": recovery.EXPECTED_AZURE_CLI_SHA256, "azure_cli_version_sha256": recovery.EXPECTED_AZURE_CLI_VERSION_SHA256, "azure_cli_source_closure_sha256": recovery.EXPECTED_AZURE_CLI_CLOSURE_SHA256, "azure_cli_closure_sha256": "f" * 64, "runtime_snapshot_sha256": "1" * 64}  # fmt: skip
    assert {name: receipt[name] for name in expected_hashes} == expected_hashes
    with pytest.raises(recovery.RecoveryPlanError, match="digest"):
        _validate(monkeypatch, expected_digest="0" * 64)


def test_plan_json_comes_from_exact_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = b"exact-plan-bytes"
    plan_json = json.dumps(_valid_plan()).encode()

    def fake_run(
        arguments: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        pass_fds = kwargs["pass_fds"]
        assert kwargs["data_dir"] == DATA_DIR
        assert arguments[-2] == "-json"
        assert len(pass_fds) == 1
        descriptor = pass_fds[0]
        os.lseek(descriptor, 0, os.SEEK_SET)
        assert os.read(descriptor, len(captured) + 1) == captured
        return subprocess.CompletedProcess(arguments, 0, plan_json, b"")

    monkeypatch.setattr(recovery, "_run_process", fake_run)

    assert (
        recovery._show_saved_plan(captured, tofu=TOFU, data_dir=DATA_DIR) == plan_json
    )
    assert (
        "plan_json"
        not in inspect.signature(recovery.revalidate_recovery_plan).parameters
    )


def test_real_opentofu_plan_decodes_from_captured_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = shutil.which("tofu")
    if executable is None:
        pytest.skip("OpenTofu is not installed")
    version = json.loads(subprocess.check_output([executable, "version", "-json"]))
    if version["terraform_version"] != recovery.EXPECTED_TOFU_VERSION:
        pytest.skip("test requires the repository-pinned OpenTofu")
    module = tmp_path / "module"
    module.mkdir(mode=0o700)
    (module / "main.tf").write_text(
        'terraform { required_version = "= 1.12.5" }\n'
        'resource "terraform_data" "probe" { input = "bound" }\n',
        encoding="utf-8",
    )
    plan_path = tmp_path / "real.tfplan"
    commands = (
        [executable, f"-chdir={module}", "init", "-backend=false", "-input=false"],
        [executable, f"-chdir={module}", "plan", "-input=false", f"-out={plan_path}"],
    )
    for command in commands:
        subprocess.run(command, check=True, capture_output=True)
    monkeypatch.setattr(recovery, "REHEARSAL_ROOT", module)

    decoded = recovery._show_saved_plan(
        plan_path.read_bytes(),
        tofu=Path(executable).resolve(),
        data_dir=module / ".terraform",
    )

    plan = json.loads(decoded)
    assert plan["terraform_version"] == recovery.EXPECTED_TOFU_VERSION
    assert plan["resource_changes"][0]["address"] == "terraform_data.probe"


def test_fixed_plan_generation_forces_refresh_and_exact_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = _saved_plan_bytes()

    def fake_run(
        arguments: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        assert kwargs["data_dir"] == DATA_DIR
        output_argument = next(
            value for value in arguments if value.startswith("-out=")
        )
        output = Path(output_argument.removeprefix("-out="))
        expected_arguments = [str(TOFU), f"-chdir={REHEARSAL_ROOT}", "plan", "-input=false", "-lock=true", "-lock-timeout=5m", "-refresh=true", "-detailed-exitcode", "-no-color", f"-out={output}", *recovery.PLAN_VARIABLE_ARGUMENTS]  # fmt: skip
        assert arguments == expected_arguments
        _protected_file(output, expected)
        return subprocess.CompletedProcess(arguments, 2, b"", b"")

    monkeypatch.setattr(recovery, "_run_process", fake_run)

    assert (
        recovery._generate_saved_plan(
            tofu=TOFU, output_parent=tmp_path, data_dir=DATA_DIR
        )
        == expected
    )
    versions = (REHEARSAL_ROOT / "versions.tf").read_text(encoding="utf-8")
    storage = (REHEARSAL_ROOT / "storage.tf").read_text(encoding="utf-8")
    account = storage.split('resource "azapi_resource" "marker_container"', 1)[0]
    assert "data_plane_available = false" in versions
    assert "storage_use_azuread" not in versions
    assert all(item not in account for item in ("queue_properties", "static_website"))
    assert "prevent_destroy = true" in account


@pytest.mark.parametrize(
    ("mode", "generation"),
    [("create", "validator_full_refresh"), ("verify", "digest_bound_revalidation")],
)
def test_cli_modes_emit_only_validated_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
    mode: str,
    generation: str,
) -> None:
    source = _source_snapshot()
    plan_bytes = _saved_plan_bytes(source=source)
    state_path = _protected_file(
        tmp_path / "state.tfstate", json.dumps(_valid_state()).encode()
    )
    plan_path = tmp_path / "recovery.tfplan"
    if mode == "verify":
        _protected_file(plan_path, plan_bytes, mode=0o400)
    monkeypatch.setattr(recovery, "_source_snapshot", lambda _: source)
    monkeypatch.setattr(
        recovery, "_snapshot_and_verify_runtime", lambda **_: _runtime_snapshot()
    )
    monkeypatch.setattr(recovery, "_initialize_tofu", lambda **_: None)
    monkeypatch.setattr(recovery, "_backend_snapshot", lambda **_: BACKEND_SHA256)
    monkeypatch.setattr(recovery, "_generate_saved_plan", lambda **_: plan_bytes)
    monkeypatch.setattr(recovery, "_provider_tree_sha256", lambda _: "d" * 64)
    monkeypatch.setattr(recovery, "_executable_sha256", lambda *_, **__: "e" * 64)
    _install_plan_show(monkeypatch, expected_plan_bytes=plan_bytes)
    fixed_datetime = type("FixedDateTime", (datetime,), {"now": classmethod(lambda cls, tz=None: NOW)})  # fmt: skip
    monkeypatch.setattr(recovery, "datetime", fixed_datetime)
    arguments = ["--mode", mode, "--saved-plan", str(plan_path), "--state-json", str(state_path), "--expected-source-commit", source.commit]  # fmt: skip
    if mode == "verify":
        arguments += ["--execution-dir", str(tmp_path / "execution"), "--expected-saved-plan-sha256", hashlib.sha256(plan_bytes).hexdigest()]  # fmt: skip

    assert recovery.main(arguments) == 0
    if mode == "create":
        assert plan_path.read_bytes() == plan_bytes
        assert plan_path.stat().st_mode & 0o777 == 0o400
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out)["generation_mode"] == generation
    assert b"opaque-open-tofu-plan" not in output.out.encode()


def test_state_snapshot_safety_regressions(monkeypatch: pytest.MonkeyPatch) -> None:
    for serial in (8, 10):
        state = _valid_state()
        state["serial"] = serial
        with pytest.raises(
            recovery.RecoveryPlanError, match="serial must be exactly 9"
        ):
            _validate(monkeypatch, state=state)
    for status in ("tainted", "unknown", "deposed"):
        state = _valid_state()
        state["resources"][0]["instances"][0]["status"] = status
        with pytest.raises(recovery.RecoveryPlanError, match="non-ready"):
            _validate(monkeypatch, state=state)
    for field, value in (("schema_version", 7), ("attributes", {"id": "different"})):
        embedded = _valid_state()
        embedded["resources"][0]["instances"][0][field] = value
        with pytest.raises(recovery.RecoveryPlanError, match="does not match"):
            _validate(monkeypatch, embedded_state=embedded)
    state = _valid_state()
    state["resources"][0]["provider"] = 'provider["attacker.invalid/provider"]'
    with pytest.raises(recovery.RecoveryPlanError, match="exact contract"):
        _validate(monkeypatch, state=state)
    embedded = _valid_state()
    embedded["resources"][0]["instances"][0]["attributes"]["semantic_field"] = "mutated"
    with pytest.raises(recovery.RecoveryPlanError, match="semantically identical"):
        _validate(monkeypatch, embedded_state=embedded)

    external = _valid_state()
    embedded = deepcopy(external)
    external["check_results"] = [{"status": "pass"}]
    embedded["check_results"] = None
    receipt = _validate(monkeypatch, state=external, embedded_state=embedded)
    assert receipt["state_semantic_sha256"] == recovery._semantic_state_sha256(
        external, label="state snapshot"
    )


def test_plan_safety_regressions_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    def rejected(plan: dict[str, Any], message: str) -> None:
        with pytest.raises(recovery.RecoveryPlanError, match=message):
            _validate(monkeypatch, plan=plan)

    for actions in json.loads(
        '[["delete","create"],["create","delete"],["delete"],["update"],["read"]]'
    ):
        plan = _valid_plan()
        change = next(item for item in plan["resource_changes"] if item["address"] == "azurerm_storage_account.marker[0]")  # fmt: skip
        change["change"]["actions"] = actions
        rejected(plan, "would change")
    stale = (NOW - timedelta(hours=4, seconds=1)).isoformat().replace("+00:00", "Z")
    future = (NOW + timedelta(minutes=5, seconds=1)).isoformat().replace("+00:00", "Z")
    # fmt: off
    for field, value, message in (
        ("errored", True, "errored"),
        ("resource_drift", [{"address": "drift"}], "drift"),
        ("deferred_changes", [{"reason": "unknown"}], "deferred"),
        ("applyable", False, "applyable"),
        ("complete", False, "complete"),
        ("timestamp", stale, "stale"),
        ("timestamp", future, "future"),
    ):
        # fmt: on
        plan = _valid_plan()
        plan[field] = value
        rejected(plan, message)
    plan = _valid_plan()
    plan["resource_changes"].append({"address": "azurerm_public_ip.forbidden[0]", "change": {"actions": ["create"]}})  # fmt: skip
    rejected(plan, "address set")
    # fmt: off
    for path, value, message in (
        (("configuration", "provider_config", "azurerm", "expressions", "features"), [{}], "data-plane opt-out"),
        (("variables", "enable_marker_legal_holds", "value"), True, "not recovery-safe"),
        (("checks", 0, "status"), "fail", "non-passing"),
    ):
        # fmt: on
        plan = _valid_plan()
        node: Any = plan
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        rejected(plan, message)


def test_noop_values_sensitivity_identity_and_prior_state_are_exact(monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    projection = _valid_state_projection()
    projected = projection["values"]["root_module"]["resources"][0]
    projected.update(values={**projected["values"], "binding": "protected"}, sensitive_values={"binding": True}, identity={"key": "state-bound"}, identity_schema_version=0)  # fmt: skip
    base = _valid_plan(state_projection=projection)
    address = projected["address"]
    for field, value in (("before", {"id": "different"}), ("after", {"id": "different"}), ("before_sensitive", {}), ("after_identity", {"key": "different"}), ("action_reason", [])):  # fmt: skip
        plan = deepcopy(base)
        change = next(item for item in plan["resource_changes"] if item["address"] == address)["change"]  # fmt: skip
        change[field] = value
        with pytest.raises(recovery.RecoveryPlanError):
            _validate(monkeypatch, plan=plan, state_projection=projection)
    plan = deepcopy(base)
    plan["prior_state"]["values"]["root_module"]["resources"][0]["values"]["binding"] = "different"  # fmt: skip
    with pytest.raises(recovery.RecoveryPlanError, match="prior state"):
        _validate(monkeypatch, plan=plan, state_projection=projection)


def test_rejected_plan_digest_cannot_be_reauthorized(monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    plan = _saved_plan_bytes()
    monkeypatch.setattr(recovery, "REJECTED_SAVED_PLAN_SHA256S", frozenset({recovery._sha256_bytes(plan)}))  # fmt: skip
    with pytest.raises(recovery.RecoveryPlanError, match="permanently rejected"):
        _validate(monkeypatch, saved_plan_bytes=plan)


# fmt: off
@pytest.mark.parametrize(
    ("address", "path", "value"),
    [
        ('azapi_resource.marker_container["restore-cleanups-v4"]', ("change", "after", "parent_id"), "/subscriptions/attacker/storage"),  # fmt: skip
        ("azurerm_private_endpoint.marker[0]", ("change", "after", "subnet_id"), "/subscriptions/attacker/subnet"),  # fmt: skip
        ("azurerm_private_endpoint.marker[0]", ("change", "after", "private_service_connection", 0, "private_connection_resource_id"), "/subscriptions/attacker/storage"),  # fmt: skip
        ('azurerm_role_assignment.marker_read["cleanup_intent"]', ("change", "after", "principal_id"), "00000000-0000-0000-0000-000000000000"),  # fmt: skip
        ("azurerm_role_assignment.marker_provision_cleanup[0]", ("change", "after", "role_definition_id"), "/subscriptions/attacker/role"),  # fmt: skip
        ("azurerm_private_endpoint.marker[0]", ("change", "after_unknown", "subnet_id"), True),  # fmt: skip
        ("azurerm_private_endpoint.marker[0]", ("provider_name",), "registry.opentofu.org/attacker/provider"),  # fmt: skip
        ('azapi_resource.marker_container["restore-cleanups-v4"]', ("change", "replace_paths"), []),  # fmt: skip
    ],
)
# fmt: on
def test_create_contract_rejects_exact_value_or_schema_mutation(
    monkeypatch: pytest.MonkeyPatch,
    address: str,
    path: tuple[str | int, ...],
    value: Any,
) -> None:
    plan = _valid_plan()
    change = next(item for item in plan["resource_changes"] if item["address"] == address)  # fmt: skip
    node: Any = change
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(recovery.RecoveryPlanError, match="exact create contract"):
        _validate(monkeypatch, plan=plan)

def test_pinned_opentofu_json_may_omit_applyable_and_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _valid_plan()
    assert "applyable" not in plan and "complete" not in plan
    assert _validate(monkeypatch, plan=plan)["validated_nonempty_change_set"] is True


def test_remote_git_pins_ssh_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []
    monkeypatch.setattr(recovery, "_trusted_system_executable", lambda path, **_: path)
    monkeypatch.setattr(recovery, "_run_process", lambda args, **kw: seen.append((args, kw)) or subprocess.CompletedProcess(args, 0, b"ok", b""))  # fmt: skip
    assert recovery._run_git(["ls-remote"], repository=False) == b"ok"
    command = seen[0][0]
    assert command[:2] == ["/usr/bin/git", "--no-replace-objects"] and seen[0][1]["cwd"] == Path("/")  # fmt: skip
    assert "/usr/bin/ssh" in command[3] and "StrictHostKeyChecking=yes" in command[3]  # fmt: skip


def test_archive_source_and_lockfile_are_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    for component in ("source", "lockfile"):
        source = _source_snapshot()
        files, lockfile = dict(source.terraform_files), source.lockfile
        if component == "source":
            files["versions.tf"] += b"\n# detached\n"
        else:
            lockfile += b"\n# detached\n"
        changed = recovery.SourceSnapshot(source.commit, files, lockfile, "b" * 64)
        message = (
            "Terraform source differs" if component == "source" else "lockfile differs"
        )
        with pytest.raises(recovery.RecoveryPlanError, match=message):
            _validate(monkeypatch, source=source, archive_source=changed)


def test_source_snapshot_rejects_clean_worktree_bytes_not_at_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, commit = tmp_path, "a" * 40
    rehearsal, validator = root / "infra", root / "scripts" / "validator.py"
    artifacts = ((rehearsal / "main.tf", b"mutated"), (rehearsal / ".terraform.lock.hcl", b"lock"), (validator, b"validator"))  # fmt: skip
    for path, value in artifacts:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    globals_ = (("REPOSITORY_ROOT", root), ("REHEARSAL_ROOT", rehearsal), ("VALIDATOR_PATH", validator))  # fmt: skip
    for name, value in globals_:
        monkeypatch.setattr(recovery, name, value)
    responses = {"rev-parse": f"{commit}\n".encode(), "remote": f"{recovery.EXPECTED_ORIGIN_URL}\n".encode(), "ls-remote": f"{commit}\trefs/heads/master\n".encode(), "status": b"", "ls-tree": b"infra/main.tf\ninfra/.terraform.lock.hcl\n", "show": b"reviewed"}  # fmt: skip
    monkeypatch.setattr(
        recovery, "_run_git", lambda arguments, **_: responses[arguments[0]]
    )
    with pytest.raises(recovery.RecoveryPlanError, match="source bytes differ"):
        recovery._source_snapshot(commit)
    responses["remote"] = b"git@github.com:attacker/fork.git\n"
    with pytest.raises(recovery.RecoveryPlanError, match="protected-master"):
        recovery._source_snapshot(commit)


@pytest.mark.parametrize(
    "kind", ["leaf_symlink", "parent_symlink", "directory", "fifo"]
)
def test_unsafe_artifact_types_are_rejected(tmp_path: Path, kind: str) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    artifact = real / "artifact"
    if kind == "leaf_symlink":
        target = _protected_file(real / "target", b"value")
        artifact.symlink_to(target)
    elif kind == "parent_symlink":
        target = _protected_file(real / "target", b"value")
        alias = tmp_path / "alias"
        alias.symlink_to(real, target_is_directory=True)
        artifact = alias / target.name
    elif kind == "directory":
        artifact.mkdir(mode=0o700)
    else:
        os.mkfifo(artifact, 0o600)

    with pytest.raises(recovery.RecoveryPlanError):
        recovery._read_protected_file(artifact, label="artifact")


def test_file_permissions_and_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exposed_directory = tmp_path / "exposed"
    exposed_directory.mkdir(mode=0o755)
    exposed = _protected_file(exposed_directory / "artifact", b"value")
    with pytest.raises(recovery.RecoveryPlanError, match="directory must have mode"):
        recovery._read_protected_file(exposed, label="artifact")

    insecure = _protected_file(tmp_path / "insecure", b"value", mode=0o644)
    with pytest.raises(recovery.RecoveryPlanError, match="mode 0400 or 0600"):
        recovery._read_protected_file(insecure, label="artifact")

    original = _protected_file(tmp_path / "original", b"value")
    linked = tmp_path / "linked"
    os.link(original, linked)
    with pytest.raises(recovery.RecoveryPlanError, match="hard link"):
        recovery._read_protected_file(original, label="artifact")

    for label, mode, required in (("plan", 0o600, 0o400), ("state", 0o400, 0o600)):
        artifact = _protected_file(tmp_path / label, b"value", mode=mode)
        with pytest.raises(recovery.RecoveryPlanError, match="mode"):
            recovery._read_protected_file(artifact, label=label, required_mode=required)

    owner_only = _protected_file(tmp_path / "owner-only", b"value")
    monkeypatch.setattr(recovery.os, "geteuid", lambda: os.getuid() + 1)
    with pytest.raises(recovery.RecoveryPlanError, match="not owned"):
        recovery._read_protected_file(owner_only, label="artifact")


def test_path_swap_after_open_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _protected_file(tmp_path / "artifact", b"reviewed")
    replacement = _protected_file(tmp_path / "replacement", b"different")
    real_read = os.read
    swapped = False

    def swap_then_read(descriptor: int, size: int) -> bytes:
        nonlocal swapped
        if not swapped:
            os.replace(replacement, artifact)
            swapped = True
        return real_read(descriptor, size)

    monkeypatch.setattr(recovery.os, "read", swap_then_read)

    with pytest.raises(recovery.RecoveryPlanError, match="changed while"):
        recovery._read_protected_file(artifact, label="artifact")


def test_parent_swap_keeps_open_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = tmp_path / "original"
    replacement = tmp_path / "replacement"
    moved = tmp_path / "moved"
    original.mkdir(mode=0o700)
    replacement.mkdir(mode=0o700)
    artifact = _protected_file(original / "artifact", b"reviewed")
    _protected_file(replacement / "artifact", b"different")
    real_open = os.open
    swapped = False

    def swap_parent_after_open(
        path: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        nonlocal swapped
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if not swapped and dir_fd is None and Path(path) == original:
            original.rename(moved)
            replacement.rename(original)
            swapped = True
        return descriptor

    monkeypatch.setattr(recovery.os, "open", swap_parent_after_open)

    assert recovery._read_protected_file(artifact, label="artifact") == b"reviewed"


def test_runtime_executables_and_versions_are_snapshotted_and_pinned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # fmt: off
    source, data_dir = tmp_path / "source", tmp_path / "data"
    source.mkdir(mode=0o700); data_dir.mkdir(mode=0o700)  # noqa: E702
    tofu = _protected_file(source / "tofu", b"tofu-binary", mode=0o500)
    azure_cli = _protected_file(source / "az", b"azure-cli", mode=0o500)
    azure_version = {"azure-cli": "2.89.1", "extensions": {}}
    pins = {"EXPECTED_TOFU_PATH": tofu, "EXPECTED_AZURE_CLI_PATH": azure_cli, "EXPECTED_TOFU_SHA256": hashlib.sha256(tofu.read_bytes()).hexdigest(), "EXPECTED_AZURE_CLI_SHA256": hashlib.sha256(azure_cli.read_bytes()).hexdigest(), "EXPECTED_AZURE_CLI_VERSION_SHA256": hashlib.sha256(recovery._canonical_json_bytes(azure_version)).hexdigest(), "EXPECTED_AZURE_CLOUD_SHA256": hashlib.sha256(recovery._canonical_json_bytes(azure_version)).hexdigest()}
    for name, value in pins.items(): monkeypatch.setattr(recovery, name, value)  # noqa: E701
    closure_sha256 = "c" * 64

    def fake_snapshot_azure_cli_closure(runtime_directory: Path) -> tuple[Path, str]:
        (runtime_directory / "homebrew").mkdir(mode=0o700, exist_ok=True)
        wrapper = runtime_directory / "az"
        if not wrapper.exists():
            wrapper.write_bytes(recovery._azure_cli_wrapper(runtime_directory)); wrapper.chmod(0o500)  # noqa: E702
        return wrapper, closure_sha256

    def fake_run(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        assert kwargs["data_dir"] == data_dir
        output = b'{"terraform_version":"1.12.5"}' if arguments[-2:] == ["version", "-json"] else json.dumps(azure_version).encode()
        return subprocess.CompletedProcess(arguments, 0, output, b"")

    monkeypatch.setattr(recovery, "_run_process", fake_run); monkeypatch.setattr(recovery, "_snapshot_azure_cli_closure", fake_snapshot_azure_cli_closure)  # noqa: E702
    runtime = recovery._snapshot_and_verify_runtime(data_dir=data_dir)
    assert runtime.tofu.read_bytes() == tofu.read_bytes() and runtime.azure_cli.read_bytes() == recovery._azure_cli_wrapper(runtime.azure_cli.parent)
    assert runtime.azure_cli_closure_sha256 == closure_sha256 and runtime.tofu.stat().st_mode & 0o777 == runtime.azure_cli.stat().st_mode & 0o777 == 0o500
    runtime.azure_cli.chmod(0o700)
    with pytest.raises(recovery.RecoveryPlanError, match="snapshot is not exact"):
        recovery._snapshot_and_verify_runtime(data_dir=data_dir)
    # fmt: on


def test_azure_cli_closure_digest_binds_dependency_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve() / "homebrew"; formula = root / "Cellar" / "azure-cli" / "1"  # noqa: E702  # fmt: skip
    formula.mkdir(parents=True)
    dependency = _protected_file(formula / "dependency.py", b"trusted", mode=0o400)
    opt = root / "opt"; opt.mkdir()  # noqa: E702
    (opt / "azure-cli").symlink_to("../Cellar/azure-cli/1")
    monkeypatch.setattr(recovery, "EXPECTED_AZURE_CLI_FORMULAE", {"azure-cli": "1"})
    digest = lambda: recovery._runtime_entries_sha256(recovery._azure_cli_closure_entries(root, snapshot=False))  # noqa: E731  # fmt: skip
    captured = bytearray()
    record = recovery._runtime_file_record(dependency, label="dependency", capture=captured)
    before = digest()
    dependency.chmod(0o600)
    dependency.write_bytes(b"changed")
    dependency.chmod(0o400)
    assert bytes(captured) == b"trusted" and record != recovery._runtime_file_record(dependency, label="dependency") and before != digest()  # fmt: skip


def test_cli_cloud_environment_is_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    scrubbed = "TF_CLI_ARGS TOFU_CLI_ARGS_plan OPENTOFU_CLI_ARGS TF_VAR_enable_marker_legal_holds TF_LOG_PATH GIT_DIR HTTPS_PROXY SSL_CERT_FILE REQUESTS_CA_BUNDLE PYTHONPATH AZURE_CONFIG_DIR ARM_TENANT_ID PATH".split()
    for name in scrubbed:
        monkeypatch.setenv(name, "hostile")
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    environment = recovery._normalized_environment(data_dir=DATA_DIR)
    expected = {
        "ARM_ENVIRONMENT": "public",
        "ARM_SUBSCRIPTION_ID": recovery.EXPECTED_SUBSCRIPTION_ID,
        "ARM_TENANT_ID": recovery.EXPECTED_TENANT_ID,
        "ARM_USE_AZUREAD": "true",
        "ARM_USE_CLI": "true",
        "AZURE_CONFIG_DIR": str(Path(pwd.getpwuid(os.geteuid()).pw_dir) / ".azure"),
        "AZURE_CORE_COLLECT_TELEMETRY": "false",
        "AZURE_CORE_DISABLE_CONFIRM_PROMPT": "true",
        "AZURE_CORE_ONLY_SHOW_ERRORS": "true",
        "AZURE_EXTENSION_USE_DYNAMIC_INSTALL": "no",
        "AZURE_LOGGING_ENABLE_LOG_FILE": "false",
        "CHECKPOINT_DISABLE": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": pwd.getpwuid(os.geteuid()).pw_dir,
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": f"{recovery.EXPECTED_AZURE_CLI_PATH.parent}:/usr/bin:/bin",
        "TF_CLI_CONFIG_FILE": os.devnull,
        "TF_DATA_DIR": str(DATA_DIR),
        "TF_IN_AUTOMATION": "1",
        "TF_INPUT": "0",
    }
    assert environment == expected


def test_backend_cache_and_workspace_are_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected_init = [str(TOFU), f"-chdir={REHEARSAL_ROOT}", "init", "-backend=true", "-input=false", "-lockfile=readonly", "-reconfigure"]  # fmt: skip
    expected_init += [
        f"-backend-config={name}={str(item).lower()}"
        for name, item in recovery.EXPECTED_BACKEND_CONFIG.items()
    ]

    def fake_init(
        arguments: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        assert arguments == expected_init and kwargs["data_dir"] == tmp_path
        return subprocess.CompletedProcess(arguments, 0, b"", b"")

    monkeypatch.setattr(recovery, "_run_process", fake_init)
    recovery._initialize_tofu(tofu=TOFU, data_dir=tmp_path)
    cases = (
        ({}, b"default\n", None),
        ({"key": "wrong.tfstate"}, b"default\n", "coordinates are not exact"),
        ({"tenant_id": "injected"}, b"default\n", "contains an override"),
        ({}, b"other\n", "workspace is not default"),
    )
    for updates, workspace, message in cases:
        config = {**recovery.EXPECTED_BACKEND_CONFIG, **updates}
        value = json.dumps({"backend": {"type": "azurerm", "config": config}}).encode()
        _protected_file(tmp_path / "terraform.tfstate", value)

        def fake_run(
            arguments: list[str], **kwargs: Any
        ) -> subprocess.CompletedProcess[bytes]:
            expected = [str(TOFU), f"-chdir={REHEARSAL_ROOT}", "workspace", "show"]
            assert arguments == expected and kwargs["data_dir"] == tmp_path
            return subprocess.CompletedProcess(arguments, 0, workspace, b"")

        monkeypatch.setattr(recovery, "_run_process", fake_run)
        if message:
            with pytest.raises(recovery.RecoveryPlanError, match=message):
                recovery._backend_snapshot(tofu=TOFU, data_dir=tmp_path)
        else:
            assert (
                recovery._backend_snapshot(tofu=TOFU, data_dir=tmp_path)
                == hashlib.sha256(value).hexdigest()
            )


def test_subprocess_is_bounded_and_kills_its_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_popen, real_killpg, seen = subprocess.Popen, os.killpg, {}

    def popen(*args: Any, **kwargs: Any) -> Any:
        seen["new_session"] = kwargs.get("start_new_session")
        return real_popen(*args, **kwargs)

    def killpg(pid: int, sig: int) -> None:
        seen["killpg"] = sig
        real_killpg(pid, sig)

    monkeypatch.setattr(recovery.subprocess, "Popen", popen)
    monkeypatch.setattr(recovery.os, "killpg", killpg)
    with pytest.raises(recovery.RecoveryPlanError, match="output exceeded"):
        recovery._run_process(
            [sys.executable, "-c", "import sys;sys.stdout.write('x'*4096)"],
            cwd=tmp_path,
            timeout_seconds=5,
            max_output_bytes=32,
        )
    with pytest.raises(recovery.RecoveryPlanError, match="timed out"):
        recovery._run_process(
            [sys.executable, "-c", "import time;time.sleep(1)"],
            cwd=tmp_path,
            timeout_seconds=0.01,
        )
    assert seen == {"new_session": True, "killpg": recovery.signal.SIGKILL}
