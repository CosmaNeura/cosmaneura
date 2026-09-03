#!/usr/bin/env python3
"""Validate a closed-world staging evidence bundle without provider access.

Success proves only local contract integrity and cross-binding.  Server-wide
scope collection and cryptographic attestation verification are deliberately
unimplemented, so a valid bundle remains ineligible to activate restore or
sanitizer execution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

POLICY_PATH = (
    Path(__file__).resolve().parents[1]
    / "contracts/staging-rehearsal/v1/evidence-manifest-policy.json"
)
SYNTHETIC_POLICY_PATH = (
    Path(__file__).resolve().parents[1]
    / "contracts/staging-rehearsal/v1/synthetic-data-policy.json"
)
POLICY_SHA256 = "55ec99542e8819756dcd476a1cdbfcd01c1ae249f34a8c30d50f051c5bb90aeb"
POLICY_SEMANTIC_SHA256 = (
    "64724c9dbb8ee1cb903e4ce9a12d1fa8752ace419ba6d8578e43bbac4d096626"
)
MAXIMUM_FILE_BYTES = 8 * 1024 * 1024
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX_RE = re.compile(r"^[0-9a-f]{64}$")
RUN_RE = re.compile(r"^[1-9][0-9]{0,19}$")
REVISION_RE = re.compile(r"^[0-9a-f]{12}$")
MANIFEST_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{11,63}$")
UTC_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


class EvidenceFailure(RuntimeError):
    """Bounded contract failure safe to report without artifact contents."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest(value: bytes) -> str:
    return f"sha256:{_sha256(value)}"


def _parse_utc(value: Any, code: str) -> datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise EvidenceFailure(code)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        raise EvidenceFailure(code) from None
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise EvidenceFailure(code)
    return parsed


def _expect_keys(value: Any, expected: Sequence[str], code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(expected):
        raise EvidenceFailure(code)
    return value


def _contains_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    if isinstance(value, Mapping):
        return any(_contains_boolean(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_boolean(item) for item in value)
    return False


def _read_regular(path: Path, code: str) -> bytes:
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid not in {0, os.geteuid()}
            or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or before.st_size <= 0
            or before.st_size > MAXIMUM_FILE_BYTES
        ):
            raise EvidenceFailure(code)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            after = os.fstat(descriptor)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise EvidenceFailure(code)
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                contents = handle.read(MAXIMUM_FILE_BYTES + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except EvidenceFailure:
        raise
    except Exception:
        raise EvidenceFailure(code) from None
    if not contents or len(contents) > MAXIMUM_FILE_BYTES:
        raise EvidenceFailure(code)
    return contents


def _load_json(raw: bytes, code: str) -> Mapping[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise EvidenceFailure(code) from None
    if not isinstance(value, Mapping) or _contains_boolean(value):
        raise EvidenceFailure(code)
    return value


def load_policy(path: Path) -> tuple[Mapping[str, Any], str]:
    try:
        if path.resolve(strict=True) != POLICY_PATH.resolve(strict=True):
            raise EvidenceFailure("policy.untrusted_path")
    except EvidenceFailure:
        raise
    except Exception:
        raise EvidenceFailure("policy.unreadable") from None
    raw = _read_regular(path, "policy.unreadable")
    raw_sha256 = _sha256(raw)
    try:
        policy = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise EvidenceFailure("policy.invalid") from None
    if (
        raw_sha256 != POLICY_SHA256
        or not isinstance(policy, Mapping)
        or _sha256(_canonical(policy)) != POLICY_SEMANTIC_SHA256
        or policy.get("schema_version") != 1
        or policy.get("policy_id") != "cosmaneura-staging-evidence-manifest-v1"
    ):
        raise EvidenceFailure("policy.invalid")
    expected_policy_fields = {
        "schema_version",
        "policy_id",
        "repository",
        "protected_ref",
        "workflow_path",
        "event_name",
        "founder_actor",
        "signer",
        "source_resource",
        "hmac_key_versions",
        "maximum_lifetime_seconds",
        "required_files",
        "artifact_contracts",
        "policy_digests",
        "schema_fields",
        "activation_blockers",
    }
    if (
        set(policy) != expected_policy_fields
        or policy["hmac_key_versions"] != ["staging-inventory-hmac-v1"]
        or policy["maximum_lifetime_seconds"] != 900
        or set(policy["artifact_contracts"])
        != {"candidate", "inventory", "quiescence", "server_scope"}
        or set(policy["schema_fields"])
        != {
            "manifest",
            "artifact",
            "candidate",
            "inventory",
            "quiescence",
            "server_scope",
            "provenance",
            "signer_claims",
            "policy_digests",
            "source_resource",
        }
    ):
        raise EvidenceFailure("policy.invalid")
    return policy, raw_sha256


def _load_synthetic_policy(policy: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = _read_regular(SYNTHETIC_POLICY_PATH, "synthetic_policy.unreadable")
    if _digest(raw) != policy["policy_digests"]["synthetic_data"]:
        raise EvidenceFailure("synthetic_policy.digest")
    try:
        synthetic = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise EvidenceFailure("synthetic_policy.invalid") from None
    if (
        not isinstance(synthetic, Mapping)
        or synthetic.get("schema_version") != 1
        or synthetic.get("policy_id") != "cosmaneura-staging-synthetic-inventory-v1"
    ):
        raise EvidenceFailure("synthetic_policy.invalid")
    return synthetic


def _inventory_key_sets(synthetic: Mapping[str, Any]) -> dict[str, set[str]]:
    try:
        profile = synthetic["profiles"]["pre"]
        tables = synthetic["database"]["tables"]
        expected_tables = set(tables) - set(profile["excluded_tables"])
        schema_sections = set(profile["schema_descriptor"]["counts"])
        containers = set(synthetic["blob"]["containers"])
    except (KeyError, TypeError):
        raise EvidenceFailure("synthetic_policy.invalid") from None
    if len(expected_tables) != profile["table_count"]:
        raise EvidenceFailure("synthetic_policy.invalid")
    rules = {
        f"database.{table_name}.{rule['id']}"
        for table_name in expected_tables
        for rule in tables[table_name].get("rules", [])
        if "pre" in rule.get("profiles", ["pre", "post"])
    }
    identities = {
        table_name
        for table_name in expected_tables
        if tables[table_name]["disposition"] != "required_empty"
        and tables[table_name].get("identity_columns")
    }
    counts = {
        "database.non_system_schemas",
        "database.sequences",
        "database.large_objects",
        "database.alembic_version",
        "redis.logical_databases_with_keys",
        "redis.keys",
        *(f"database.schema.{name}" for name in schema_sections),
        *(f"database.{name}" for name in expected_tables),
        *(f"blob.{name}" for name in containers),
    }
    return {
        "counts": counts,
        "rule_violation_counts": rules,
        "identity_hmacs": identities,
        "state_hmacs": expected_tables,
        "blob_state_hmacs": containers,
    }


def _read_bundle(
    bundle: Path,
    policy: Mapping[str, Any],
) -> dict[str, bytes]:
    try:
        if not bundle.is_absolute() or bundle.resolve(strict=True) != bundle:
            raise EvidenceFailure("bundle.unsafe")
        mode = bundle.lstat().st_mode
        if not stat.S_ISDIR(mode) or mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise EvidenceFailure("bundle.unsafe")
        children = list(bundle.iterdir())
    except EvidenceFailure:
        raise
    except Exception:
        raise EvidenceFailure("bundle.unsafe") from None
    required = set(policy["required_files"])
    if {child.name for child in children} != required:
        raise EvidenceFailure("bundle.file_set")
    return {
        name: _read_regular(bundle / name, f"bundle.file_unsafe.{name}")
        for name in sorted(required)
    }


def _verify_checksums(files: Mapping[str, bytes]) -> None:
    expected_names = sorted(set(files) - {"SHA256SUMS"})
    expected = "".join(
        f"{_sha256(files[name])}  {name}\n" for name in expected_names
    ).encode()
    if files["SHA256SUMS"] != expected:
        raise EvidenceFailure("bundle.checksums")


def _validate_provenance(
    provenance: Any,
    claims: Any,
    policy: Mapping[str, Any],
) -> None:
    fields = policy["schema_fields"]
    provenance = _expect_keys(
        provenance,
        fields["provenance"],
        "provenance.schema",
    )
    claims = _expect_keys(
        claims,
        fields["signer_claims"],
        "signer_claims.schema",
    )
    if (
        any(not isinstance(value, str) for value in provenance.values())
        or any(not isinstance(value, str) for value in claims.values())
        or provenance["repository"] != policy["repository"]
        or provenance["protected_ref"] != policy["protected_ref"]
        or provenance["workflow_path"] != policy["workflow_path"]
        or provenance["event_name"] != policy["event_name"]
        or SHA_RE.fullmatch(provenance["workflow_sha"]) is None
        or RUN_RE.fullmatch(provenance["run_id"]) is None
        or RUN_RE.fullmatch(provenance["run_attempt"]) is None
        or claims["issuer"] != policy["signer"]["issuer"]
        or claims["subject"] != policy["signer"]["subject"]
        or claims["repository"] != provenance["repository"]
        or claims["ref"] != provenance["protected_ref"]
        or claims["sha"] != provenance["workflow_sha"]
        or claims["workflow_sha"] != provenance["workflow_sha"]
        or claims["workflow_ref"]
        != f"{policy['repository']}/{policy['workflow_path']}@{policy['protected_ref']}"
        or claims["run_id"] != provenance["run_id"]
        or claims["run_attempt"] != provenance["run_attempt"]
        or claims["actor"] != policy["founder_actor"]
        or claims["triggering_actor"] != policy["founder_actor"]
    ):
        raise EvidenceFailure("provenance.mismatch")


def _validate_time(
    observed_at: Any,
    expires_at: Any,
    now: datetime,
    policy: Mapping[str, Any],
) -> None:
    observed = _parse_utc(observed_at, "evidence.time")
    expires = _parse_utc(expires_at, "evidence.time")
    lifetime = int(policy["maximum_lifetime_seconds"])
    if (
        now.tzinfo is None
        or now.utcoffset() != UTC.utcoffset(now)
        or observed > now
        or now >= expires
        or (expires - observed).total_seconds() > lifetime
        or (now - observed).total_seconds() > lifetime
    ):
        raise EvidenceFailure("evidence.stale")


def _validate_receipt(value: Mapping[str, Any], code: str) -> str:
    supplied = value.get("receipt_sha256")
    if not isinstance(supplied, str) or HEX_RE.fullmatch(supplied) is None:
        raise EvidenceFailure(code)
    unsigned = dict(value)
    del unsigned["receipt_sha256"]
    if _sha256(_canonical(unsigned)) != supplied:
        raise EvidenceFailure(code)
    return f"sha256:{supplied}"


def _validate_common(
    artifact: Mapping[str, Any],
    manifest: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> None:
    for field in (
        "source_sha",
        "candidate_sha",
        "source_resource",
        "observed_at",
        "expires_at",
        "provenance",
        "signer_claims",
    ):
        if artifact[field] != manifest[field]:
            raise EvidenceFailure(f"artifact.binding.{field}")
    _validate_provenance(artifact["provenance"], artifact["signer_claims"], policy)


def _validate_inventory(
    inventory: Mapping[str, Any],
    manifest: Mapping[str, Any],
    policy: Mapping[str, Any],
    synthetic: Mapping[str, Any],
) -> str:
    _expect_keys(inventory, policy["schema_fields"]["inventory"], "inventory.schema")
    _validate_common(inventory, manifest, policy)
    digest_maps = (
        "identity_hmacs",
        "state_hmacs",
        "blob_state_hmacs",
    )
    count_maps = ("counts", "rule_violation_counts")
    key_sets = _inventory_key_sets(synthetic)
    profile = synthetic["profiles"]["pre"]
    if (
        inventory["schema_version"] != 2
        or inventory["policy_id"] != "cosmaneura-staging-synthetic-inventory-v1"
        or inventory["policy_sha256"]
        != policy["policy_digests"]["synthetic_data"].removeprefix("sha256:")
        or not isinstance(inventory["approved_state_sha256"], str)
        or HEX_RE.fullmatch(inventory["approved_state_sha256"]) is None
        or inventory["hmac_key_version"] != manifest["hmac_key_version"]
        or inventory["phase"] != "pre"
        or inventory["outcome"] != "pass"
        or inventory["errors"] != []
        or any(
            not isinstance(inventory[name], Mapping)
            or set(inventory[name]) != key_sets[name]
            for name in key_sets
        )
        or any(
            not isinstance(inventory[name], Mapping)
            or any(not isinstance(key, str) for key in inventory[name])
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in inventory[name].values()
            )
            for name in count_maps
        )
        or any(
            not isinstance(inventory[name], Mapping)
            or any(not isinstance(key, str) for key in inventory[name])
            or any(
                not isinstance(value, str) or HEX_RE.fullmatch(value) is None
                for value in inventory[name].values()
            )
            for name in digest_maps
        )
        or any(
            not isinstance(inventory[name], str)
            or HEX_RE.fullmatch(inventory[name]) is None
            for name in (
                "catalog_hmac",
                "schema_descriptor_sha256",
                "redis_state_hmac",
            )
        )
        or inventory["schema_descriptor_sha256"]
        != profile["schema_descriptor"]["sha256"]
        or any(value != 0 for value in inventory["rule_violation_counts"].values())
        or inventory["counts"]["database.non_system_schemas"] != 1
        or inventory["counts"]["database.sequences"] != 0
        or inventory["counts"]["database.large_objects"] != 0
        or inventory["counts"]["database.alembic_version"] != 1
        or inventory["counts"]["redis.logical_databases_with_keys"] != 0
        or inventory["counts"]["redis.keys"] != 0
        or any(
            inventory["counts"][f"database.schema.{name}"] != value
            for name, value in profile["schema_descriptor"]["counts"].items()
        )
        or any(
            inventory["counts"][f"database.{name}"] != 0
            for name in key_sets["state_hmacs"]
            if synthetic["database"]["tables"][name]["disposition"] == "required_empty"
        )
        or any(
            inventory["counts"][f"blob.{name}"] != 0
            for name, disposition in synthetic["blob"]["containers"].items()
            if disposition == "required_empty"
        )
    ):
        raise EvidenceFailure("inventory.invalid")
    return _validate_receipt(inventory, "inventory.receipt")


def _validate_candidate(
    candidate: Mapping[str, Any],
    manifest: Mapping[str, Any],
    policy: Mapping[str, Any],
    expected_schema_revision: str,
) -> str:
    _expect_keys(candidate, policy["schema_fields"]["candidate"], "candidate.schema")
    _validate_common(candidate, manifest, policy)
    expected_digests = {
        "evidence_manifest": manifest["policy_digests"]["evidence_manifest"],
        **policy["policy_digests"],
    }
    if (
        candidate["schema_version"] != 1
        or candidate["evidence_type"] != "candidate"
        or candidate["state"] != "verified"
        or isinstance(candidate["approval_pr_number"], bool)
        or not isinstance(candidate["approval_pr_number"], int)
        or candidate["approval_pr_number"] <= 0
        or candidate["protected_master_sha"] != manifest["provenance"]["workflow_sha"]
        or not isinstance(candidate["schema_revision"], str)
        or REVISION_RE.fullmatch(candidate["schema_revision"]) is None
        or candidate["schema_revision"] != expected_schema_revision
        or candidate["policy_digests"] != expected_digests
    ):
        raise EvidenceFailure("candidate.invalid")
    return _validate_receipt(candidate, "candidate.receipt")


def _validate_claim_artifact(
    artifact: Mapping[str, Any],
    *,
    kind: str,
    manifest: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> str:
    fields = policy["schema_fields"][kind]
    _expect_keys(artifact, fields, f"{kind}.schema")
    _validate_common(artifact, manifest, policy)
    expected_digests = {
        "evidence_manifest": manifest["policy_digests"]["evidence_manifest"],
        **policy["policy_digests"],
    }
    if (
        artifact["schema_version"] != 1
        or artifact["evidence_type"] != kind
        or artifact["policy_digests"] != expected_digests
    ):
        raise EvidenceFailure(f"{kind}.invalid")
    if kind == "quiescence" and artifact["state"] != "quiesced":
        raise EvidenceFailure("quiescence.invalid")
    if kind == "server_scope" and (
        artifact["state"] != "unimplemented"
        or artifact["blocker"] != "server_scope_collection_unimplemented"
    ):
        raise EvidenceFailure("server_scope.invalid")
    return _validate_receipt(artifact, f"{kind}.receipt")


def validate_bundle(
    *,
    policy: Mapping[str, Any],
    policy_sha256: str,
    bundle: Path,
    now: datetime,
) -> dict[str, Any]:
    files = _read_bundle(bundle, policy)
    _verify_checksums(files)
    synthetic = _load_synthetic_policy(policy)
    manifest = _load_json(files["evidence-manifest.json"], "manifest.invalid")
    _expect_keys(manifest, policy["schema_fields"]["manifest"], "manifest.schema")
    policy_digests = _expect_keys(
        manifest["policy_digests"],
        policy["schema_fields"]["policy_digests"],
        "manifest.policy_digests",
    )
    source_resource = _expect_keys(
        manifest["source_resource"],
        policy["schema_fields"]["source_resource"],
        "manifest.source_resource",
    )
    expected_policy_digests = {
        "evidence_manifest": f"sha256:{policy_sha256}",
        **policy["policy_digests"],
    }
    if (
        manifest["schema_version"] != 1
        or not isinstance(manifest["manifest_id"], str)
        or MANIFEST_ID_RE.fullmatch(manifest["manifest_id"]) is None
        or not isinstance(manifest["source_sha"], str)
        or SHA_RE.fullmatch(manifest["source_sha"]) is None
        or not isinstance(manifest["candidate_sha"], str)
        or SHA_RE.fullmatch(manifest["candidate_sha"]) is None
        or manifest["hmac_key_version"] not in policy["hmac_key_versions"]
        or source_resource != policy["source_resource"]
        or policy_digests != expected_policy_digests
    ):
        raise EvidenceFailure("manifest.invalid")
    _validate_time(
        manifest["observed_at"],
        manifest["expires_at"],
        now,
        policy,
    )
    _validate_provenance(manifest["provenance"], manifest["signer_claims"], policy)

    artifact_contracts = policy["artifact_contracts"]
    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) != len(artifact_contracts):
        raise EvidenceFailure("manifest.artifacts")
    entries: dict[str, Mapping[str, Any]] = {}
    for entry in artifacts:
        entry = _expect_keys(
            entry,
            policy["schema_fields"]["artifact"],
            "manifest.artifacts",
        )
        kind = entry["kind"]
        if (
            not isinstance(kind, str)
            or kind in entries
            or artifact_contracts.get(kind) != entry["path"]
            or not isinstance(entry["file_sha256"], str)
            or DIGEST_RE.fullmatch(entry["file_sha256"]) is None
            or not isinstance(entry["receipt_sha256"], str)
            or DIGEST_RE.fullmatch(entry["receipt_sha256"]) is None
        ):
            raise EvidenceFailure("manifest.artifacts")
        entries[kind] = entry
    if set(entries) != set(artifact_contracts):
        raise EvidenceFailure("manifest.artifacts")
    unsigned_manifest = dict(manifest)
    supplied_evidence_digest = unsigned_manifest.pop("evidence_set_sha256")
    if (
        not isinstance(supplied_evidence_digest, str)
        or DIGEST_RE.fullmatch(supplied_evidence_digest) is None
        or _digest(_canonical(unsigned_manifest)) != supplied_evidence_digest
    ):
        raise EvidenceFailure("manifest.evidence_set_digest")

    values = {
        kind: _load_json(files[path], f"{kind}.invalid")
        for kind, path in artifact_contracts.items()
    }
    receipts = {
        "candidate": _validate_candidate(
            values["candidate"],
            manifest,
            policy,
            synthetic["profiles"]["pre"]["revision"],
        ),
        "inventory": _validate_inventory(
            values["inventory"],
            manifest,
            policy,
            synthetic,
        ),
        "quiescence": _validate_claim_artifact(
            values["quiescence"],
            kind="quiescence",
            manifest=manifest,
            policy=policy,
        ),
        "server_scope": _validate_claim_artifact(
            values["server_scope"],
            kind="server_scope",
            manifest=manifest,
            policy=policy,
        ),
    }
    for kind, entry in entries.items():
        path = artifact_contracts[kind]
        if entry["file_sha256"] != _digest(files[path]):
            raise EvidenceFailure(f"artifact.file_digest.{kind}")
        if entry["receipt_sha256"] != receipts[kind]:
            raise EvidenceFailure(f"artifact.receipt_digest.{kind}")

    return {
        "schema_version": 1,
        "policy_id": policy["policy_id"],
        "manifest_id": manifest["manifest_id"],
        "evidence_set_sha256": supplied_evidence_digest,
        "outcome": "contract_valid",
        "activation_state": "blocked",
        "activation_blockers": policy["activation_blockers"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=POLICY_PATH)
    parser.add_argument("--bundle", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        policy, policy_sha256 = load_policy(args.policy)
        result = validate_bundle(
            policy=policy,
            policy_sha256=policy_sha256,
            bundle=args.bundle,
            now=datetime.now(UTC),
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except EvidenceFailure as exc:
        print(f"staging evidence manifest failed: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print("staging evidence manifest failed: internal_error", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
