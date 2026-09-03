from __future__ import annotations

import hashlib
import importlib.util
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/validate_staging_evidence_manifest.py"
POLICY = ROOT / "contracts/staging-rehearsal/v1/evidence-manifest-policy.json"
NOW = datetime(2026, 9, 3, 12, 5, tzinfo=UTC)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "standalone_staging_evidence_manifest",
        SCRIPT,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module() -> ModuleType:
    return _load_module()


def _receipt(module: ModuleType, value: dict[str, Any]) -> dict[str, Any]:
    value = dict(value)
    value.pop("receipt_sha256", None)
    value["receipt_sha256"] = hashlib.sha256(module._canonical(value)).hexdigest()
    return value


def _bundle(
    module: ModuleType,
    tmp_path: Path,
    *,
    artifact_mutator: Callable[[dict[str, dict[str, Any]]], None] | None = None,
    manifest_mutator: Callable[[dict[str, Any]], None] | None = None,
    observed_at: str = "2026-09-03T12:00:00Z",
    expires_at: str = "2026-09-03T12:15:00Z",
) -> Path:
    policy, policy_sha256 = module.load_policy(POLICY)
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
        "server_scope": {
            "schema_version": 1,
            "evidence_type": "server_scope",
            "state": "unimplemented",
            "blocker": "server_scope_collection_unimplemented",
            **common,
            "policy_digests": policy_digests,
        },
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
    artifacts = {name: _receipt(module, value) for name, value in artifacts.items()}

    encoded = {
        name: module._canonical(value) + b"\n" for name, value in artifacts.items()
    }
    artifact_entries = [
        {
            "kind": name,
            "path": policy["artifact_contracts"][name],
            "file_sha256": module._digest(encoded[name]),
            "receipt_sha256": f"sha256:{value['receipt_sha256']}",
        }
        for name, value in sorted(artifacts.items())
    ]
    manifest = {
        "schema_version": 1,
        "manifest_id": "rehearsal-20260903",
        "source_sha": common["source_sha"],
        "candidate_sha": common["candidate_sha"],
        "hmac_key_version": "staging-inventory-hmac-v1",
        "source_resource": common["source_resource"],
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


def test_exact_bundle_validates_but_remains_blocked(
    module: ModuleType,
    tmp_path: Path,
) -> None:
    result = _validate(module, _bundle(module, tmp_path))

    assert result["outcome"] == "contract_valid"
    assert result["activation_state"] == "blocked"
    assert result["activation_blockers"] == [
        "server_scope_collection_unimplemented",
        "attestation_verification_unimplemented",
        "provider_access_unimplemented",
        "workflow_integration_unimplemented",
        "restore_activation_unimplemented",
    ]
    assert result["evidence_set_sha256"].startswith("sha256:")


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
        lambda values: values["server_scope"].update(state="collected"),
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
