from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/validate_staging_evidence_manifest.py"
POLICY = ROOT / "contracts/staging-rehearsal/v1/evidence-manifest-policy.json"
SERVER_SCOPE_SCRIPT = ROOT / "scripts/staging_server_scope_evidence.py"
SERVER_SCOPE_POLICY = (
    ROOT / "contracts/staging-rehearsal/v1/server-scope-evidence-policy.json"
)
SERVER_SCOPE_AUTHENTICATION_SCRIPT = (
    ROOT / "scripts/authenticate_staging_server_scope_receipt.py"
)
SERVER_SCOPE_AUTHENTICATION_POLICY = (
    ROOT / "contracts/staging-rehearsal/v1/server-scope-authentication-policy.json"
)
SERVER_SCOPE_KEY = b"server-scope-manifest-test-key-at-least-32-bytes"
NOW = datetime(2026, 9, 3, 12, 5, tzinfo=UTC)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "standalone_staging_evidence_manifest",
        SCRIPT,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_server_scope_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "standalone_staging_server_scope_for_manifest",
        SERVER_SCOPE_SCRIPT,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_server_scope_authentication_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "standalone_staging_server_scope_authentication_for_manifest",
        SERVER_SCOPE_AUTHENTICATION_SCRIPT,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(ROOT))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


@pytest.fixture
def module() -> ModuleType:
    return _load_module()


def _receipt(module: ModuleType, value: dict[str, Any]) -> dict[str, Any]:
    value = dict(value)
    value.pop("receipt_sha256", None)
    value["receipt_sha256"] = hashlib.sha256(module._canonical(value)).hexdigest()
    return value


def _generated_server_scope_receipt(
    manifest_policy: dict[str, Any],
    *,
    source_revision: str,
    observed_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    collector = _load_server_scope_module()
    server_policy = json.loads(SERVER_SCOPE_POLICY.read_text())
    database = {
        "name": manifest_policy["source_resource"]["database_name"],
        "is_template": False,
        "allow_connections": True,
        "owner": "owner-role",
        "encoding": "UTF8",
        "collation": "C",
        "ctype": "C",
        "locale_provider": "c",
        "icu_locale": None,
        "icu_rules": None,
        "collation_version": None,
        "default_tablespace": "pg_default",
        "connection_limit": -1,
    }
    disabled_template = {
        **database,
        "name": "template0",
        "is_template": True,
        "allow_connections": False,
    }
    server_surfaces = {name: [] for name in collector.SERVER_SURFACES}
    server_surfaces["databases"] = [database, disabled_template]
    server_surfaces["settings"] = [
        [name, "configured", None, "default", "configured", "configured", False]
        for name in server_policy["required_settings"]
    ]
    database_surfaces = {name: [] for name in collector.DATABASE_SURFACES}
    snapshot = collector.ServerSnapshot(
        version_major=16,
        visibility={
            "pg_authid": True,
            "pg_monitor": True,
            "pg_read_all_settings": True,
        },
        surfaces=server_surfaces,
    )

    class Adapter:
        def start_capture(self, maximum_seconds: int) -> None:
            assert maximum_seconds == collector.MAXIMUM_CAPTURE_SECONDS

        def server_snapshot(self, required_settings: list[str]) -> Any:
            assert required_settings == server_policy["required_settings"]
            return snapshot

        def database_snapshot(self, database_name: str) -> dict[str, list[Any]]:
            assert database_name == database["name"]
            return database_surfaces

    server_policy["activation_ready"] = True
    server_policy["source"].update(
        resource_id=manifest_policy["source_resource"]["postgres_server_id"],
        revision=source_revision,
        host="test.postgres.database.azure.com",
        primary_database=database["name"],
    )
    server_policy["hmac"]["key_version"] = "staging-server-scope-hmac-v1"
    server_policy["expected_server_surfaces"] = {
        name: collector._pin_json(
            collector._surface_pin(
                SERVER_SCOPE_KEY,
                f"server:{name}",
                server_surfaces[name],
            )
        )
        for name in collector.SERVER_SURFACES
    }
    identity = collector._identity_hmac(
        SERVER_SCOPE_KEY,
        "database",
        database["name"],
    )
    disabled_identity = collector._identity_hmac(
        SERVER_SCOPE_KEY,
        "database",
        disabled_template["name"],
    )
    server_policy["expected_databases"] = [
        {
            "identity_hmac": identity,
            "is_template": False,
            "allow_connections": True,
            "status": "primary_inventory",
            "surfaces": {
                name: collector._pin_json(
                    collector._surface_pin(
                        SERVER_SCOPE_KEY,
                        f"database:{identity}:{name}",
                        database_surfaces[name],
                    )
                )
                for name in collector.DATABASE_SURFACES
            },
        },
        {
            "identity_hmac": disabled_identity,
            "is_template": True,
            "allow_connections": False,
            "status": "connections_disabled",
            "surfaces": None,
        },
    ]
    completed = datetime.strptime(observed_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    times = iter([completed - timedelta(minutes=1), completed])
    receipt = collector.collect_evidence(
        server_policy,
        manifest_policy["policy_digests"]["server_scope"].removeprefix("sha256:"),
        Adapter(),
        SERVER_SCOPE_KEY,
        source_resource_id=manifest_policy["source_resource"]["postgres_server_id"],
        source_revision=source_revision,
        key_version=server_policy["hmac"]["key_version"],
        clock=lambda: next(times),
        monotonic=lambda: 0.0,
    )
    return receipt, server_policy


def _bundle(
    module: ModuleType,
    tmp_path: Path,
    *,
    artifact_mutator: Callable[[dict[str, dict[str, Any]]], None] | None = None,
    manifest_mutator: Callable[[dict[str, Any]], None] | None = None,
    approved_restore_time: str | None = None,
    observed_at: str = "2026-09-03T12:00:00Z",
    expires_at: str = "2026-09-03T12:15:00Z",
) -> Path:
    loaded_policy, policy_sha256 = module.load_policy(POLICY)
    policy = dict(loaded_policy)
    synthetic = module._load_synthetic_policy(policy)
    inventory_keys = module._inventory_key_sets(synthetic)
    workflow_sha = "3" * 40
    provenance = {
        "repository": policy["repository"],
        "protected_ref": policy["protected_ref"],
        "workflow_path": policy["workflow_path"],
        "workflow_sha": workflow_sha,
        "run_id": "901",
        "run_attempt": "1",
        "event_name": policy["event_name"],
    }
    claims = {
        "issuer": policy["signer"]["issuer"],
        "subject": policy["signer"]["subject"],
        "repository": policy["repository"],
        "ref": policy["protected_ref"],
        "sha": workflow_sha,
        "workflow_ref": (
            f"{policy['repository']}/{policy['workflow_path']}@"
            f"{policy['protected_ref']}"
        ),
        "workflow_sha": workflow_sha,
        "run_id": "901",
        "run_attempt": "1",
        "actor": policy["founder_actor"],
        "triggering_actor": policy["founder_actor"],
    }
    common = {
        "source_sha": "1" * 40,
        "candidate_sha": "2" * 40,
        "source_resource": policy["source_resource"],
        "observed_at": observed_at,
        "expires_at": expires_at,
        "provenance": provenance,
        "signer_claims": claims,
    }
    policy_digests = {
        "evidence_manifest": f"sha256:{policy_sha256}",
        **policy["policy_digests"],
    }
    artifacts = {
        "candidate": {
            "schema_version": 1,
            "evidence_type": "candidate",
            "state": "verified",
            "approval_pr_number": 230,
            "protected_master_sha": workflow_sha,
            "schema_revision": synthetic["profiles"]["pre"]["revision"],
            **common,
            "policy_digests": policy_digests,
        },
        "inventory": {
            "schema_version": 2,
            "policy_id": "cosmaneura-staging-synthetic-inventory-v1",
            "policy_sha256": policy["policy_digests"]["synthetic_data"].removeprefix(
                "sha256:"
            ),
            "approved_state_sha256": "4" * 64,
            "hmac_key_version": "staging-inventory-hmac-v1",
            **common,
            "phase": "pre",
            "counts": {name: 0 for name in inventory_keys["counts"]},
            "rule_violation_counts": {
                name: 0 for name in inventory_keys["rule_violation_counts"]
            },
            "identity_hmacs": {
                name: "8" * 64 for name in inventory_keys["identity_hmacs"]
            },
            "catalog_hmac": "5" * 64,
            "schema_descriptor_sha256": synthetic["profiles"]["pre"][
                "schema_descriptor"
            ]["sha256"],
            "state_hmacs": {name: "9" * 64 for name in inventory_keys["state_hmacs"]},
            "blob_state_hmacs": {
                name: "a" * 64 for name in inventory_keys["blob_state_hmacs"]
            },
            "redis_state_hmac": "7" * 64,
            "errors": [],
            "outcome": "pass",
        },
        "quiescence": {
            "schema_version": 1,
            "evidence_type": "quiescence",
            "state": "quiesced",
            **common,
            "policy_digests": policy_digests,
        },
        "server_scope": _generated_server_scope_receipt(
            policy,
            source_revision=common["source_sha"],
            observed_at=observed_at,
        )[0],
    }
    inventory_counts = artifacts["inventory"]["counts"]
    inventory_counts["database.non_system_schemas"] = 1
    inventory_counts["database.alembic_version"] = 1
    for name, value in synthetic["profiles"]["pre"]["schema_descriptor"][
        "counts"
    ].items():
        inventory_counts[f"database.schema.{name}"] = value
    for name in inventory_keys["state_hmacs"]:
        if synthetic["database"]["tables"][name]["disposition"] != "required_empty":
            inventory_counts[f"database.{name}"] = 1
    for name, disposition in synthetic["blob"]["containers"].items():
        if disposition != "required_empty":
            inventory_counts[f"blob.{name}"] = 1
    if artifact_mutator:
        artifact_mutator(artifacts)
    artifacts = {
        name: value if name == "server_scope" else _receipt(module, value)
        for name, value in artifacts.items()
    }

    encoded = {
        name: module._canonical(value) + b"\n" for name, value in artifacts.items()
    }
    artifact_entries = [
        {
            "kind": name,
            "path": policy["artifact_contracts"][name],
            "file_sha256": module._digest(encoded[name]),
            "receipt_sha256": (
                module._digest(module._canonical(value))
                if name == "server_scope"
                else f"sha256:{value['receipt_sha256']}"
            ),
        }
        for name, value in sorted(artifacts.items())
    ]
    manifest = {
        "schema_version": 2,
        "manifest_id": "rehearsal-20260903",
        "source_sha": common["source_sha"],
        "candidate_sha": common["candidate_sha"],
        "hmac_key_version": "staging-inventory-hmac-v1",
        "source_resource": common["source_resource"],
        "approved_restore_time": (
            observed_at if approved_restore_time is None else approved_restore_time
        ),
        "observed_at": observed_at,
        "expires_at": expires_at,
        "provenance": provenance,
        "signer_claims": claims,
        "policy_digests": policy_digests,
        "artifacts": artifact_entries,
    }
    if manifest_mutator:
        manifest_mutator(manifest)
    manifest["evidence_set_sha256"] = module._digest(module._canonical(manifest))
    manifest_raw = module._canonical(manifest) + b"\n"

    bundle = tmp_path / "bundle"
    bundle.mkdir(mode=0o700, parents=True)
    files = {
        "evidence-manifest.json": manifest_raw,
        **{policy["artifact_contracts"][name]: raw for name, raw in encoded.items()},
    }
    for name, raw in files.items():
        path = bundle / name
        path.write_bytes(raw)
        path.chmod(0o600)
    checksums = "".join(
        f"{hashlib.sha256(raw).hexdigest()}  {name}\n"
        for name, raw in sorted(files.items())
    )
    checksum_path = bundle / "SHA256SUMS"
    checksum_path.write_text(checksums)
    checksum_path.chmod(0o600)
    return bundle


def _validate(module: ModuleType, bundle: Path) -> dict[str, Any]:
    policy, digest = module.load_policy(POLICY)
    return module.validate_bundle(
        policy=policy,
        policy_sha256=digest,
        bundle=bundle,
        now=NOW,
    )


def _change_server_scope_entry_digest(
    manifest: dict[str, Any], field: str = "receipt_sha256"
) -> None:
    entry = next(
        item for item in manifest["artifacts"] if item["kind"] == "server_scope"
    )
    entry[field] = "sha256:" + "f" * 64


def _add_out_of_order_database(receipt: dict[str, Any]) -> None:
    identity = "0" * 64
    assert identity < receipt["databases"][0]["identity_hmac"]
    receipt["databases"].append(
        {"identity_hmac": identity, "status": "connections_disabled"}
    )


def _add_too_many_databases(receipt: dict[str, Any]) -> None:
    receipt["databases"].extend(
        {
            "identity_hmac": f"{index:064x}",
            "status": "connections_disabled",
        }
        for index in range(64)
    )


def _exceed_total_record_cap(receipt: dict[str, Any]) -> None:
    for name in list(receipt["server_surfaces"])[:6]:
        receipt["server_surfaces"][name]["count"] = 100_000


def _primary_database(receipt: dict[str, Any]) -> dict[str, Any]:
    return next(
        database
        for database in receipt["databases"]
        if database["status"] == "primary_inventory"
    )


def _add_nonempty_empty_database(receipt: dict[str, Any]) -> None:
    identity = "0" * 64
    surfaces = deepcopy(_primary_database(receipt)["surfaces"])
    surfaces["user_relations"]["count"] = 1
    receipt["databases"].append(
        {
            "identity_hmac": identity,
            "status": "empty",
            "surfaces": surfaces,
        }
    )
    receipt["databases"].sort(key=lambda item: item["identity_hmac"])


def test_exact_bundle_validates_but_remains_blocked(
    module: ModuleType,
    tmp_path: Path,
) -> None:
    bundle = _bundle(module, tmp_path)
    result = _validate(module, bundle)
    policy, policy_sha256 = module.load_policy(POLICY)
    manifest_raw = (bundle / "evidence-manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    candidate = json.loads((bundle / "candidate.json").read_bytes())
    receipts = {
        entry["kind"]: entry["receipt_sha256"] for entry in manifest["artifacts"]
    }

    assert result["outcome"] == "contract_valid"
    assert result["activation_state"] == "blocked"
    assert result["activation_blockers"] == [
        "server_scope_policy_unconfigured",
        "attestation_verification_unimplemented",
        "provider_access_unimplemented",
        "workflow_integration_unimplemented",
        "restore_activation_unimplemented",
    ]
    assert result["evidence_set_sha256"].startswith("sha256:")
    assert result["evidence_binding"] == {
        "schema_version": 1,
        "binding_policy_id": "cosmaneura-staging-restore-evidence-binding-v1",
        "evidence_manifest_policy_id": "cosmaneura-staging-evidence-manifest-v2",
        "evidence_manifest_policy_sha256": f"sha256:{policy_sha256}",
        "manifest_id": "rehearsal-20260903",
        "manifest_file_sha256": module._digest(manifest_raw),
        "bundle_index_sha256": module._digest((bundle / "SHA256SUMS").read_bytes()),
        "evidence_set_sha256": manifest["evidence_set_sha256"],
        "source_sha": "1" * 40,
        "candidate_sha": "2" * 40,
        "source_server_resource_id": policy["source_resource"]["postgres_server_id"],
        "source_database_name": policy["source_resource"]["database_name"],
        "approved_restore_time": "2026-09-03T12:00:00Z",
        "observed_at": "2026-09-03T12:00:00Z",
        "expires_at": "2026-09-03T12:15:00Z",
        "candidate_approval_pr_number": 230,
        "candidate_protected_master_sha": "3" * 40,
        "candidate_schema_revision": candidate["schema_revision"],
        "candidate_receipt_sha256": receipts["candidate"],
        "inventory_receipt_sha256": receipts["inventory"],
        "quiescence_receipt_sha256": receipts["quiescence"],
        "server_scope_receipt_sha256": receipts["server_scope"],
        "repository": policy["repository"],
        "protected_ref": policy["protected_ref"],
        "workflow_path": policy["workflow_path"],
        "workflow_sha": "3" * 40,
        "run_id": "901",
        "run_attempt": "1",
        "event_name": policy["event_name"],
        "signer_issuer": policy["signer"]["issuer"],
        "signer_subject": policy["signer"]["subject"],
    }


def test_validated_snapshot_returns_the_same_read_manifest_and_server_scope_bytes(
    module: ModuleType,
    tmp_path: Path,
) -> None:
    bundle = _bundle(module, tmp_path)
    policy, digest = module.load_policy(POLICY)

    snapshot = module.validate_bundle_snapshot(
        policy=policy,
        policy_sha256=digest,
        bundle=bundle,
        now=NOW,
    )

    assert snapshot.result == _validate(module, bundle)
    original_manifest_bytes = (bundle / "evidence-manifest.json").read_bytes()
    original_bytes = (bundle / "server-scope.json").read_bytes()
    assert snapshot.manifest_bytes == original_manifest_bytes
    assert snapshot.manifest == json.loads(snapshot.manifest_bytes)
    assert snapshot.server_scope_bytes == original_bytes
    assert snapshot.server_scope_receipt == json.loads(snapshot.server_scope_bytes)
    assert snapshot.result["evidence_binding"]["server_scope_receipt_sha256"] == (
        module._digest(module._canonical(snapshot.server_scope_receipt))
    )

    (bundle / "evidence-manifest.json").write_bytes(b"changed after validation\n")
    (bundle / "server-scope.json").write_bytes(b"changed after validation\n")
    assert snapshot.manifest_bytes == original_manifest_bytes
    assert snapshot.manifest == json.loads(original_manifest_bytes)
    assert snapshot.server_scope_bytes == original_bytes
    assert snapshot.server_scope_receipt == json.loads(original_bytes)


def test_validated_snapshot_binds_every_same_read_file_to_launch_digests(
    module: ModuleType,
    tmp_path: Path,
) -> None:
    bundle = _bundle(module, tmp_path)
    policy, digest = module.load_policy(POLICY)
    expected = {
        path.name: module._digest(path.read_bytes()) for path in bundle.iterdir()
    }

    snapshot = module.validate_bundle_snapshot(
        policy=policy,
        policy_sha256=digest,
        bundle=bundle,
        now=NOW,
        expected_file_sha256s=expected,
    )
    assert snapshot._has_validated_manifest_snapshot()

    mismatched = dict(expected)
    mismatched["candidate.json"] = f"sha256:{'0' * 64}"
    with pytest.raises(module.EvidenceFailure, match="bundle.launch_binding"):
        module.validate_bundle_snapshot(
            policy=policy,
            policy_sha256=digest,
            bundle=bundle,
            now=NOW,
            expected_file_sha256s=mismatched,
        )

    missing = dict(expected)
    missing.pop("SHA256SUMS")
    with pytest.raises(module.EvidenceFailure, match="bundle.launch_binding"):
        module.validate_bundle_snapshot(
            policy=policy,
            policy_sha256=digest,
            bundle=bundle,
            now=NOW,
            expected_file_sha256s=missing,
        )


def test_validated_manifest_projection_is_caller_owned(
    module: ModuleType,
    tmp_path: Path,
) -> None:
    bundle = _bundle(module, tmp_path)
    policy, digest = module.load_policy(POLICY)
    snapshot = module.validate_bundle_snapshot(
        policy=policy,
        policy_sha256=digest,
        bundle=bundle,
        now=NOW,
    )
    original_bytes = snapshot.manifest_bytes
    original_manifest = snapshot.manifest

    changed = snapshot.manifest
    changed["manifest_id"] = "rehearsal-mutated"
    changed["artifacts"].clear()

    assert snapshot.manifest_bytes == original_bytes
    assert snapshot.manifest == original_manifest


def test_validated_snapshot_rejects_direct_construction_and_subclasses(
    module: ModuleType,
) -> None:
    with pytest.raises(module.EvidenceFailure, match="validated_bundle.invalid"):
        module.ValidatedEvidenceBundle()

    class SnapshotSubclass(module.ValidatedEvidenceBundle):
        pass

    with pytest.raises(module.EvidenceFailure, match="validated_bundle.invalid"):
        SnapshotSubclass._from_validated_manifest_bytes(
            result_bytes=b"{}",
            server_scope_bytes=b"{}",
            manifest_bytes=b"{}",
        )

    forged = object.__new__(SnapshotSubclass)
    object.__setattr__(forged, "result_bytes", b"{}")
    object.__setattr__(forged, "server_scope_bytes", b"{}")
    object.__setattr__(forged, "manifest_bytes", b"{}")
    object.__setattr__(forged, "_validation_seal", module._VALIDATED_BUNDLE_SEAL)
    object.__setattr__(
        forged, "_manifest_validation_seal", module._VALIDATED_BUNDLE_SEAL
    )
    assert forged._is_validated_snapshot() is False
    with pytest.raises(module.EvidenceFailure, match="validated_bundle.invalid"):
        _ = forged.manifest


def test_json_loaders_reject_oversized_integer_with_bounded_errors(
    module: ModuleType,
) -> None:
    raw = b'{"schema_version":' + b"9" * 5_000 + b"}"

    with pytest.raises(module.EvidenceFailure, match="manifest.invalid"):
        module._load_json(raw, "manifest.invalid")
    with pytest.raises(module.EvidenceFailure, match="server_scope.invalid"):
        module._load_server_scope_json(raw)


def test_public_snapshot_properties_reject_malformed_bytes_with_bounded_errors(
    module: ModuleType,
) -> None:
    oversized_integer = b'{"schema_version":' + b"9" * 5_000 + b"}"
    malformed = module.ValidatedEvidenceBundle._from_validated_manifest_bytes(
        result_bytes=oversized_integer,
        server_scope_bytes=b"[]",
        manifest_bytes=b"[]",
    )

    with pytest.raises(module.EvidenceFailure, match="validated_bundle.invalid"):
        _ = malformed.result
    with pytest.raises(module.EvidenceFailure, match="validated_bundle.invalid"):
        _ = malformed.server_scope_receipt
    with pytest.raises(module.EvidenceFailure, match="validated_bundle.invalid"):
        _ = malformed.manifest


def test_private_snapshot_helper_remains_compatible_but_omits_attestation_data(
    module: ModuleType,
) -> None:
    snapshot = module.ValidatedEvidenceBundle._from_validated_bytes(
        result_bytes=b"{}",
        server_scope_bytes=b"{}",
    )

    assert snapshot._is_validated_snapshot() is True
    assert snapshot._has_validated_manifest_snapshot() is False
    assert snapshot.manifest_bytes == b""
    with pytest.raises(module.EvidenceFailure, match="validated_bundle.invalid"):
        _ = snapshot.manifest


def test_server_scope_boolean_walk_recursion_is_bounded(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_recursion(_value: Any) -> bool:
        raise RecursionError

    monkeypatch.setattr(module, "_contains_boolean", raise_recursion)

    with pytest.raises(module.EvidenceFailure, match="server_scope.invalid"):
        module._load_server_scope_json(b'{"pass":true,"nested":{}}')


def test_validated_snapshot_is_immutable_and_rejects_an_unbound_active_policy(
    module: ModuleType,
    tmp_path: Path,
) -> None:
    bundle = _bundle(module, tmp_path)
    policy, digest = module.load_policy(POLICY)
    snapshot = module.validate_bundle_snapshot(
        policy=policy,
        policy_sha256=digest,
        bundle=bundle,
        now=NOW,
    )
    _expected_receipt, server_policy = _generated_server_scope_receipt(
        dict(policy),
        source_revision="1" * 40,
        observed_at="2026-09-03T12:00:00Z",
    )
    assert snapshot.server_scope_receipt == _expected_receipt
    authentication = _load_server_scope_authentication_module()
    authentication_policy = json.loads(SERVER_SCOPE_AUTHENTICATION_POLICY.read_text())
    authentication_policy["activation_ready"] = True
    server_policy_bytes = (
        json.dumps(server_policy, separators=(",", ":"), ensure_ascii=True) + "\n"
    ).encode("ascii")
    server_policy_sha256 = hashlib.sha256(server_policy_bytes).hexdigest()
    authentication_policy["server_scope_policy"]["sha256"] = (
        f"sha256:{server_policy_sha256}"
    )
    authentication_policy_bytes = (
        json.dumps(
            authentication_policy,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        + "\n"
    ).encode("ascii")
    authentication_policy_sha256 = hashlib.sha256(
        authentication_policy_bytes
    ).hexdigest()
    authenticated_snapshot = (
        authentication.evidence_manifest.ValidatedEvidenceBundle._from_validated_bytes(
            result_bytes=snapshot.result_bytes,
            server_scope_bytes=snapshot.server_scope_bytes,
        )
    )

    (bundle / "server-scope.json").write_bytes(b"changed after validation\n")
    with pytest.raises(authentication.AuthenticationFailure, match="policy_mismatch"):
        authentication._authenticate_validated_bundle(
            authentication_policy_bytes=authentication_policy_bytes,
            authentication_policy_sha256=authentication_policy_sha256,
            server_scope_policy_bytes=server_policy_bytes,
            server_scope_policy_sha256=server_policy_sha256,
            snapshot=authenticated_snapshot,
            key=SERVER_SCOPE_KEY,
            clock=lambda: NOW,
        )
    assert snapshot.server_scope_bytes == module._canonical(_expected_receipt) + b"\n"


def test_binding_manifest_tamper_is_bound_by_the_evidence_set(
    module: ModuleType,
    tmp_path: Path,
) -> None:
    bundle = _bundle(module, tmp_path)
    manifest_path = bundle / "evidence-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["manifest_id"] = "rehearsal-20260904"
    manifest_path.write_bytes(module._canonical(manifest) + b"\n")
    indexed_files = {
        path.name: path.read_bytes()
        for path in bundle.iterdir()
        if path.name != "SHA256SUMS"
    }
    (bundle / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256(raw).hexdigest()}  {name}\n"
            for name, raw in sorted(indexed_files.items())
        )
    )

    with pytest.raises(module.EvidenceFailure, match="manifest.evidence_set_digest"):
        _validate(module, bundle)


@pytest.mark.parametrize(
    ("approved_restore_time", "failure"),
    [
        ("2026-09-03T12:00:01Z", "evidence.approved_restore_time_mismatch"),
        ("2026-09-03T12:00:00+00:00", "evidence.approved_restore_time"),
    ],
)
def test_approved_restore_time_must_be_canonical_and_equal_observation(
    module: ModuleType,
    tmp_path: Path,
    approved_restore_time: str,
    failure: str,
) -> None:
    bundle = _bundle(
        module,
        tmp_path,
        approved_restore_time=approved_restore_time,
    )

    with pytest.raises(module.EvidenceFailure, match=failure):
        _validate(module, bundle)


def test_committed_dormant_server_scope_policy_is_valid_and_remains_blocked(
    module: ModuleType,
) -> None:
    loaded_policy, _ = module.load_policy(POLICY)

    server_policy = module._load_server_scope_policy(loaded_policy)

    assert server_policy["activation_ready"] is False
    assert server_policy["source"]["resource_id"] == (
        "CONFIGURE_EXACT_STAGING_POSTGRES_RESOURCE_ID"
    )
    assert "server_scope_policy_unconfigured" in loaded_policy["activation_blockers"]


def test_collector_receipt_matches_an_activated_exact_policy(
    module: ModuleType,
) -> None:
    loaded_policy, _ = module.load_policy(POLICY)
    policy = dict(loaded_policy)
    receipt, server_policy = _generated_server_scope_receipt(
        policy,
        source_revision="1" * 40,
        observed_at="2026-09-03T12:00:00Z",
    )

    digest = module._validate_server_scope(
        receipt,
        manifest={"source_sha": "1" * 40, "observed_at": "2026-09-03T12:00:00Z"},
        policy=policy,
        server_policy=server_policy,
    )

    assert digest == module._digest(module._canonical(receipt))

    changed = deepcopy(receipt)
    changed["server_surfaces"]["roles"]["hmac"] = "f" * 64
    with pytest.raises(module.EvidenceFailure, match="server_scope.policy_binding"):
        module._validate_server_scope(
            changed,
            manifest={
                "source_sha": "1" * 40,
                "observed_at": "2026-09-03T12:00:00Z",
            },
            policy=policy,
            server_policy=server_policy,
        )

    changed = deepcopy(receipt)
    changed["databases"][0]["status"] = []
    with pytest.raises(module.EvidenceFailure, match="server_scope.database"):
        module._validate_server_scope(
            changed,
            manifest={
                "source_sha": "1" * 40,
                "observed_at": "2026-09-03T12:00:00Z",
            },
            policy=policy,
            server_policy=server_policy,
        )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value["source"].update(revision="f" * 40),
        lambda value: value["hmac"].update(key_version="rotated-key-v2"),
        lambda value: value["expected_server_surfaces"]["roles"].update(count=1),
    ],
)
def test_collector_receipt_rejects_activated_policy_drift(
    module: ModuleType,
    mutator: Callable[[dict[str, Any]], None],
) -> None:
    loaded_policy, _ = module.load_policy(POLICY)
    policy = dict(loaded_policy)
    receipt, server_policy = _generated_server_scope_receipt(
        policy,
        source_revision="1" * 40,
        observed_at="2026-09-03T12:00:00Z",
    )
    mutator(server_policy)

    with pytest.raises(module.EvidenceFailure, match="server_scope.policy_binding"):
        module._validate_server_scope(
            receipt,
            manifest={
                "source_sha": "1" * 40,
                "observed_at": "2026-09-03T12:00:00Z",
            },
            policy=policy,
            server_policy=server_policy,
        )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value["source"].update(
            resource_id="CONFIGURE_EXACT_STAGING_POSTGRES_RESOURCE_ID"
        ),
        lambda value: value["source"].update(
            revision="CONFIGURE_EXACT_SOURCE_REVISION"
        ),
        lambda value: value["hmac"].update(
            key_version="CONFIGURE_OWNER_CONTROLLED_KEY_VERSION"
        ),
    ],
)
def test_activated_server_scope_policy_rejects_placeholders(
    module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutator: Callable[[dict[str, Any]], None],
) -> None:
    loaded_policy, _ = module.load_policy(POLICY)
    manifest_policy = deepcopy(loaded_policy)
    _, server_policy = _generated_server_scope_receipt(
        dict(loaded_policy),
        source_revision="1" * 40,
        observed_at="2026-09-03T12:00:00Z",
    )
    mutator(server_policy)
    raw = module._canonical(server_policy) + b"\n"
    policy_path = tmp_path / "server-scope-policy.json"
    policy_path.write_bytes(raw)
    policy_path.chmod(0o600)
    manifest_policy["policy_digests"] = dict(manifest_policy["policy_digests"])
    manifest_policy["policy_digests"]["server_scope"] = module._digest(raw)
    monkeypatch.setattr(module, "SERVER_SCOPE_POLICY_PATH", policy_path)

    with pytest.raises(module.EvidenceFailure, match="server_scope_policy.invalid"):
        module._load_server_scope_policy(manifest_policy)


@pytest.mark.parametrize("mutation", ["status", "required_settings"])
def test_server_scope_policy_rejects_unhashable_values_with_bounded_error(
    module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    loaded_policy, _ = module.load_policy(POLICY)
    manifest_policy = deepcopy(loaded_policy)
    server_policy = json.loads(SERVER_SCOPE_POLICY.read_text())
    if mutation == "status":
        server_policy["expected_databases"][0]["status"] = []
    else:
        server_policy["required_settings"] = [[]]
    raw = module._canonical(server_policy) + b"\n"
    policy_path = tmp_path / "server-scope-policy.json"
    policy_path.write_bytes(raw)
    policy_path.chmod(0o600)
    manifest_policy["policy_digests"] = dict(manifest_policy["policy_digests"])
    manifest_policy["policy_digests"]["server_scope"] = module._digest(raw)
    monkeypatch.setattr(module, "SERVER_SCOPE_POLICY_PATH", policy_path)

    with pytest.raises(module.EvidenceFailure, match="server_scope_policy.invalid"):
        module._load_server_scope_policy(manifest_policy)


def test_dormant_empty_database_policy_requires_zero_protected_surfaces(
    module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded_policy, _ = module.load_policy(POLICY)
    manifest_policy = deepcopy(loaded_policy)
    server_policy = json.loads(SERVER_SCOPE_POLICY.read_text())
    surfaces = deepcopy(server_policy["expected_databases"][0]["surfaces"])
    surfaces["large_objects"]["count"] = 0
    server_policy["expected_databases"].append(
        {
            "identity_hmac": "CONFIGURE_SECONDARY_DATABASE_IDENTITY_HMAC_SHA256",
            "is_template": False,
            "allow_connections": True,
            "status": "empty",
            "surfaces": surfaces,
        }
    )
    raw = module._canonical(server_policy) + b"\n"
    policy_path = tmp_path / "server-scope-policy.json"
    policy_path.write_bytes(raw)
    policy_path.chmod(0o600)
    manifest_policy["policy_digests"] = dict(manifest_policy["policy_digests"])
    manifest_policy["policy_digests"]["server_scope"] = module._digest(raw)
    monkeypatch.setattr(module, "SERVER_SCOPE_POLICY_PATH", policy_path)

    with pytest.raises(module.EvidenceFailure, match="server_scope_policy.invalid"):
        module._load_server_scope_policy(manifest_policy)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda manifest: manifest.update(unexpected="value"),
        lambda manifest: manifest.update(caller_approved=True),
        lambda manifest: manifest.update(hmac_key_version="unsupported"),
        lambda manifest: manifest.update(source_sha="f" * 40),
        lambda manifest: manifest["provenance"].update(run_attempt="0"),
        lambda manifest: manifest["signer_claims"].update(actor="not-founder"),
        lambda manifest: manifest["policy_digests"].update(
            synthetic_data="sha256:" + "f" * 64
        ),
        lambda manifest: manifest["policy_digests"].update(
            server_scope="sha256:" + "f" * 64
        ),
        lambda manifest: manifest["artifacts"][0].update(path="../inventory.json"),
    ],
)
def test_manifest_claims_fail_closed(
    module: ModuleType,
    tmp_path: Path,
    mutator: Callable[[dict[str, Any]], None],
) -> None:
    bundle = _bundle(module, tmp_path, manifest_mutator=mutator)

    with pytest.raises(module.EvidenceFailure):
        _validate(module, bundle)


@pytest.mark.parametrize(
    ("field", "failure"),
    [
        ("file_sha256", "artifact.file_digest.server_scope"),
        ("receipt_sha256", "artifact.receipt_digest.server_scope"),
    ],
)
def test_server_scope_file_and_receipt_digests_are_independent(
    module: ModuleType,
    tmp_path: Path,
    field: str,
    failure: str,
) -> None:
    bundle = _bundle(
        module,
        tmp_path,
        manifest_mutator=lambda manifest: _change_server_scope_entry_digest(
            manifest, field
        ),
    )

    with pytest.raises(module.EvidenceFailure, match=failure):
        _validate(module, bundle)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda values: values["inventory"].update(unexpected="value"),
        lambda values: values["inventory"].update(caller_approved=True),
        lambda values: values["inventory"].update(candidate_sha="f" * 40),
        lambda values: values["inventory"]["counts"].update(
            {"unexpected.surface": 123}
        ),
        lambda values: values["inventory"]["provenance"].update(run_id="902"),
        lambda values: values["candidate"].update(protected_master_sha="f" * 40),
        lambda values: values["candidate"].update(approval_pr_number=True),
        lambda values: values["candidate"].update(schema_revision="aaaaaaaaaaaa"),
        lambda values: values["quiescence"].update(state="not_quiesced"),
        lambda values: values["server_scope"].update(unexpected="value"),
    ],
)
def test_artifact_schema_and_bindings_fail_closed(
    module: ModuleType,
    tmp_path: Path,
    mutator: Callable[[dict[str, dict[str, Any]]], None],
) -> None:
    bundle = _bundle(module, tmp_path, artifact_mutator=mutator)

    with pytest.raises(module.EvidenceFailure):
        _validate(module, bundle)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.update({"pass": False}),
        lambda value: value.update(schema_version=True),
        lambda value: value.update(schema_version=1.0),
        lambda value: value.update(errors=["catalog.read_blocked"]),
        lambda value: value.update(policy_id="unexpected-policy"),
        lambda value: value.update(policy_sha256="f" * 64),
        lambda value: value.update(source_revision="f" * 40),
        lambda value: value.update(key_version="invalid key version"),
        lambda value: value.update(capture_started_at="2026-09-03T12:00:01Z"),
        lambda value: value.update(capture_completed_at="2026-09-03T12:00:01Z"),
        lambda value: value.update(server_version_major=15),
        lambda value: value.update(server_version_major=True),
        lambda value: value.update(server_version_major=16.0),
        lambda value: value.update(evidence_hmac="not-a-digest"),
        lambda value: value["server_surfaces"].pop("roles"),
        lambda value: value["server_surfaces"].update(
            unexpected={"count": 0, "hmac": "f" * 64}
        ),
        lambda value: value["server_surfaces"]["roles"].update(count=True),
        lambda value: value["server_surfaces"]["roles"].update(count=-1),
        lambda value: value["server_surfaces"]["roles"].update(hmac="f" * 63),
        lambda value: value["server_surfaces"]["databases"].update(count=1),
        lambda value: value["server_surfaces"]["settings"].update(count=0),
        lambda value: value.update(databases=[]),
        lambda value: value["databases"].append(deepcopy(value["databases"][0])),
        lambda value: value["databases"][0].update(status="empty"),
        lambda value: _primary_database(value)["surfaces"].pop("schema"),
        lambda value: _primary_database(value)["surfaces"]["schema"].update(
            count=100_001
        ),
        lambda value: value.update(primary_database_identity_hmac="f" * 64),
        _add_out_of_order_database,
        _add_too_many_databases,
        _exceed_total_record_cap,
        _add_nonempty_empty_database,
    ],
)
def test_server_scope_receipt_fails_closed(
    module: ModuleType,
    tmp_path: Path,
    mutator: Callable[[dict[str, Any]], None],
) -> None:
    bundle = _bundle(
        module,
        tmp_path,
        artifact_mutator=lambda artifacts: mutator(artifacts["server_scope"]),
    )

    with pytest.raises(module.EvidenceFailure, match="server_scope"):
        _validate(module, bundle)


def test_stale_or_overlong_evidence_fails_closed(
    module: ModuleType,
    tmp_path: Path,
) -> None:
    bundle = _bundle(
        module,
        tmp_path,
        observed_at="2026-09-03T11:00:00Z",
        expires_at="2026-09-03T11:15:00Z",
    )
    with pytest.raises(module.EvidenceFailure, match="evidence.stale"):
        _validate(module, bundle)


def test_bundle_rejects_extra_missing_symlink_and_checksum_drift(
    module: ModuleType,
    tmp_path: Path,
) -> None:
    extra = _bundle(module, tmp_path / "extra")
    (extra / "unexpected.json").write_text("{}")
    with pytest.raises(module.EvidenceFailure, match="bundle.file_set"):
        _validate(module, extra)

    missing = _bundle(module, tmp_path / "missing")
    (missing / "inventory.json").unlink()
    with pytest.raises(module.EvidenceFailure, match="bundle.file_set"):
        _validate(module, missing)

    symlink = _bundle(module, tmp_path / "symlink")
    target = symlink.parent / "inventory-target.json"
    target.write_bytes((symlink / "inventory.json").read_bytes())
    (symlink / "inventory.json").unlink()
    (symlink / "inventory.json").symlink_to(target)
    with pytest.raises(module.EvidenceFailure, match="bundle.file_unsafe.inventory"):
        _validate(module, symlink)

    checksum = _bundle(module, tmp_path / "checksum")
    (checksum / "SHA256SUMS").write_text("0" * 64 + "  inventory.json\n")
    with pytest.raises(module.EvidenceFailure, match="bundle.checksums"):
        _validate(module, checksum)


def test_canonical_receipt_and_file_hashes_are_both_required(
    module: ModuleType,
    tmp_path: Path,
) -> None:
    bundle = _bundle(module, tmp_path)
    inventory_path = bundle / "inventory.json"
    inventory = json.loads(inventory_path.read_bytes())
    inventory["receipt_sha256"] = "f" * 64
    inventory_path.write_bytes(module._canonical(inventory) + b"\n")
    checksums = {
        path.name: path.read_bytes()
        for path in bundle.iterdir()
        if path.name != "SHA256SUMS"
    }
    (bundle / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256(raw).hexdigest()}  {name}\n"
            for name, raw in sorted(checksums.items())
        )
    )

    with pytest.raises(module.EvidenceFailure):
        _validate(module, bundle)


def test_policy_path_and_digest_are_pinned(module: ModuleType, tmp_path: Path) -> None:
    copied = tmp_path / "policy.json"
    copied.write_bytes(POLICY.read_bytes())

    with pytest.raises(module.EvidenceFailure, match="policy.untrusted_path"):
        module.load_policy(copied)
