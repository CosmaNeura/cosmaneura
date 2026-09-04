#!/usr/bin/env python3
"""Authenticate one validated staging server-scope receipt with its HMAC key.

The checked-in authentication and server-scope policies are deliberately
disabled. This module has no network or subprocess capability and cannot
authorize a restore; an activated future caller must still provide separately
verified provider and GitHub-attestation receipts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from scripts import staging_server_scope_evidence as server_scope
    from scripts import validate_staging_evidence_manifest as evidence_manifest
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    import staging_server_scope_evidence as server_scope
    import validate_staging_evidence_manifest as evidence_manifest


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = (
    ROOT / "contracts/staging-rehearsal/v1/server-scope-authentication-policy.json"
)
POLICY_SHA256 = "ad4b5ddd3e7cf8f6c50e34f63f1d2bd1d46d36f418fcd64b262b514daa2a28d5"
MAXIMUM_POLICY_BYTES = 1024 * 1024
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")

BASE_BINDING_FIELDS = frozenset(
    {
        "schema_version",
        "binding_policy_id",
        "evidence_manifest_policy_id",
        "evidence_manifest_policy_sha256",
        "manifest_id",
        "manifest_file_sha256",
        "bundle_index_sha256",
        "evidence_set_sha256",
        "source_sha",
        "candidate_sha",
        "source_server_resource_id",
        "source_database_name",
        "approved_restore_time",
        "observed_at",
        "expires_at",
        "candidate_approval_pr_number",
        "candidate_protected_master_sha",
        "candidate_schema_revision",
        "candidate_receipt_sha256",
        "inventory_receipt_sha256",
        "quiescence_receipt_sha256",
        "server_scope_receipt_sha256",
        "repository",
        "protected_ref",
        "workflow_path",
        "workflow_sha",
        "run_id",
        "run_attempt",
        "event_name",
        "signer_issuer",
        "signer_subject",
    }
)
RECEIPT_FIELDS = (
    "schema_version",
    "policy_id",
    "policy_sha256",
    "manifest_id",
    "manifest_file_sha256",
    "bundle_index_sha256",
    "evidence_set_sha256",
    "server_scope_file_sha256",
    "server_scope_receipt_sha256",
    "source_sha",
    "source_server_resource_id",
    "source_database_name",
    "source_resource_hmac",
    "primary_database_identity_hmac",
    "server_scope_key_version",
    "server_scope_evidence_hmac",
    "observed_at",
    "expires_at",
    "authenticated_at",
    "pass",
)
ACTIVATION_BLOCKERS = (
    "server_scope_authentication_policy_disabled",
    "server_scope_policy_unconfigured",
    "trusted_key_delivery_unimplemented",
    "provider_observation_unimplemented",
    "attestation_verification_unimplemented",
    "workflow_integration_unimplemented",
    "restore_activation_unimplemented",
)
OUTPUT_ACTIVATION_BLOCKERS = (
    "provider_observation_unimplemented",
    "attestation_verification_unimplemented",
    "workflow_integration_unimplemented",
    "restore_activation_unimplemented",
)


class AuthenticationFailure(RuntimeError):
    """Bounded authentication failure safe to report without evidence contents."""


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        raise AuthenticationFailure("authentication.non_canonical_value") from None


def _digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _expect_keys(
    value: Any, expected: set[str] | frozenset[str], code: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(expected):
        raise AuthenticationFailure(code)
    return value


def _parse_utc(value: Any, code: str) -> datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise AuthenticationFailure(code)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        raise AuthenticationFailure(code) from None
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise AuthenticationFailure(code)
    return parsed


def _trusted_now(now: datetime) -> tuple[datetime, str]:
    if (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() != UTC.utcoffset(now)
        or now.microsecond != 0
    ):
        raise AuthenticationFailure("authentication.clock_invalid")
    normalized = now.astimezone(UTC)
    return normalized, normalized.strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _read_regular(path: Path, code: str) -> bytes:
    try:
        before = path.lstat()
        if (
            not path.is_absolute()
            or not stat.S_ISREG(before.st_mode)
            or before.st_uid not in {0, os.geteuid()}
            or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or not 0 < before.st_size <= MAXIMUM_POLICY_BYTES
        ):
            raise AuthenticationFailure(code)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            after = os.fstat(descriptor)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise AuthenticationFailure(code)
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                raw = handle.read(MAXIMUM_POLICY_BYTES + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except AuthenticationFailure:
        raise
    except OSError:
        raise AuthenticationFailure(code) from None
    if not raw or len(raw) > MAXIMUM_POLICY_BYTES:
        raise AuthenticationFailure(code)
    return raw


def validate_authentication_policy(
    policy: Any,
    *,
    executable: bool,
    server_scope_policy_sha256: str = server_scope.CANONICAL_POLICY_SHA256,
) -> Mapping[str, Any]:
    policy = _expect_keys(
        policy,
        {
            "schema_version",
            "policy_id",
            "activation_ready",
            "manifest_policy",
            "server_scope_policy",
            "source",
            "hmac",
            "maximum_lifetime_seconds",
            "receipt_fields",
            "activation_blockers",
            "output_activation_blockers",
        },
        "authentication_policy.invalid",
    )
    hmac_policy = policy["hmac"]
    if not isinstance(hmac_policy, Mapping):
        raise AuthenticationFailure("authentication_policy.invalid")
    if (
        type(policy["schema_version"]) is not int
        or policy["schema_version"] != 1
        or policy["policy_id"] != "cosmaneura-staging-server-scope-authentication-v1"
        or not isinstance(policy["activation_ready"], bool)
        or policy["manifest_policy"]
        != {
            "policy_id": "cosmaneura-staging-evidence-manifest-v2",
            "sha256": f"sha256:{evidence_manifest.POLICY_SHA256}",
        }
        or policy["server_scope_policy"]
        != {
            "policy_id": "cosmaneura-staging-server-scope-evidence-v1",
            "sha256": f"sha256:{server_scope_policy_sha256}",
        }
        or policy["source"]
        != {
            "resource_id": (
                "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/"
                "resourceGroups/cosmaneura-staging/providers/"
                "Microsoft.DBforPostgreSQL/flexibleServers/"
                "cosmaneura-staging-postgres"
            ),
            "database_name": "staging",
        }
        or type(hmac_policy.get("minimum_key_bytes")) is not int
        or type(hmac_policy.get("maximum_key_bytes")) is not int
        or hmac_policy
        != {
            "algorithm": "HMAC-SHA-256",
            "domain": "cosmaneura:staging-server-scope-evidence:v1",
            "allowed_key_versions": ["staging-server-scope-hmac-v1"],
            "minimum_key_bytes": server_scope.MINIMUM_HMAC_KEY_BYTES,
            "maximum_key_bytes": server_scope.MAXIMUM_HMAC_KEY_BYTES,
            "key_bytes_include_all_file_content": True,
        }
        or type(policy["maximum_lifetime_seconds"]) is not int
        or policy["maximum_lifetime_seconds"] != 900
        or policy["receipt_fields"] != list(RECEIPT_FIELDS)
        or policy["activation_blockers"] != list(ACTIVATION_BLOCKERS)
        or policy["output_activation_blockers"] != list(OUTPUT_ACTIVATION_BLOCKERS)
    ):
        raise AuthenticationFailure("authentication_policy.invalid")
    if executable and policy["activation_ready"] is not True:
        raise AuthenticationFailure("authentication_policy.disabled")
    return policy


def _parse_authentication_policy_document(
    raw: bytes,
    policy_sha256: str,
    *,
    executable: bool,
    server_scope_policy_sha256: str,
) -> Mapping[str, Any]:
    if (
        type(raw) is not bytes
        or not 0 < len(raw) <= MAXIMUM_POLICY_BYTES
        or not isinstance(policy_sha256, str)
        or HEX_RE.fullmatch(policy_sha256) is None
        or not isinstance(server_scope_policy_sha256, str)
        or HEX_RE.fullmatch(server_scope_policy_sha256) is None
    ):
        raise AuthenticationFailure("authentication_policy.binding_mismatch")
    if hashlib.sha256(raw).hexdigest() != policy_sha256:
        raise AuthenticationFailure("authentication_policy.binding_mismatch")
    try:
        policy = json.loads(raw)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise AuthenticationFailure("authentication_policy.invalid") from None
    try:
        return validate_authentication_policy(
            policy,
            executable=executable,
            server_scope_policy_sha256=server_scope_policy_sha256,
        )
    except AuthenticationFailure:
        raise
    except (TypeError, ValueError, RecursionError):
        raise AuthenticationFailure("authentication_policy.invalid") from None


def load_authentication_policy_document(
    path: Path = POLICY_PATH,
) -> tuple[Mapping[str, Any], str, bytes]:
    try:
        if path.resolve(strict=True) != POLICY_PATH.resolve(strict=True):
            raise AuthenticationFailure("authentication_policy.untrusted_path")
    except AuthenticationFailure:
        raise
    except OSError:
        raise AuthenticationFailure("authentication_policy.unreadable") from None
    raw = _read_regular(path, "authentication_policy.unreadable")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != POLICY_SHA256:
        raise AuthenticationFailure("authentication_policy.hash_mismatch")
    try:
        policy = json.loads(raw)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise AuthenticationFailure("authentication_policy.invalid") from None
    return validate_authentication_policy(policy, executable=False), digest, raw


def load_authentication_policy(
    path: Path = POLICY_PATH,
) -> tuple[Mapping[str, Any], str]:
    policy, digest, _raw = load_authentication_policy_document(path)
    return policy, digest


def _authenticate_validated_bundle(
    *,
    authentication_policy_bytes: bytes,
    authentication_policy_sha256: str,
    server_scope_policy_bytes: bytes,
    server_scope_policy_sha256: str,
    snapshot: evidence_manifest.ValidatedEvidenceBundle,
    key: bytes,
    clock: Callable[[], datetime] = _utc_now,
) -> dict[str, Any]:
    """Authenticate bytes minted by this module's validated-bundle path."""

    policy = _parse_authentication_policy_document(
        authentication_policy_bytes,
        authentication_policy_sha256,
        executable=True,
        server_scope_policy_sha256=server_scope_policy_sha256,
    )
    if (
        type(snapshot) is not evidence_manifest.ValidatedEvidenceBundle
        or not snapshot._is_validated_snapshot()
    ):
        raise AuthenticationFailure("validated_bundle.invalid")
    result_bytes = snapshot.result_bytes
    server_scope_bytes = snapshot.server_scope_bytes
    if (
        type(result_bytes) is not bytes
        or not 0 < len(result_bytes) <= evidence_manifest.MAXIMUM_FILE_BYTES
        or type(server_scope_bytes) is not bytes
        or not 0 < len(server_scope_bytes) <= evidence_manifest.MAXIMUM_FILE_BYTES
    ):
        raise AuthenticationFailure("validated_bundle.invalid")
    try:
        snapshot_result = json.loads(result_bytes)
        server_receipt = json.loads(server_scope_bytes)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise AuthenticationFailure("validated_bundle.invalid") from None
    result = _expect_keys(
        snapshot_result,
        {
            "schema_version",
            "policy_id",
            "manifest_id",
            "evidence_set_sha256",
            "evidence_binding",
            "outcome",
            "activation_state",
            "activation_blockers",
        },
        "validated_bundle.invalid",
    )
    binding = _expect_keys(
        result["evidence_binding"], BASE_BINDING_FIELDS, "validated_bundle.invalid"
    )
    if (
        result["schema_version"] != 1
        or result["policy_id"] != policy["manifest_policy"]["policy_id"]
        or result["manifest_id"] != binding["manifest_id"]
        or result["evidence_set_sha256"] != binding["evidence_set_sha256"]
        or result["outcome"] != "contract_valid"
        or result["activation_state"] != "blocked"
        or binding["evidence_manifest_policy_id"]
        != policy["manifest_policy"]["policy_id"]
        or binding["evidence_manifest_policy_sha256"]
        != policy["manifest_policy"]["sha256"]
        or binding["source_server_resource_id"] != policy["source"]["resource_id"]
        or binding["source_database_name"] != policy["source"]["database_name"]
    ):
        raise AuthenticationFailure("validated_bundle.binding_mismatch")
    for field in (
        "manifest_file_sha256",
        "bundle_index_sha256",
        "evidence_set_sha256",
        "server_scope_receipt_sha256",
    ):
        if (
            not isinstance(binding[field], str)
            or DIGEST_RE.fullmatch(binding[field]) is None
        ):
            raise AuthenticationFailure("validated_bundle.binding_mismatch")

    if not isinstance(server_receipt, Mapping):
        raise AuthenticationFailure("server_scope.snapshot_invalid")
    if (
        _digest(_canonical(server_receipt)) != binding["server_scope_receipt_sha256"]
        or server_receipt.get("source_revision") != binding["source_sha"]
        or server_receipt.get("key_version")
        not in policy["hmac"]["allowed_key_versions"]
    ):
        raise AuthenticationFailure("server_scope.binding_mismatch")
    observed = _parse_utc(binding["observed_at"], "validated_bundle.time_invalid")
    expires = _parse_utc(binding["expires_at"], "validated_bundle.time_invalid")
    if (
        expires <= observed
        or (expires - observed).total_seconds() > policy["maximum_lifetime_seconds"]
    ):
        raise AuthenticationFailure("validated_bundle.stale")

    try:
        verified = server_scope.verify_evidence_hmac(
            server_receipt,
            server_scope_policy_bytes,
            server_scope_policy_sha256,
            key,
        )
    except server_scope.EvidenceFailure as exc:
        raise AuthenticationFailure(str(exc)) from None
    if (
        verified.source_resource_id != policy["source"]["resource_id"]
        or verified.primary_database != policy["source"]["database_name"]
        or verified.capture_completed_at > observed
        or (observed - verified.capture_started_at).total_seconds()
        > policy["maximum_lifetime_seconds"]
    ):
        raise AuthenticationFailure("server_scope.binding_mismatch")
    trusted_now, authenticated_at = _trusted_now(clock())
    if observed > trusted_now or trusted_now >= expires:
        raise AuthenticationFailure("validated_bundle.stale")

    receipt = {
        "schema_version": 1,
        "policy_id": policy["policy_id"],
        "policy_sha256": f"sha256:{authentication_policy_sha256}",
        "manifest_id": binding["manifest_id"],
        "manifest_file_sha256": binding["manifest_file_sha256"],
        "bundle_index_sha256": binding["bundle_index_sha256"],
        "evidence_set_sha256": binding["evidence_set_sha256"],
        "server_scope_file_sha256": _digest(server_scope_bytes),
        "server_scope_receipt_sha256": binding["server_scope_receipt_sha256"],
        "source_sha": binding["source_sha"],
        "source_server_resource_id": binding["source_server_resource_id"],
        "source_database_name": binding["source_database_name"],
        "source_resource_hmac": server_receipt["source_resource_hmac"],
        "primary_database_identity_hmac": server_receipt[
            "primary_database_identity_hmac"
        ],
        "server_scope_key_version": server_receipt["key_version"],
        "server_scope_evidence_hmac": server_receipt["evidence_hmac"],
        "observed_at": binding["observed_at"],
        "expires_at": binding["expires_at"],
        "authenticated_at": authenticated_at,
        "pass": True,
    }
    if set(receipt) != set(policy["receipt_fields"]):
        raise AuthenticationFailure("authentication_receipt.invalid")
    return {
        "schema_version": 1,
        "policy_id": policy["policy_id"],
        "server_scope_authentication_receipt": receipt,
        "server_scope_authentication_receipt_sha256": _digest(_canonical(receipt)),
        "activation_state": "blocked",
        "activation_blockers": policy["output_activation_blockers"],
    }


def _authenticate_evidence_bundle_with_documents(
    *,
    authentication_policy_bytes: bytes,
    authentication_policy_sha256: str,
    server_scope_policy_bytes: bytes,
    server_scope_policy_sha256: str,
    bundle: Path,
    key: bytes,
    validation_now: datetime,
    clock: Callable[[], datetime] = _utc_now,
) -> dict[str, Any]:
    """Dependency-injected implementation for internal use and unit tests."""

    policy = _parse_authentication_policy_document(
        authentication_policy_bytes,
        authentication_policy_sha256,
        executable=True,
        server_scope_policy_sha256=server_scope_policy_sha256,
    )
    manifest_policy, manifest_policy_sha256 = evidence_manifest.load_policy(
        evidence_manifest.POLICY_PATH
    )
    if f"sha256:{manifest_policy_sha256}" != policy["manifest_policy"]["sha256"]:
        raise AuthenticationFailure("authentication_policy.binding_mismatch")
    snapshot = evidence_manifest.validate_bundle_snapshot(
        policy=manifest_policy,
        policy_sha256=manifest_policy_sha256,
        bundle=bundle,
        now=validation_now,
    )
    return _authenticate_validated_bundle(
        authentication_policy_bytes=authentication_policy_bytes,
        authentication_policy_sha256=authentication_policy_sha256,
        server_scope_policy_bytes=server_scope_policy_bytes,
        server_scope_policy_sha256=server_scope_policy_sha256,
        snapshot=snapshot,
        key=key,
        clock=clock,
    )


def authenticate_evidence_bundle(
    *,
    bundle: Path,
    key: bytes,
    validation_now: datetime,
    clock: Callable[[], datetime] = _utc_now,
) -> dict[str, Any]:
    """Authenticate a bundle under the exact checked-in dormant policies."""

    authentication_policy, authentication_policy_sha256, authentication_bytes = (
        load_authentication_policy_document()
    )
    validate_authentication_policy(
        authentication_policy,
        executable=True,
        server_scope_policy_sha256=server_scope.CANONICAL_POLICY_SHA256,
    )
    _, server_scope_policy_sha256, server_scope_policy_bytes = (
        server_scope.read_policy_document()
    )
    return _authenticate_evidence_bundle_with_documents(
        authentication_policy_bytes=authentication_bytes,
        authentication_policy_sha256=authentication_policy_sha256,
        server_scope_policy_bytes=server_scope_policy_bytes,
        server_scope_policy_sha256=server_scope_policy_sha256,
        bundle=bundle,
        key=key,
        validation_now=validation_now,
        clock=clock,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--hmac-key-file", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        (
            authentication_policy,
            authentication_policy_sha256,
            authentication_policy_bytes,
        ) = load_authentication_policy_document()
        validate_authentication_policy(
            authentication_policy,
            executable=True,
            server_scope_policy_sha256=server_scope.CANONICAL_POLICY_SHA256,
        )
        _, server_policy_sha256, server_scope_policy_bytes = (
            server_scope.read_policy_document()
        )
        key = server_scope.read_hmac_key(args.hmac_key_file)
        validation_now = _utc_now()
        result = _authenticate_evidence_bundle_with_documents(
            authentication_policy_bytes=authentication_policy_bytes,
            authentication_policy_sha256=authentication_policy_sha256,
            server_scope_policy_bytes=server_scope_policy_bytes,
            server_scope_policy_sha256=server_policy_sha256,
            bundle=args.bundle,
            key=key,
            validation_now=validation_now,
            clock=_utc_now,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (AuthenticationFailure, evidence_manifest.EvidenceFailure) as exc:
        print(
            json.dumps({"errors": [str(exc)], "pass": False}),
            file=sys.stderr,
        )
        return 1
    except server_scope.EvidenceFailure as exc:
        print(
            json.dumps({"errors": [str(exc)], "pass": False}),
            file=sys.stderr,
        )
        return 1
    except Exception:
        print(
            json.dumps({"errors": ["internal_error"], "pass": False}),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
