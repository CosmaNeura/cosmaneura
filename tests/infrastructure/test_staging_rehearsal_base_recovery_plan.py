from __future__ import annotations
# ruff: noqa: E701, E702, I001
# fmt: off

import hashlib
import importlib.util
import inspect
import io
import json
import os
import selectors
import shutil
import signal
import stat
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


def _azure_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, bytes]]:
    home = tmp_path / "home"; config = home / ".azure"  # noqa: E702
    config.mkdir(parents=True, mode=0o700); values = {"config": b"config", "clouds.config": b"cloud", "azureProfile.json": b"profile", "msal_token_cache.json": b"token-cache"}
    for name, value in values.items():
        path = config / name; path.write_bytes(value); path.chmod(0o600)  # noqa: E702
    monkeypatch.setattr(recovery, "EXPECTED_AZURE_CONFIG_SHA256S", {name: hashlib.sha256(values[name]).hexdigest() for name in ("config", "clouds.config")}); monkeypatch.setattr(recovery.pwd, "getpwuid", lambda _: type("Passwd", (), {"pw_dir": str(home)})()); return home, values
def _state_resource(address: str) -> dict[str, Any]:
    base, separator, raw_key = address.partition("[")
    resource_type, name = base.split(".", 1)
    provider, schema_version, resource_id = recovery.EXPECTED_STATE_IDENTITIES[address]
    instance: dict[str, Any] = {"schema_version": schema_version, "attributes": {"id": resource_id}}
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


def _valid_state_projection() -> dict[str, Any]: resources = [{**dict(recovery._resource_change_metadata(address)), "schema_version": recovery.EXPECTED_STATE_IDENTITIES[address][1], "values": {"id": recovery.EXPECTED_STATE_IDENTITIES[address][2]}, "sensitive_values": {}} for address in sorted(recovery.EXPECTED_EXISTING_ADDRESSES)]; return {"format_version": "1.0", "terraform_version": recovery.EXPECTED_TOFU_VERSION, "values": {"root_module": {"resources": resources}}}


def _valid_plan(*, state_projection: dict[str, Any] | None = None) -> dict[str, Any]:
    projected = state_projection or _valid_state_projection(); projected_by_address = {item["address"]: item for item in projected["values"]["root_module"]["resources"]}
    def no_op(address: str) -> dict[str, Any]:
        resource = projected_by_address[address]; values, sensitive = deepcopy(resource["values"]), deepcopy(resource["sensitive_values"])
        change = {"actions": ["no-op"], "before": values, "after": deepcopy(values), "after_unknown": {}, "before_sensitive": sensitive, "after_sensitive": deepcopy(sensitive)}  # fmt: skip
        if "identity" in resource: change.update(before_identity=deepcopy(resource["identity"]), after_identity=deepcopy(resource["identity"]))  # fmt: skip
        return {**recovery._resource_change_metadata(address), "change": change}
    changes = [no_op(address) for address in sorted(recovery.EXPECTED_EXISTING_ADDRESSES)]
    changes.extend(deepcopy(recovery._expected_create_change(address)) for address in sorted(recovery.EXPECTED_CREATE_ADDRESSES))
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


def _runtime_snapshot() -> Any: return recovery.RuntimeSnapshot(TOFU, AZURE_CLI, recovery.EXPECTED_TOFU_SHA256, recovery.EXPECTED_AZURE_CLI_SHA256, recovery.EXPECTED_AZURE_CLI_VERSION_SHA256, "f" * 64, "1" * 64)  # fmt: skip
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

    def fake_show_state(value: bytes, *, tofu: Path, data_dir: Path) -> bytes: assert value and tofu == TOFU and data_dir.is_absolute(); return json.dumps(projection).encode()
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
    _install_plan_show(monkeypatch, expected_plan_bytes=plan_bytes, plan=plan or _valid_plan(state_projection=state_projection), state_projection=state_projection)
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
    state_bytes = json.dumps(_valid_state()).encode(); state_projection_bytes = json.dumps(_valid_state_projection()).encode()
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
    (module / ".terraform").mkdir(mode=0o700, exist_ok=True)
    (module / ".terraform" / recovery.TRUSTED_RUNTIME_DIRECTORY_NAME).mkdir(mode=0o700)

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
    state = _valid_state(); state["resources"][0]["provider"] = 'provider["attacker.invalid/provider"]'
    with pytest.raises(recovery.RecoveryPlanError, match="exact contract"): _validate(monkeypatch, state=state)
    embedded = _valid_state(); embedded["resources"][0]["instances"][0]["attributes"]["semantic_field"] = "mutated"
    with pytest.raises(recovery.RecoveryPlanError, match="semantically identical"): _validate(monkeypatch, embedded_state=embedded)
    external = _valid_state(); embedded = deepcopy(external); external["check_results"] = [{"status": "pass"}]; embedded["check_results"] = None
    receipt = _validate(monkeypatch, state=external, embedded_state=embedded)
    assert receipt["state_semantic_sha256"] == recovery._semantic_state_sha256(external, label="state snapshot")


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
    for field, value, message in (
        ("errored", True, "errored"),
        ("resource_drift", [{"address": "drift"}], "drift"),
        ("deferred_changes", [{"reason": "unknown"}], "deferred"),
        ("applyable", False, "applyable"),
        ("complete", False, "complete"),
        ("timestamp", stale, "stale"),
        ("timestamp", future, "future"),
    ):
        plan = _valid_plan()
        plan[field] = value
        rejected(plan, message)
    plan = _valid_plan()
    plan["resource_changes"].append({"address": "azurerm_public_ip.forbidden[0]", "change": {"actions": ["create"]}})  # fmt: skip
    rejected(plan, "address set")
    for path, value, message in (
        (("configuration", "provider_config", "azurerm", "expressions", "features"), [{}], "data-plane opt-out"),
        (("variables", "enable_marker_legal_holds", "value"), True, "not recovery-safe"),
        (("checks", 0, "status"), "fail", "non-passing"),
    ):
        plan = _valid_plan()
        node: Any = plan
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        rejected(plan, message)


def test_noop_values_sensitivity_identity_and_prior_state_are_exact(monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    projection = _valid_state_projection(); projected = projection["values"]["root_module"]["resources"][0]
    projected.update(values={**projected["values"], "binding": "protected"}, sensitive_values={"binding": True}, identity={"key": "state-bound"}, identity_schema_version=0)  # fmt: skip
    base = _valid_plan(state_projection=projection); address = projected["address"]
    for field, value in (("before", {"id": "different"}), ("after", {"id": "different"}), ("before_sensitive", {}), ("after_identity", {"key": "different"}), ("action_reason", [])):  # fmt: skip
        plan = deepcopy(base); change = next(item for item in plan["resource_changes"] if item["address"] == address)["change"]; change[field] = value  # fmt: skip
        with pytest.raises(recovery.RecoveryPlanError): _validate(monkeypatch, plan=plan, state_projection=projection)
    plan = deepcopy(base); plan["prior_state"]["values"]["root_module"]["resources"][0]["values"]["binding"] = "different"  # fmt: skip
    with pytest.raises(recovery.RecoveryPlanError, match="prior state"): _validate(monkeypatch, plan=plan, state_projection=projection)


def test_rejected_plan_digest_cannot_be_reauthorized(monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    plan = _saved_plan_bytes(); monkeypatch.setattr(recovery, "REJECTED_SAVED_PLAN_SHA256S", frozenset({recovery._sha256_bytes(plan)}))  # fmt: skip
    with pytest.raises(recovery.RecoveryPlanError, match="permanently rejected"): _validate(monkeypatch, saved_plan_bytes=plan)


CREATE_MUTATIONS = [('azapi_resource.marker_container["restore-cleanups-v4"]', ("change", "after", "parent_id"), "/subscriptions/attacker/storage"), ("azurerm_private_endpoint.marker[0]", ("change", "after", "subnet_id"), "/subscriptions/attacker/subnet"), ("azurerm_private_endpoint.marker[0]", ("change", "after", "private_service_connection", 0, "private_connection_resource_id"), "/subscriptions/attacker/storage"), ('azurerm_role_assignment.marker_read["cleanup_intent"]', ("change", "after", "principal_id"), "00000000-0000-0000-0000-000000000000"), ("azurerm_role_assignment.marker_provision_cleanup[0]", ("change", "after", "role_definition_id"), "/subscriptions/attacker/role"), ("azurerm_private_endpoint.marker[0]", ("change", "after_unknown", "subnet_id"), True), ("azurerm_private_endpoint.marker[0]", ("provider_name",), "registry.opentofu.org/attacker/provider"), ('azapi_resource.marker_container["restore-cleanups-v4"]', ("change", "replace_paths"), [])]
@pytest.mark.parametrize(("address", "path", "value"), CREATE_MUTATIONS)
def test_create_contract_rejects_exact_value_or_schema_mutation(monkeypatch: pytest.MonkeyPatch, address: str, path: tuple[str | int, ...], value: Any) -> None:
    plan = _valid_plan(); change = next(item for item in plan["resource_changes"] if item["address"] == address)  # fmt: skip
    node: Any = change
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(recovery.RecoveryPlanError, match="exact create contract"): _validate(monkeypatch, plan=plan)

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


def test_runtime_executables_and_versions_are_snapshotted_and_pinned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, data_dir = tmp_path / "source", tmp_path / "data"
    source.mkdir(mode=0o700); data_dir.mkdir(mode=0o700)  # noqa: E702
    runtime_directory = data_dir / recovery.TRUSTED_RUNTIME_DIRECTORY_NAME; runtime_directory.mkdir(mode=0o700); stale = runtime_directory / ".azure-config-zzz-safe"; stale.mkdir(mode=0o700); _protected_file(stale / "msal_token_cache.json", b"stale", mode=0o600); stale.chmod(0o500)  # noqa: E702
    tofu = _protected_file(source / "tofu", b"tofu-binary", mode=0o500)
    azure_cli = _protected_file(source / "az", b"azure-cli", mode=0o500)
    azure_version = {"azure-cli": "2.89.1", "extensions": {}}
    pins = {"EXPECTED_TOFU_PATH": tofu, "EXPECTED_AZURE_CLI_PATH": azure_cli, "EXPECTED_TOFU_SHA256": hashlib.sha256(tofu.read_bytes()).hexdigest(), "EXPECTED_AZURE_CLI_SHA256": hashlib.sha256(azure_cli.read_bytes()).hexdigest(), "EXPECTED_AZURE_CLI_VERSION_SHA256": hashlib.sha256(recovery._canonical_json_bytes(azure_version)).hexdigest(), "EXPECTED_AZURE_CLOUD_SHA256": hashlib.sha256(recovery._canonical_json_bytes(azure_version)).hexdigest()}
    for name, value in pins.items(): monkeypatch.setattr(recovery, name, value)  # noqa: E701
    monkeypatch.setattr(recovery, "_trusted_executable_source", lambda path, **_: path)
    closure_sha256 = "c" * 64

    def fake_snapshot_azure_cli_closure(runtime_directory: Path) -> tuple[Path, str]:
        (runtime_directory / "homebrew").mkdir(mode=0o700, exist_ok=True); (runtime_directory / "homebrew").chmod(0o700); wrapper = runtime_directory / "az"
        if not wrapper.exists(): wrapper.write_bytes(recovery._azure_cli_wrapper(runtime_directory)); wrapper.chmod(0o500)  # noqa: E702
        return wrapper, closure_sha256

    def fake_run(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        assert kwargs["data_dir"] == data_dir; output = b'{"terraform_version":"1.12.5"}' if arguments[-2:] == ["version", "-json"] else json.dumps(azure_version).encode()
        return subprocess.CompletedProcess(arguments, 0, output, b"")

    monkeypatch.setattr(recovery, "_run_process", fake_run); monkeypatch.setattr(recovery, "_snapshot_azure_cli_closure", fake_snapshot_azure_cli_closure)  # noqa: E702
    unsafe_mode = runtime_directory / ".azure-config-aaa-unsafe"; unsafe_mode.mkdir(mode=0o700); _protected_file(unsafe_mode / "msal_token_cache.json", b"credential", mode=0o600); unsafe_mode.chmod(0o755)  # noqa: E702
    with pytest.raises(recovery.RecoveryPlanError, match="snapshot is unsafe"): recovery._snapshot_and_verify_runtime(data_dir=data_dir)
    assert not unsafe_mode.exists() and not any(path.name == "msal_token_cache.json" for path in runtime_directory.rglob("*"))
    if sys.platform == "darwin":
        unsafe_acl = runtime_directory / ".azure-config-unsafe-acl"; unsafe_acl.mkdir(mode=0o700); _protected_file(unsafe_acl / "msal_token_cache.json", b"credential", mode=0o600)  # noqa: E702
        subprocess.run(["/bin/chmod", "+a", "everyone allow read", str(unsafe_acl)], check=True)
        with pytest.raises(recovery.RecoveryPlanError, match="snapshot is unsafe"): recovery._snapshot_and_verify_runtime(data_dir=data_dir)
        assert not unsafe_acl.exists() and not any(path.name == "msal_token_cache.json" for path in runtime_directory.rglob("*"))
    previous_umask = os.umask(0o777)
    try:
        runtime = recovery._snapshot_and_verify_runtime(data_dir=data_dir); assert recovery._snapshot_and_verify_runtime(data_dir=data_dir) == runtime
    finally:
        os.umask(previous_umask)
    assert runtime.tofu.read_bytes() == tofu.read_bytes() and runtime.azure_cli.read_bytes() == recovery._azure_cli_wrapper(runtime.azure_cli.parent) and not stale.exists() and not (runtime.azure_cli.parent / "azure-home").exists()
    assert runtime.azure_cli_closure_sha256 == closure_sha256 and runtime.tofu.stat().st_mode & 0o777 == runtime.azure_cli.stat().st_mode & 0o777 == 0o500
    null_home_binding = recovery._validated_null_home()
    with open(os.devnull, "wb") as sink:
        sink.write(b"probe")
    assert recovery._azure_cli_environment(runtime.azure_cli.parent)["HOME"] == os.devnull and stat.S_ISCHR(os.stat(os.devnull, follow_symlinks=False).st_mode) and recovery._validated_null_home() == null_home_binding
    runtime.azure_cli.chmod(0o700)
    with pytest.raises(recovery.RecoveryPlanError, match="snapshot is not exact"): recovery._snapshot_and_verify_runtime(data_dir=data_dir)


def test_azure_cli_closure_digest_binds_dependency_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve() / "homebrew"; formula = root / "Cellar" / "azure-cli" / "1"  # noqa: E702  # fmt: skip
    formula.mkdir(parents=True); dependency = _protected_file(formula / "dependency.py", b"trusted", mode=0o400)
    opt = root / "opt"; opt.mkdir()  # noqa: E702
    (opt / "azure-cli").symlink_to("../Cellar/azure-cli/1")
    monkeypatch.setattr(recovery, "EXPECTED_AZURE_CLI_FORMULAE", {"azure-cli": "1"})
    digest = lambda: recovery._runtime_entries_sha256(recovery._azure_cli_closure_entries(root, snapshot=False))  # noqa: E731  # fmt: skip
    captured = bytearray(); record = recovery._runtime_file_record(dependency, label="dependency", capture=captured); before = digest()
    dependency.chmod(0o600); dependency.write_bytes(b"changed"); dependency.chmod(0o400)
    assert bytes(captured) == b"trusted" and record != recovery._runtime_file_record(dependency, label="dependency") and before != digest()  # fmt: skip


def test_cli_cloud_environment_is_pinned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, _ = _azure_home(tmp_path, monkeypatch); scrubbed = "TF_CLI_ARGS TOFU_CLI_ARGS_plan OPENTOFU_CLI_ARGS TF_VAR_enable_marker_legal_holds TF_LOG_PATH GIT_DIR HTTPS_PROXY SSL_CERT_FILE REQUESTS_CA_BUNDLE PYTHONPATH AZURE_CONFIG_DIR ARM_TENANT_ID PATH".split()
    for name in scrubbed:
        monkeypatch.setenv(name, "hostile")
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    runtime_directory = DATA_DIR / recovery.TRUSTED_RUNTIME_DIRECTORY_NAME
    environment = recovery._normalized_environment(data_dir=DATA_DIR); expected = {"ARM_ENVIRONMENT": "public", "ARM_SUBSCRIPTION_ID": recovery.EXPECTED_SUBSCRIPTION_ID, "ARM_TENANT_ID": recovery.EXPECTED_TENANT_ID, "ARM_USE_AZUREAD": "true", "ARM_USE_CLI": "true", "AZURE_CONFIG_DIR": str(home / ".azure"), recovery.AZURE_CONFIG_BINDING_ENV: recovery._validated_azure_config(home)[1], "AZURE_CORE_COLLECT_TELEMETRY": "false", "AZURE_CORE_DISABLE_CONFIRM_PROMPT": "true", "AZURE_CORE_ONLY_SHOW_ERRORS": "true", "AZURE_EXTENSION_USE_DYNAMIC_INSTALL": "no", "AZURE_LOGGING_ENABLE_LOG_FILE": "false", "CHECKPOINT_DISABLE": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_NO_REPLACE_OBJECTS": "1", "GIT_TERMINAL_PROMPT": "0", "HOME": os.devnull, "LANG": "C", "LC_ALL": "C", "NETRC": os.devnull, "PATH": f"{runtime_directory}:/usr/bin:/bin", "TF_CLI_CONFIG_FILE": os.devnull, "TF_DATA_DIR": str(DATA_DIR), "TF_IN_AUTOMATION": "1", "TF_INPUT": "0"}
    expected.update(recovery._azure_cli_environment(runtime_directory))
    assert environment == expected


def test_azure_cli_wrapper_uses_private_bound_config_and_refuses_late_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, values = _azure_home(tmp_path, monkeypatch); config, binding = recovery._validated_azure_config(home); state_refresh_source = (ROOT / "scripts/validate_staging_rehearsal_base_state_refresh_plan.py").read_text(); assert state_refresh_source.count("recovery._snapshot_and_verify_runtime(data_dir=") == 2 and "recovery._resolve_and_verify_tofu()" not in state_refresh_source  # noqa: E702
    runtime, modules = tmp_path / "runtime", tmp_path / "modules"; runtime.mkdir(mode=0o700); (home / ".netrc").write_bytes(b"hostile"); (modules / "azure" / "cli").mkdir(parents=True)
    (modules / "azure" / "__init__.py").write_bytes(b""); (modules / "azure" / "cli" / "__init__.py").write_bytes(b"")  # noqa: E702
    (modules / "azure" / "cli" / "__main__.py").write_text('import __main__,json,os,signal,subprocess,sys,time\nfrom pathlib import Path\np=Path(os.environ["AZURE_CONFIG_DIR"])\nif "break-cleanup" in sys.argv:\n old=p.with_name(p.name+"-moved");p.rename(old);p.mkdir()\nif "rename-cleanup" in sys.argv:\n p.rename(p.with_name(p.name+"-moved"));raise SystemExit(0)\nif "rename-file-cleanup" in sys.argv:(p/"msal_token_cache.json").rename(p.parent/"escaped-token")\nif "tighten-cleanup" in sys.argv:p.chmod(0o500)\nif "acl-cleanup" in sys.argv:subprocess.run(["/bin/chmod","+a","everyone allow read",str(p)],check=True)\nif "mutate-home-during" in sys.argv:(Path.home()/".netrc").write_bytes(b"hostile")\nif "mutate-home-exit" in sys.argv:os.environ["HOME"]=sys.argv[-1];raise SystemExit(0)\nif "hardlink-cleanup" in sys.argv:os.link(p/"msal_token_cache.json",sys.argv[-1])\nif "symlink-cleanup" in sys.argv:\n victim=p/"msal_token_cache.json";victim.unlink();victim.symlink_to(sys.argv[-1])\nif "same-device-mount-cleanup" in sys.argv:\n mounted=p/"mounted";mounted.mkdir();(mounted/"sentinel").write_bytes(b"keep");original_mount_identity=__main__.mount_identity;mount_calls=0\n def changed_mount_identity(fd):\n  global mount_calls\n  mount_calls+=1;value=original_mount_identity(fd);return ("foreign-mount",) if mount_calls >= 3 else value\n __main__.mount_identity=changed_mount_identity\nif "wait-signal" in sys.argv:\n print("ready",flush=True);time.sleep(30)\nif "signal-cleanup-entry" in sys.argv:\n original_pthread_sigmask=signal.pthread_sigmask;pause_cleanup=True\n def pthread_sigmask(how,mask):\n  global pause_cleanup\n  if pause_cleanup:\n   pause_cleanup=False;print("cleanup-entry-ready",flush=True);time.sleep(0.5)\n  return original_pthread_sigmask(how,mask)\n signal.pthread_sigmask=pthread_sigmask\nif "signal-cleanup" in sys.argv:\n original_fchmod=os.fchmod\n def fchmod(fd,mode):\n  if mode == 0o700:print("snapshot-cleanup-ready",flush=True);time.sleep(0.5)\n  return original_fchmod(fd,mode)\n os.fchmod=fchmod\nprint(json.dumps({"path":str(p),"files":sorted(x.name for x in p.iterdir()),"home":os.environ["HOME"],"netrc":(Path.home()/".netrc").exists()}),flush=True)\n')
    python = runtime / "homebrew" / "Cellar" / "python@3.14" / recovery.EXPECTED_AZURE_CLI_FORMULAE["python@3.14"] / "Frameworks" / "Python.framework" / "Versions" / "3.14" / "bin" / "python3.14"
    python.parent.mkdir(parents=True); python.symlink_to(sys.executable)
    assert recovery._azure_cli_environment(runtime)["HOME"] == os.devnull; monkeypatch.setattr(recovery, "_azure_cli_environment", lambda _: {"HOME": os.devnull, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(modules), "PYTHONNOUSERSITE": "1", "PYTHONSAFEPATH": "1"})
    lock = runtime / recovery.AZURE_CONFIG_LOCK_NAME; lock.touch(mode=0o600); lock.chmod(0o600); wrapper = runtime / "az"; wrapper.write_bytes(recovery._azure_cli_wrapper(runtime)); wrapper.chmod(0o500)  # noqa: E702
    environment = {"AZURE_CONFIG_DIR": str(config), recovery.AZURE_CONFIG_BINDING_ENV: binding}
    result = subprocess.run([wrapper, "version"], env=environment, capture_output=True, check=False)
    assert result.returncode == 0, (result.stdout, result.stderr)
    observed = json.loads(result.stdout)
    assert result.returncode == 0 and observed["path"] != str(config) and observed["files"] == sorted(values) and observed["home"] == os.devnull and not observed["netrc"] and not Path(observed["path"]).exists()
    environment[recovery.AZURE_CONFIG_BINDING_ENV] = recovery._validated_azure_config(home)[1]; boundary = subprocess.run([wrapper, "same-device-mount-cleanup"], env=environment, capture_output=True, check=False)  # noqa: E702
    retained = [path for path in runtime.iterdir() if path.name.startswith(".azure-config-")]
    assert boundary.returncode == 2 and boundary.stderr == b"azure_cli_guard_refused\n" and len(retained) == 1 and (retained[0] / "mounted" / "sentinel").read_bytes() == b"keep" and not any(path.name in values for path in runtime.rglob("*"))
    shutil.rmtree(retained[0])
    hardlink_external = tmp_path / "hardlink-external"; environment[recovery.AZURE_CONFIG_BINDING_ENV] = recovery._validated_azure_config(home)[1]  # noqa: E702
    hardlink = subprocess.run([wrapper, "hardlink-cleanup", str(hardlink_external)], env=environment, capture_output=True, check=False)
    retained = [path for path in runtime.iterdir() if path.name.startswith(".azure-config-")]
    assert hardlink.returncode == 2 and hardlink.stderr == b"azure_cli_guard_refused\n" and hardlink_external.read_bytes() == values["msal_token_cache.json"] and len(retained) == 1 and not any(retained[0].iterdir())
    retained[0].rmdir()
    escaped_token = runtime / "escaped-token"; environment[recovery.AZURE_CONFIG_BINDING_ENV] = recovery._validated_azure_config(home)[1]  # noqa: E702
    renamed_file = subprocess.run([wrapper, "rename-file-cleanup"], env=environment, capture_output=True, check=False)
    assert renamed_file.returncode == 2 and renamed_file.stderr == b"azure_cli_guard_refused\n" and escaped_token.read_bytes() == values["msal_token_cache.json"] and not any(path.name.startswith(".azure-config-") for path in runtime.iterdir())
    escaped_token.unlink()
    symlink_target = tmp_path / "symlink-target"; symlink_target.write_bytes(b"keep"); environment[recovery.AZURE_CONFIG_BINDING_ENV] = recovery._validated_azure_config(home)[1]  # noqa: E702
    symlink = subprocess.run([wrapper, "symlink-cleanup", str(symlink_target)], env=environment, capture_output=True, check=False)
    retained = [path for path in runtime.iterdir() if path.name.startswith(".azure-config-")]
    assert symlink.returncode == 2 and symlink.stderr == b"azure_cli_guard_refused\n" and symlink_target.read_bytes() == b"keep" and len(retained) == 1 and not any(retained[0].iterdir())
    retained[0].rmdir()
    environment[recovery.AZURE_CONFIG_BINDING_ENV] = recovery._validated_azure_config(home)[1]; mutated_home = subprocess.run([wrapper, "mutate-home-during"], env=environment, capture_output=True, check=False)  # noqa: E702
    assert mutated_home.returncode == 2 and mutated_home.stderr == b"azure_cli_guard_refused\n" and not (Path(os.devnull) / ".netrc").exists() and not any(path.name.startswith(".azure-config-") for path in runtime.iterdir())
    hostile_exit_home = tmp_path / "hostile-exit-home"; hostile_exit_home.mkdir(mode=0o700); environment[recovery.AZURE_CONFIG_BINDING_ENV] = recovery._validated_azure_config(home)[1]  # noqa: E702
    mutated_home_exit = subprocess.run([wrapper, "mutate-home-exit", str(hostile_exit_home)], env=environment, capture_output=True, check=False)
    assert mutated_home_exit.returncode == 2 and mutated_home_exit.stderr == b"azure_cli_guard_refused\n" and not any(path.name.startswith(".azure-config-") for path in runtime.iterdir())
    for name in ("config", "msal_token_cache.json"):
        environment[recovery.AZURE_CONFIG_BINDING_ENV] = recovery._validated_azure_config(home)[1]; (config / name).write_bytes(b"changed"); refused = subprocess.run([wrapper, "version"], env=environment, capture_output=True, check=False)
        assert refused.returncode == 2 and refused.stdout == b"" and refused.stderr == b"azure_cli_guard_refused\n"; (config / name).write_bytes(values[name])
    environment[recovery.AZURE_CONFIG_BINDING_ENV] = recovery._validated_azure_config(home)[1]
    moved = home / ".azure-old"; config.rename(moved); shutil.copytree(moved, config)
    refused = subprocess.run([wrapper, "version"], env=environment, capture_output=True, check=False)
    assert refused.returncode == 2 and refused.stderr == b"azure_cli_guard_refused\n"
    shutil.rmtree(config); moved.rename(config)
    stale_mode = runtime / ".azure-config-aaa-unsafe"; stale_safe = runtime / ".azure-config-zzz-safe"; stale_mode.mkdir(mode=0o700); stale_safe.mkdir(mode=0o700); _protected_file(stale_mode / "msal_token_cache.json", b"credential", mode=0o600); _protected_file(stale_safe / "azureProfile.json", b"credential", mode=0o600); stale_mode.chmod(0o755)  # noqa: E702
    environment[recovery.AZURE_CONFIG_BINDING_ENV] = recovery._validated_azure_config(home)[1]; refused = subprocess.run([wrapper, "version"], env=environment, capture_output=True, check=False)  # noqa: E702
    assert refused.returncode == 2 and refused.stderr == b"azure_cli_guard_refused\n" and not stale_mode.exists() and not stale_safe.exists() and not any(path.name in values for path in runtime.rglob("*"))
    if sys.platform == "darwin":
        stale_acl = runtime / ".azure-config-stale-acl"; stale_acl.mkdir(mode=0o700); _protected_file(stale_acl / "msal_token_cache.json", b"credential", mode=0o600)  # noqa: E702
        subprocess.run(["/bin/chmod", "+a", "everyone allow read", str(stale_acl)], check=True)
        environment[recovery.AZURE_CONFIG_BINDING_ENV] = recovery._validated_azure_config(home)[1]; refused = subprocess.run([wrapper, "version"], env=environment, capture_output=True, check=False)  # noqa: E702
        assert refused.returncode == 2 and refused.stderr == b"azure_cli_guard_refused\n" and not stale_acl.exists() and not any(path.name in values for path in runtime.rglob("*"))
    sitecustomize = modules / "sitecustomize.py"
    stale_rename = runtime / ".azure-config-stale-rename"; moved_stale = runtime / ".azure-config-stale-renamed"; stale_rename.mkdir(mode=0o700); _protected_file(stale_rename / "msal_token_cache.json", b"credential", mode=0o600)  # noqa: E702
    sitecustomize.write_text(f'import os\nfrom pathlib import Path\nstale=Path({str(stale_rename)!r});moved=Path({str(moved_stale)!r});original_fchmod=os.fchmod\ndef fchmod(fd,mode):\n result=original_fchmod(fd,mode)\n if mode == 0o700 and stale.exists():stale.rename(moved)\n return result\nos.fchmod=fchmod\n')
    environment[recovery.AZURE_CONFIG_BINDING_ENV] = recovery._validated_azure_config(home)[1]; renamed_stale = subprocess.run([wrapper, "version"], env=environment, capture_output=True, check=False)  # noqa: E702
    assert renamed_stale.returncode == 2 and renamed_stale.stderr == b"azure_cli_guard_refused\n" and not stale_rename.exists() and moved_stale.is_dir() and not any(moved_stale.iterdir()) and not any(path.name in values for path in runtime.rglob("*"))
    moved_stale.rmdir()
    sitecustomize.write_text('import os,time\noriginal_open=os.open\npaused=False\ndef open(path,flags,mode=0o777,*,dir_fd=None):\n global paused\n result=original_open(path,flags,mode,dir_fd=dir_fd)\n if path == "pause-cleanup" and dir_fd is not None and not paused:\n  paused=True;print("stale-cleanup-ready",flush=True);time.sleep(0.5)\n return result\nos.open=open\n')
    stale = runtime / ".azure-config-stale-signal"; pause = stale / "pause-cleanup"; stale.mkdir(mode=0o700); pause.mkdir(mode=0o700); _protected_file(pause / "msal_token_cache.json", b"stale", mode=0o600)  # noqa: E702
    environment[recovery.AZURE_CONFIG_BINDING_ENV] = recovery._validated_azure_config(home)[1]; process = subprocess.Popen([wrapper, "version"], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)  # noqa: E702
    assert process.stdout is not None; selector = selectors.DefaultSelector(); selector.register(process.stdout, selectors.EVENT_READ); assert selector.select(5) and process.stdout.readline() == b"stale-cleanup-ready\n"; selector.close()  # noqa: E702
    process.send_signal(signal.SIGTERM); stdout, stderr = process.communicate(timeout=5)  # noqa: E702
    assert process.returncode == 2 and stdout == b"" and stderr == b"azure_cli_guard_refused\n" and not stale.exists() and not any(path.name in values for path in runtime.rglob("*"))
    sitecustomize.write_text('import os,time\noriginal_mkdir=os.mkdir\ndef mkdir(path,mode=0o777,*,dir_fd=None):\n result=original_mkdir(path,mode,dir_fd=dir_fd)\n if isinstance(path,str) and path.startswith(".azure-config-"):\n  print("snapshot-created",flush=True);time.sleep(0.5)\n return result\nos.mkdir=mkdir\n')
    for creation_signal in (signal.SIGHUP, signal.SIGINT):
        environment[recovery.AZURE_CONFIG_BINDING_ENV] = recovery._validated_azure_config(home)[1]; process = subprocess.Popen([wrapper, "version"], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)  # noqa: E702
        assert process.stdout is not None; selector = selectors.DefaultSelector(); selector.register(process.stdout, selectors.EVENT_READ); assert selector.select(5) and process.stdout.readline() == b"snapshot-created\n"; selector.close()  # noqa: E702
        process.send_signal(creation_signal); stdout, stderr = process.communicate(timeout=5)  # noqa: E702
        assert process.returncode == 2 and stdout == b"" and stderr == b"azure_cli_guard_refused\n" and not any(path.name.startswith(".azure-config-") for path in runtime.iterdir()) and not any(path.name in values for path in runtime.rglob("*"))
    sitecustomize.unlink()
    sitecustomize.write_text('import os,time\noriginal_setitem=os.environ.__class__.__setitem__\ndef setitem(self,key,value):\n if key == "AZURE_CONFIG_DIR" and isinstance(value,str) and ".azure-config-" in value:\n  print("snapshot-assignment-ready",flush=True);time.sleep(0.5)\n return original_setitem(self,key,value)\nos.environ.__class__.__setitem__=setitem\n')
    environment[recovery.AZURE_CONFIG_BINDING_ENV] = recovery._validated_azure_config(home)[1]; process = subprocess.Popen([wrapper, "version"], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)  # noqa: E702
    assert process.stdout is not None; selector = selectors.DefaultSelector(); selector.register(process.stdout, selectors.EVENT_READ); assert selector.select(5) and process.stdout.readline() == b"snapshot-assignment-ready\n"; selector.close()  # noqa: E702
    process.send_signal(signal.SIGTERM); stdout, stderr = process.communicate(timeout=5)  # noqa: E702
    assert process.returncode == 2 and stdout == b"" and stderr == b"azure_cli_guard_refused\n" and not any(path.name.startswith(".azure-config-") for path in runtime.iterdir()) and not any(path.name in values for path in runtime.rglob("*"))
    hostile_home = tmp_path / "hostile-home"; hostile_home.mkdir(mode=0o700)
    sitecustomize.write_text(f'import os\noriginal_setitem=os.environ.__class__.__setitem__\ndef setitem(self,key,value):\n if key == "AZURE_CONFIG_DIR" and isinstance(value,str) and ".azure-config-" in value:original_setitem(self,"HOME",{str(hostile_home)!r})\n return original_setitem(self,key,value)\nos.environ.__class__.__setitem__=setitem\n')
    environment[recovery.AZURE_CONFIG_BINDING_ENV] = recovery._validated_azure_config(home)[1]; home_changed_before_module = subprocess.run([wrapper, "version"], env=environment, capture_output=True, check=False)  # noqa: E702
    assert home_changed_before_module.returncode == 2 and home_changed_before_module.stdout == b"" and home_changed_before_module.stderr == b"azure_cli_guard_refused\n" and not any(hostile_home.iterdir()) and not any(path.name.startswith(".azure-config-") for path in runtime.iterdir())
    mkdir_marker = tmp_path / "snapshot-mkdir-hook-fired"; fstat_marker = tmp_path / "snapshot-fstat-hook-fired"
    sitecustomize.write_text(f'import os\nfrom pathlib import Path\noriginal_mkdir=os.mkdir\noriginal_fstat=os.fstat\nsnapshot_created=False\nfailed=False\ndef mkdir(path,mode=0o777,*,dir_fd=None):\n global snapshot_created\n result=original_mkdir(path,mode,dir_fd=dir_fd)\n if isinstance(path,str) and path.startswith(".azure-config-"):\n  snapshot_created=True;Path({str(mkdir_marker)!r}).touch()\n return result\ndef fstat(fd):\n global failed\n if snapshot_created and not failed:\n  failed=True;Path({str(fstat_marker)!r}).touch();raise OSError("injected fstat failure")\n return original_fstat(fd)\nos.mkdir=mkdir\nos.fstat=fstat\n')
    environment[recovery.AZURE_CONFIG_BINDING_ENV] = recovery._validated_azure_config(home)[1]; refused = subprocess.run([wrapper, "version"], env=environment, capture_output=True, check=False)  # noqa: E702
    assert refused.returncode == 2 and refused.stdout == b"" and refused.stderr == b"azure_cli_guard_refused\n" and mkdir_marker.is_file() and fstat_marker.is_file() and not any(path.name.startswith(".azure-config-") for path in runtime.iterdir())
    device_marker = tmp_path / "snapshot-device-hook-fired"
    sitecustomize.write_text(f'import os,stat\nfrom pathlib import Path\noriginal_mkdir=os.mkdir\noriginal_fstat=os.fstat\nsnapshot_created=False\nchanged=False\ndef mkdir(path,mode=0o777,*,dir_fd=None):\n global snapshot_created\n result=original_mkdir(path,mode,dir_fd=dir_fd)\n if isinstance(path,str) and path.startswith(".azure-config-"):snapshot_created=True\n return result\ndef fstat(fd):\n global changed\n result=original_fstat(fd)\n if snapshot_created and not changed and stat.S_ISDIR(result.st_mode):\n  changed=True;Path({str(device_marker)!r}).touch();values=list(result);values[2]+=1;return os.stat_result(values)\n return result\nos.mkdir=mkdir\nos.fstat=fstat\n')
    environment[recovery.AZURE_CONFIG_BINDING_ENV] = recovery._validated_azure_config(home)[1]; refused = subprocess.run([wrapper, "version"], env=environment, capture_output=True, check=False)  # noqa: E702
    retained = [path for path in runtime.iterdir() if path.name.startswith(".azure-config-")]
    assert refused.returncode == 2 and refused.stdout == b"" and refused.stderr == b"azure_cli_guard_refused\n" * 2 and device_marker.is_file() and not any(path.name in values for path in runtime.rglob("*"))
    for path in retained: path.rmdir()
    sitecustomize.unlink(); environment[recovery.AZURE_CONFIG_BINDING_ENV] = recovery._validated_azure_config(home)[1]; renamed_current = subprocess.run([wrapper, "rename-cleanup"], env=environment, capture_output=True, check=False)  # noqa: E702
    renamed_snapshots = [path for path in runtime.iterdir() if path.name.startswith(".azure-config-") and path.name.endswith("-moved")]
    assert renamed_current.returncode == 2 and renamed_current.stderr == b"azure_cli_guard_refused\n" and len(renamed_snapshots) == 1 and not any(renamed_snapshots[0].iterdir()) and not any(path.name in values for path in runtime.rglob("*"))
    renamed_snapshots[0].rmdir()
    environment[recovery.AZURE_CONFIG_BINDING_ENV] = recovery._validated_azure_config(home)[1]; restricted_umask = subprocess.run([wrapper, "version"], env=environment, capture_output=True, check=False, umask=0o777)  # noqa: E702
    assert restricted_umask.returncode == 0 and not any(path.name.startswith(".azure-config-") for path in runtime.iterdir())
    environment[recovery.AZURE_CONFIG_BINDING_ENV] = recovery._validated_azure_config(home)[1]; tightened = subprocess.run([wrapper, "tighten-cleanup"], env=environment, capture_output=True, check=False)  # noqa: E702
    assert tightened.returncode == 2 and tightened.stderr == b"azure_cli_guard_refused\n" and not any(path.name.startswith(".azure-config-") for path in runtime.iterdir()) and not any(path.name in values for path in runtime.rglob("*"))
    if sys.platform == "darwin":
        environment[recovery.AZURE_CONFIG_BINDING_ENV] = recovery._validated_azure_config(home)[1]; acl_changed = subprocess.run([wrapper, "acl-cleanup"], env=environment, capture_output=True, check=False)  # noqa: E702
        assert acl_changed.returncode == 2 and acl_changed.stderr == b"azure_cli_guard_refused\n" and not any(path.name.startswith(".azure-config-") for path in runtime.iterdir()) and not any(path.name in values for path in runtime.rglob("*"))
    environment[recovery.AZURE_CONFIG_BINDING_ENV] = recovery._validated_azure_config(home)[1]; process = subprocess.Popen([wrapper, "signal-cleanup-entry"], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)  # noqa: E702
    assert process.stdout is not None; selector = selectors.DefaultSelector(); selector.register(process.stdout, selectors.EVENT_READ); assert selector.select(5); first_line, cleanup_line = process.stdout.readline(), process.stdout.readline(); assert cleanup_line == b"cleanup-entry-ready\n", (first_line, cleanup_line); selector.close()  # noqa: E702
    process.send_signal(signal.SIGTERM); stdout, stderr = process.communicate(timeout=5)  # noqa: E702
    assert process.returncode == 128 + signal.SIGTERM and stdout == b"" and stderr == b"" and not any(path.name.startswith(".azure-config-") for path in runtime.iterdir()) and not any(path.name in values for path in runtime.rglob("*"))
    environment[recovery.AZURE_CONFIG_BINDING_ENV] = recovery._validated_azure_config(home)[1]; process = subprocess.Popen([wrapper, "signal-cleanup"], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)  # noqa: E702
    assert process.stdout is not None; selector = selectors.DefaultSelector(); selector.register(process.stdout, selectors.EVENT_READ); assert selector.select(5); first_line, cleanup_line = process.stdout.readline(), process.stdout.readline(); assert cleanup_line == b"snapshot-cleanup-ready\n", (first_line, cleanup_line); selector.close()  # noqa: E702
    process.send_signal(signal.SIGINT); stdout, stderr = process.communicate(timeout=5)  # noqa: E702
    assert process.returncode == 128 + signal.SIGINT and stdout == b"" and stderr == b"" and not any(path.name.startswith(".azure-config-") for path in runtime.iterdir()) and not any(path.name in values for path in runtime.rglob("*"))
    for termination_signal in (signal.SIGHUP, signal.SIGINT, signal.SIGQUIT, signal.SIGTERM):
        environment[recovery.AZURE_CONFIG_BINDING_ENV] = recovery._validated_azure_config(home)[1]; process = subprocess.Popen([wrapper, "wait-signal"], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)  # noqa: E702
        assert process.stdout is not None; selector = selectors.DefaultSelector(); selector.register(process.stdout, selectors.EVENT_READ); assert selector.select(5) and process.stdout.readline() == b"ready\n"; selector.close()  # noqa: E702
        process.send_signal(termination_signal); stdout, stderr = process.communicate(timeout=5)  # noqa: E702
        assert process.returncode == 128 + termination_signal and stdout == b"" and stderr == b"" and not any(path.name in values for path in runtime.rglob("*"))
    environment[recovery.AZURE_CONFIG_BINDING_ENV] = recovery._validated_azure_config(home)[1]; broken = subprocess.run([wrapper, "break-cleanup"], env=environment, capture_output=True, check=False)
    assert broken.returncode == 2 and b"azure_cli_guard_refused\n" in broken.stderr and not any(path.name in values for path in runtime.rglob("*"))
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS extended ACL semantics")
def test_azure_config_and_runtime_acl_grants_are_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, _ = _azure_home(tmp_path, monkeypatch); config = home / ".azure"; runtime = tmp_path / "runtime"; runtime.mkdir(mode=0o700)  # noqa: E702
    for path in (home, config, config / "config"):
        subprocess.run(["/bin/chmod", "+a", "everyone allow read", str(path)], check=True)
        with pytest.raises(recovery.RecoveryPlanError, match="ACL grants or inherits external access"): recovery._validated_azure_config(home)
        subprocess.run(["/bin/chmod", "-N", str(path)], check=True)
    subprocess.run(["/bin/chmod", "+a", "everyone allow read", str(runtime)], check=True)
    with pytest.raises(recovery.RecoveryPlanError, match="ACL grants or inherits external access"): recovery._require_protected_directory(runtime, label="runtime")


def test_stale_snapshot_swap_scrubs_original_without_deleting_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = tmp_path / "runtime"; runtime.mkdir(mode=0o700); stale = runtime / ".azure-config-stale1"; moved = runtime / ".azure-config-moved1"; replacement = runtime / "replacement"; stale.mkdir(mode=0o700); replacement.mkdir(mode=0o700)  # noqa: E702
    _protected_file(stale / "msal_token_cache.json", b"credential", mode=0o600); _protected_file(replacement / "sentinel", b"keep", mode=0o600); real_fchmod = os.fchmod; swapped = False  # noqa: E702
    def swap_after_validation(descriptor: int, mode: int) -> None:
        nonlocal swapped
        real_fchmod(descriptor, mode)
        if not swapped: stale.rename(moved); replacement.rename(stale); swapped = True  # noqa: E701
    monkeypatch.setattr(recovery.os, "fchmod", swap_after_validation)
    with pytest.raises(recovery.RecoveryPlanError, match="snapshot changed"): recovery._remove_stale_azure_config_snapshots(runtime)
    assert (stale / "sentinel").read_bytes() == b"keep" and moved.is_dir() and not any(moved.iterdir())


def test_stale_snapshot_rename_away_scrubs_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = tmp_path / "runtime"; runtime.mkdir(mode=0o700); stale = runtime / ".azure-config-stale1"; moved = runtime / ".azure-config-moved1"; stale.mkdir(mode=0o700)  # noqa: E702
    _protected_file(stale / "msal_token_cache.json", b"credential", mode=0o600); real_fchmod = os.fchmod; renamed = False  # noqa: E702
    def rename_after_open(descriptor: int, mode: int) -> None:
        nonlocal renamed
        real_fchmod(descriptor, mode)
        if not renamed: stale.rename(moved); renamed = True  # noqa: E701
    monkeypatch.setattr(recovery.os, "fchmod", rename_after_open)
    with pytest.raises(recovery.RecoveryPlanError, match="snapshot changed"): recovery._remove_stale_azure_config_snapshots(runtime)
    assert not stale.exists() and moved.is_dir() and not any(moved.iterdir())


def test_stale_cleanup_keeps_lock_bound_to_original_runtime_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = tmp_path / "runtime"; moved = tmp_path / "runtime-moved"; replacement = tmp_path / "runtime-replacement"; runtime.mkdir(mode=0o700); replacement.mkdir(mode=0o700)  # noqa: E702
    old_snapshot = runtime / ".azure-config-old123"; old_snapshot.mkdir(mode=0o700); _protected_file(old_snapshot / "msal_token_cache.json", b"old", mode=0o600)  # noqa: E702
    replacement_snapshot = replacement / ".azure-config-active1"; replacement_snapshot.mkdir(mode=0o700); sentinel = _protected_file(replacement_snapshot / "sentinel", b"keep", mode=0o600)  # noqa: E702
    real_open_lock = recovery._open_azure_config_lock
    def lock_then_replace(parent: int) -> int:
        descriptor = real_open_lock(parent); runtime.rename(moved); replacement.rename(runtime); return descriptor  # noqa: E702
    monkeypatch.setattr(recovery, "_open_azure_config_lock", lock_then_replace)
    with pytest.raises(recovery.RecoveryPlanError, match="snapshot changed"):
        recovery._remove_stale_azure_config_snapshots(runtime)
    assert not (moved / old_snapshot.name).exists() and (runtime / replacement_snapshot.name / sentinel.name).read_bytes() == b"keep"


def test_snapshot_scrub_refuses_same_device_mount_boundary_without_traversing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = tmp_path / "snapshot"; mounted = snapshot / "mounted"; mounted.mkdir(parents=True, mode=0o700); sentinel = _protected_file(mounted / "sentinel", b"keep", mode=0o600)  # noqa: E702
    descriptor = os.open(snapshot, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)); expected_device = os.fstat(descriptor).st_dev; expected_mount = recovery._mount_identity(descriptor)  # noqa: E702
    monkeypatch.setattr(recovery, "_mount_identity", lambda fd: expected_mount if fd == descriptor else ("foreign-mount",))
    try:
        assert not recovery._scrub_azure_config_snapshot(descriptor, expected_device=expected_device, expected_mount=expected_mount)
    finally:
        os.close(descriptor)
    assert sentinel.read_bytes() == b"keep"


@pytest.mark.parametrize("entry_type", ["hardlink", "symlink"])
def test_snapshot_scrub_removes_but_rejects_linked_file(tmp_path: Path, entry_type: str) -> None:
    snapshot = tmp_path / "snapshot"; snapshot.mkdir(mode=0o700); external = _protected_file(tmp_path / "external", b"keep", mode=0o600); entry = snapshot / "credential"  # noqa: E702
    if entry_type == "hardlink": os.link(external, entry)  # noqa: E701
    else: entry.symlink_to(external)  # noqa: E701
    descriptor = os.open(snapshot, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)); metadata = os.fstat(descriptor)  # noqa: E702
    try:
        assert not recovery._scrub_azure_config_snapshot(descriptor, expected_device=metadata.st_dev, expected_mount=recovery._mount_identity(descriptor))
    finally:
        os.close(descriptor)
    assert not entry.exists() and external.read_bytes() == b"keep"


def test_azure_config_capture_rejects_directory_swap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, values = _azure_home(tmp_path, monkeypatch); config = home / ".azure"  # noqa: E702
    config.chmod(0o755)
    with pytest.raises(recovery.RecoveryPlanError, match="path is unsafe"): recovery._validated_azure_config(home)
    config.chmod(0o700); (config / "msal_token_cache.json").chmod(0o644)
    with pytest.raises(recovery.RecoveryPlanError, match="not exact"): recovery._validated_azure_config(home)
    (config / "msal_token_cache.json").chmod(0o600)
    replacement, moved = home / ".replacement", home / ".moved"; shutil.copytree(config, replacement)
    real_open, swapped = os.open, False
    def swap_after_directory_open(path: Any, flags: int, mode: int=0o777, *, dir_fd: int | None=None) -> int:
        nonlocal swapped
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if path == ".azure" and dir_fd is not None and not swapped:
            config.rename(moved); replacement.rename(config); swapped = True  # noqa: E702
        return descriptor
    monkeypatch.setattr(recovery.os, "open", swap_after_directory_open)
    with pytest.raises(recovery.RecoveryPlanError, match="path changed"): recovery._validated_azure_config(home)


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


def test_post_execution_runtime_change_does_not_signal_reaped_process_group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bindings = iter(((1,), (1,), (2,))); signals: list[tuple[int, int]] = []  # noqa: E702
    monkeypatch.setattr(recovery, "_validated_null_home", lambda: next(bindings))
    monkeypatch.setattr(recovery.os, "killpg", lambda pid, value: signals.append((pid, value)))
    with pytest.raises(recovery.RecoveryPlanError, match="null HOME changed during execution"):
        recovery._run_process([sys.executable, "-c", "pass"], cwd=tmp_path, timeout_seconds=5)
    assert signals == []


def test_subprocess_start_failure_is_reported_without_process_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    signals: list[tuple[int, int]] = []
    def refuse_start(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("missing executable")
    monkeypatch.setattr(recovery.subprocess, "Popen", refuse_start)
    monkeypatch.setattr(recovery.os, "killpg", lambda pid, value: signals.append((pid, value)))
    with pytest.raises(recovery.RecoveryPlanError, match="required local command did not complete") as error:
        recovery._run_process(["/missing"], cwd=tmp_path, timeout_seconds=5)
    assert isinstance(error.value.__cause__, OSError) and signals == []


def test_subprocess_refuses_missing_runtime_before_environment_or_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "data"; data_dir.mkdir(mode=0o700); marker = tmp_path / "executed"; calls: list[str] = []  # noqa: E702
    def restore_during_environment_build(**_: Any) -> dict[str, str]:
        calls.append("environment"); (data_dir / recovery.TRUSTED_RUNTIME_DIRECTORY_NAME).mkdir(mode=0o700)
        return {"HOME": os.devnull, "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"}
    monkeypatch.setattr(recovery, "_normalized_environment", restore_during_environment_build)
    with pytest.raises(recovery.RecoveryPlanError, match="trusted runtime directory (?:path )?is unavailable"):
        recovery._run_process([sys.executable, "-c", f"from pathlib import Path;Path({str(marker)!r}).touch()"], cwd=tmp_path, timeout_seconds=5, data_dir=data_dir)
    assert calls == [] and not marker.exists() and not (data_dir / recovery.TRUSTED_RUNTIME_DIRECTORY_NAME).exists()
