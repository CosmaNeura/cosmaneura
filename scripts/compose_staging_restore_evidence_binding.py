#!/usr/bin/env python3
"""Compose three sealed staging proofs into one safe-submit binding.

The checked-in policy is deliberately disabled. This module has no network,
credential, subprocess, marker, restore, database, deployment, or Azure SDK
capability.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import fields as dataclass_fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

try:
    from scripts import authenticate_staging_github_attestation as github_attestation
    from scripts import authenticate_staging_server_scope_receipt as server_auth
    from scripts import staging_restore_safe_submit as safe_submit
    from scripts import (
        verify_staging_source_provider_observation as provider_observation,
    )
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    import authenticate_staging_github_attestation as github_attestation
    import authenticate_staging_server_scope_receipt as server_auth
    import staging_restore_safe_submit as safe_submit
    import verify_staging_source_provider_observation as provider_observation


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = (
    ROOT / "contracts/staging-rehearsal/v1/restore-evidence-composition-policy.json"
)
POLICY_SHA256 = "ba97547aa95686af448c169949379bb8ed1c20d4683adc3b1c6a70114ca6c1c3"
POLICY_TEMPLATE_SHA256 = (
    "00e0030d650b10475d683b060bffe6a66270400d489bc39bae2f65d4c41d0916"
)
MAXIMUM_DOCUMENT_BYTES = 1024 * 1024
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX_RE = re.compile(r"^[0-9a-f]{64}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REVISION_RE = re.compile(r"^[0-9a-f]{12}$")
RUN_RE = re.compile(r"^[1-9][0-9]{0,19}$")
MANIFEST_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{11,63}$")
UTC_RE = re.compile(r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")

OUTPUT_FIELDS = tuple(
    (
        "schema_version binding_policy_id evidence_manifest_policy_id "
        "evidence_manifest_policy_sha256 manifest_id manifest_file_sha256 "
        "bundle_index_sha256 evidence_set_sha256 source_sha candidate_sha "
        "source_server_resource_id source_database_name approved_restore_time "
        "observed_at expires_at candidate_approval_pr_number "
        "candidate_protected_master_sha candidate_schema_revision "
        "candidate_receipt_sha256 inventory_receipt_sha256 "
        "quiescence_receipt_sha256 server_scope_receipt_sha256 repository "
        "protected_ref workflow_path workflow_sha run_id run_attempt event_name "
        "signer_issuer signer_subject attestation_receipt_sha256 "
        "provider_receipt_sha256 server_scope_authentication_receipt_sha256"
    ).split()
)


class CompositionFailure(RuntimeError):
    """Bounded composition failure safe to report without receipt contents."""


def _fail(code: str) -> NoReturn:
    raise CompositionFailure(code)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        raise CompositionFailure("composition.non_canonical") from None


def _digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _load_mapping(raw: bytes, code: str, *, maximum: int) -> Mapping[str, Any]:
    if type(raw) is not bytes or not 0 < len(raw) <= maximum:
        _fail(code)

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail(code)
            result[key] = value
        return result

    def reject_nonfinite(_value: str) -> NoReturn:
        _fail(code)

    def parse_integer(value: str) -> int:
        if len(value.removeprefix("-")) > 20:
            _fail(code)
        return int(value)

    try:
        value = json.loads(
            raw,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_nonfinite,
            parse_float=reject_nonfinite,
            parse_int=parse_integer,
        )
    except CompositionFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise CompositionFailure(code) from None
    if not isinstance(value, Mapping):
        _fail(code)
    nodes = 0

    def bounded(item: Any, depth: int = 0) -> None:
        nonlocal nodes
        nodes += 1
        if depth > 64 or nodes > 100_000:
            _fail(code)
        if isinstance(item, str) and len(item) > maximum:
            _fail(code)
        if isinstance(item, Mapping):
            for key, child in item.items():
                bounded(key, depth + 1)
                bounded(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                bounded(child, depth + 1)

    try:
        bounded(value)
    except RecursionError:
        raise CompositionFailure(code) from None
    return value


def _expect_exact_keys(
    value: Any, expected: Collection[str], code: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(expected):
        _fail(code)
    return value


def _parse_utc(value: Any, code: str) -> datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        _fail(code)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        raise CompositionFailure(code) from None
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        _fail(code)
    return parsed


def _trusted_now(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:
        raise CompositionFailure("composition_clock.unavailable") from None
    try:
        if (
            type(value) is not datetime
            or value.tzinfo is None
            or value.utcoffset() != timedelta(0)
            or value.microsecond != 0
        ):
            _fail("composition_clock.invalid")
        return value.astimezone(UTC)
    except CompositionFailure:
        raise
    except Exception:
        raise CompositionFailure("composition_clock.invalid") from None


def _expected_policy_pins() -> dict[str, dict[str, str]]:
    return {
        "evidence_manifest": {
            "policy_id": "cosmaneura-staging-evidence-manifest-v2",
            "sha256": f"sha256:{server_auth.evidence_manifest.POLICY_SHA256}",
        },
        "server_scope_authentication": {
            "policy_id": "cosmaneura-staging-server-scope-authentication-v1",
            "sha256": f"sha256:{server_auth.POLICY_SHA256}",
        },
        "source_provider_observation": {
            "policy_id": "cosmaneura-staging-source-provider-observation-v1",
            "sha256": f"sha256:{provider_observation.POLICY_SHA256}",
        },
        "github_attestation_authentication": {
            "policy_id": "cosmaneura-staging-github-attestation-authentication-v1",
            "sha256": f"sha256:{github_attestation.POLICY_SHA256}",
        },
    }


def validate_composition_policy(policy: Any, *, executable: bool) -> Mapping[str, Any]:
    if not isinstance(policy, Mapping):
        _fail("composition_policy.invalid")
    ready = policy.get("activation_ready")
    activation = policy.get("activation")
    if (
        type(ready) is not bool
        or not isinstance(activation, Mapping)
        or any(type(value) is not bool for value in activation.values())
    ):
        _fail("composition_policy.invalid")
    normalized = dict(policy)
    normalized["activation_ready"] = False
    normalized["activation"] = dict.fromkeys(activation, False)
    try:
        normalized_sha = hashlib.sha256(_canonical(normalized)).hexdigest()
    except CompositionFailure:
        _fail("composition_policy.invalid")
    if normalized_sha != POLICY_TEMPLATE_SHA256:
        _fail("composition_policy.invalid")
    inputs = policy["inputs"]
    actual_output_fields = tuple(
        field.name
        for field in dataclass_fields(safe_submit.RestoreEvidenceBinding)
        if field.name != "_binding_seal"
    )
    if (
        policy["policy_pins"] != _expected_policy_pins()
        or inputs["server_scope_authentication_receipt"]["fields"]
        != list(server_auth.RECEIPT_FIELDS)
        or policy["output"]["fields"] != list(OUTPUT_FIELDS)
        or actual_output_fields != OUTPUT_FIELDS
        or policy["output"]["binding_policy_id"] != policy["policy_id"]
    ):
        _fail("composition_policy.invalid")
    if executable and (
        ready is not True or any(value is not True for value in activation.values())
    ):
        _fail("composition_policy.disabled")
    return policy


def _parse_policy_document(
    raw: bytes, claimed_sha256: str, *, executable: bool
) -> Mapping[str, Any]:
    if (
        type(raw) is not bytes
        or not 0 < len(raw) <= MAXIMUM_DOCUMENT_BYTES
        or not isinstance(claimed_sha256, str)
        or HEX_RE.fullmatch(claimed_sha256) is None
        or hashlib.sha256(raw).hexdigest() != claimed_sha256
    ):
        _fail("composition_policy.hash_mismatch")
    return validate_composition_policy(
        _load_mapping(
            raw, "composition_policy.invalid", maximum=MAXIMUM_DOCUMENT_BYTES
        ),
        executable=executable,
    )


def load_composition_policy_document(
    path: Path = POLICY_PATH,
) -> tuple[Mapping[str, Any], str, bytes]:
    try:
        raw = path.read_bytes()
    except OSError:
        raise CompositionFailure("composition_policy.read_failed") from None
    digest = hashlib.sha256(raw).hexdigest()
    if digest != POLICY_SHA256:
        _fail("composition_policy.hash_mismatch")
    return _parse_policy_document(raw, digest, executable=False), digest, raw


def _validate_base(
    binding: server_auth.ServerAuthenticatedSafeSubmitBinding,
    policy: Mapping[str, Any],
) -> tuple[Mapping[str, Any], bytes, datetime, datetime]:
    if (
        type(binding) is not server_auth.ServerAuthenticatedSafeSubmitBinding
        or not binding._is_authenticator_minted()
    ):
        _fail("server_authenticated_binding.invalid")
    try:
        raw = binding.record_bytes
    except Exception:
        raise CompositionFailure("server_authenticated_binding.invalid") from None
    record = _expect_exact_keys(
        _load_mapping(
            raw, "server_authenticated_binding.invalid", maximum=MAXIMUM_DOCUMENT_BYTES
        ),
        server_auth.SERVER_AUTHENTICATED_SAFE_SUBMIT_FIELDS,
        "server_authenticated_binding.invalid",
    )
    digest_fields = {
        "evidence_manifest_policy_sha256",
        "manifest_file_sha256",
        "bundle_index_sha256",
        "evidence_set_sha256",
        "candidate_receipt_sha256",
        "inventory_receipt_sha256",
        "quiescence_receipt_sha256",
        "server_scope_receipt_sha256",
        "server_scope_authentication_receipt_sha256",
    }
    pins = policy["policy_pins"]
    approval_pr = record.get("candidate_approval_pr_number")
    if (
        _canonical(record) != raw
        or type(record.get("schema_version")) is not int
        or record["schema_version"] != policy["output"]["schema_version"]
        or record.get("binding_policy_id") != policy["policy_id"]
        or record.get("evidence_manifest_policy_id")
        != pins["evidence_manifest"]["policy_id"]
        or record.get("evidence_manifest_policy_sha256")
        != pins["evidence_manifest"]["sha256"]
        or any(
            not isinstance(record.get(field), str)
            or DIGEST_RE.fullmatch(record[field]) is None
            for field in digest_fields
        )
        or not isinstance(record.get("manifest_id"), str)
        or MANIFEST_ID_RE.fullmatch(record["manifest_id"]) is None
        or any(
            not isinstance(record.get(field), str)
            or SHA_RE.fullmatch(record[field]) is None
            for field in (
                "source_sha",
                "candidate_sha",
                "candidate_protected_master_sha",
                "workflow_sha",
            )
        )
        or record["candidate_protected_master_sha"] != record["workflow_sha"]
        or isinstance(approval_pr, bool)
        or not isinstance(approval_pr, int)
        or approval_pr <= 0
        or not isinstance(record.get("candidate_schema_revision"), str)
        or REVISION_RE.fullmatch(record["candidate_schema_revision"]) is None
        or not isinstance(record.get("run_id"), str)
        or RUN_RE.fullmatch(record["run_id"]) is None
        or not isinstance(record.get("run_attempt"), str)
        or RUN_RE.fullmatch(record["run_attempt"]) is None
        or any(
            not isinstance(record.get(field), str) or not record[field]
            for field in (
                "source_server_resource_id",
                "source_database_name",
                "repository",
                "protected_ref",
                "workflow_path",
                "event_name",
                "signer_issuer",
                "signer_subject",
            )
        )
    ):
        _fail("server_authenticated_binding.invalid")
    observed = _parse_utc(record.get("observed_at"), "binding_time.invalid")
    approved = _parse_utc(record.get("approved_restore_time"), "binding_time.invalid")
    expires = _parse_utc(record.get("expires_at"), "binding_time.invalid")
    if (
        approved != observed
        or expires <= observed
        or (expires - observed).total_seconds()
        > policy["time"]["maximum_upstream_receipt_lifetime_seconds"][
            "server_scope_authentication"
        ]
    ):
        _fail("server_authenticated_binding.stale")
    return record, raw, observed, expires


def _validate_server_receipt(
    raw: bytes,
    *,
    base: Mapping[str, Any],
    observed: datetime,
    expires: datetime,
    policy: Mapping[str, Any],
) -> tuple[Mapping[str, Any], datetime]:
    maximum = policy["inputs"]["server_scope_authentication_receipt"]["maximum_bytes"]
    receipt = _expect_exact_keys(
        _load_mapping(raw, "server_authentication_receipt.invalid", maximum=maximum),
        server_auth.RECEIPT_FIELDS,
        "server_authentication_receipt.invalid",
    )
    pin = policy["policy_pins"]["server_scope_authentication"]
    digest_fields = (
        "policy_sha256",
        "manifest_file_sha256",
        "bundle_index_sha256",
        "evidence_set_sha256",
        "server_scope_file_sha256",
        "server_scope_receipt_sha256",
    )
    if (
        _canonical(receipt) != raw
        or _digest(raw) != base["server_scope_authentication_receipt_sha256"]
        or type(receipt.get("schema_version")) is not int
        or receipt["schema_version"] != 1
        or receipt.get("policy_id") != pin["policy_id"]
        or receipt.get("policy_sha256") != pin["sha256"]
        or receipt.get("pass") is not True
        or any(
            not isinstance(receipt.get(field), str)
            or DIGEST_RE.fullmatch(receipt[field]) is None
            for field in digest_fields
        )
        or any(
            not isinstance(receipt.get(field), str)
            or HEX_RE.fullmatch(receipt[field]) is None
            for field in (
                "source_resource_hmac",
                "primary_database_identity_hmac",
                "server_scope_evidence_hmac",
            )
        )
        or not isinstance(receipt.get("server_scope_key_version"), str)
        or not receipt["server_scope_key_version"]
        or any(
            receipt.get(field) != base.get(field)
            for field in policy["cross_bindings"][
                "server_authentication_fields_equal_base"
            ]
        )
    ):
        _fail("server_authentication_receipt.invalid")
    authenticated = _parse_utc(
        receipt.get("authenticated_at"), "server_authentication_receipt.invalid"
    )
    receipt_expires = _parse_utc(
        receipt.get("expires_at"), "server_authentication_receipt.invalid"
    )
    if (
        authenticated < observed
        or authenticated >= expires
        or receipt_expires != expires
    ):
        _fail("server_authentication_receipt.stale")
    return receipt, authenticated


def _sealed_provider_receipt(
    value: provider_observation.VerifiedProviderObservation,
) -> tuple[Mapping[str, Any], bytes, str]:
    if (
        type(value) is not provider_observation.VerifiedProviderObservation
        or not value._is_observer_minted()
    ):
        _fail("provider_observation.invalid")
    try:
        raw, claimed = value.receipt_bytes, value.receipt_sha256
    except Exception:
        raise CompositionFailure("provider_observation.invalid") from None
    receipt = _expect_exact_keys(
        _load_mapping(
            raw, "provider_observation.invalid", maximum=MAXIMUM_DOCUMENT_BYTES
        ),
        provider_observation.RECEIPT_FIELDS,
        "provider_observation.invalid",
    )
    if _canonical(receipt) != raw or claimed != _digest(raw):
        _fail("provider_observation.invalid")
    return receipt, raw, claimed


def _sealed_attestation_receipt(
    value: github_attestation.VerifiedGitHubAttestation,
) -> tuple[Mapping[str, Any], bytes, str]:
    if (
        type(value) is not github_attestation.VerifiedGitHubAttestation
        or not value._is_authenticator_minted()
    ):
        _fail("github_attestation.invalid")
    try:
        raw, claimed = value.receipt_bytes, value.receipt_sha256
    except Exception:
        raise CompositionFailure("github_attestation.invalid") from None
    receipt = _expect_exact_keys(
        _load_mapping(
            raw, "github_attestation.invalid", maximum=MAXIMUM_DOCUMENT_BYTES
        ),
        github_attestation.RECEIPT_FIELDS,
        "github_attestation.invalid",
    )
    if _canonical(receipt) != raw or claimed != _digest(raw):
        _fail("github_attestation.invalid")
    return receipt, raw, claimed


def _validate_provider_receipt(
    receipt: Mapping[str, Any],
    *,
    receipt_digest: str,
    base: Mapping[str, Any],
    base_digest: str,
    base_observed: datetime,
    base_expires: datetime,
    policy: Mapping[str, Any],
) -> tuple[datetime, datetime]:
    pin = policy["policy_pins"]["source_provider_observation"]
    if (
        type(receipt.get("schema_version")) is not int
        or receipt["schema_version"] != 1
        or receipt.get("policy_id") != pin["policy_id"]
        or receipt.get("policy_sha256") != pin["sha256"]
        or receipt.get("base_binding_sha256") != base_digest
        or receipt.get("pass") is not True
        or DIGEST_RE.fullmatch(receipt_digest) is None
        or any(
            receipt.get(field) != base.get(field)
            for field in policy["cross_bindings"]["provider_fields_equal_base"]
        )
    ):
        _fail("provider_observation.binding_mismatch")
    observed = _parse_utc(receipt.get("observed_at"), "provider_observation.invalid")
    expires = _parse_utc(receipt.get("expires_at"), "provider_observation.invalid")
    if (
        observed < base_observed
        or observed >= base_expires
        or expires <= observed
        or expires > base_expires
        or (expires - observed).total_seconds()
        > policy["time"]["maximum_upstream_receipt_lifetime_seconds"][
            "source_provider_observation"
        ]
    ):
        _fail("provider_observation.stale")
    return observed, expires


def _validate_attestation_receipt(
    receipt: Mapping[str, Any],
    *,
    receipt_digest: str,
    base: Mapping[str, Any],
    base_digest: str,
    base_observed: datetime,
    base_expires: datetime,
    policy: Mapping[str, Any],
) -> tuple[datetime, datetime]:
    pin = policy["policy_pins"]["github_attestation_authentication"]
    identities = policy["cross_bindings"]["github_attestation_identity_equal_base"]
    if (
        type(receipt.get("schema_version")) is not int
        or receipt["schema_version"] != 1
        or receipt.get("policy_id") != pin["policy_id"]
        or receipt.get("policy_sha256") != pin["sha256"]
        or receipt.get("base_binding_sha256") != base_digest
        or receipt.get("subject_sha256") != base["manifest_file_sha256"]
        or receipt.get("pass") is not True
        or DIGEST_RE.fullmatch(receipt_digest) is None
        or any(
            receipt.get(field) != base.get(field)
            for field in policy["cross_bindings"][
                "github_attestation_fields_equal_base"
            ]
        )
        or any(
            receipt.get(receipt_field) != base.get(base_field)
            for receipt_field, base_field in identities.items()
        )
    ):
        _fail("github_attestation.binding_mismatch")
    verified = _parse_utc(receipt.get("verified_at"), "github_attestation.invalid")
    expires = _parse_utc(receipt.get("expires_at"), "github_attestation.invalid")
    if (
        verified < base_observed
        or verified >= base_expires
        or expires <= verified
        or expires > base_expires
        or (expires - verified).total_seconds()
        > policy["time"]["maximum_upstream_receipt_lifetime_seconds"][
            "github_attestation_authentication"
        ]
    ):
        _fail("github_attestation.stale")
    return verified, expires


def _mint_binding(
    record: Mapping[str, Any],
) -> safe_submit.RestoreEvidenceBinding:
    if set(record) != set(OUTPUT_FIELDS):
        _fail("evidence_binding.invalid")
    values = dict(record)
    for field in ("approved_restore_time", "observed_at", "expires_at"):
        values[field] = _parse_utc(values[field], "evidence_binding.invalid")
    binding = object.__new__(safe_submit.RestoreEvidenceBinding)
    for name in OUTPUT_FIELDS:
        object.__setattr__(binding, name, values[name])
    object.__setattr__(
        binding, "_binding_seal", safe_submit._RESTORE_EVIDENCE_BINDING_SEAL
    )
    try:
        round_trip = safe_submit.SafeRestoreSubmitter._binding_record(binding)
    except Exception:
        raise CompositionFailure("evidence_binding.invalid") from None
    if round_trip != record:
        _fail("evidence_binding.invalid")
    return binding


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _compose_restore_evidence_binding_with_document(
    *,
    policy_bytes: bytes,
    policy_sha256: str,
    server_binding: server_auth.ServerAuthenticatedSafeSubmitBinding,
    server_authentication_receipt_bytes: bytes,
    provider_observation: provider_observation.VerifiedProviderObservation,
    github_attestation: github_attestation.VerifiedGitHubAttestation,
    clock: Callable[[], datetime] = _utc_now,
) -> tuple[dict[str, Any], safe_submit.RestoreEvidenceBinding]:
    policy = _parse_policy_document(policy_bytes, policy_sha256, executable=True)
    base, base_bytes, base_observed, base_expires = _validate_base(
        server_binding, policy
    )
    _server_receipt, authenticated = _validate_server_receipt(
        server_authentication_receipt_bytes,
        base=base,
        observed=base_observed,
        expires=base_expires,
        policy=policy,
    )
    provider, _provider_bytes, provider_digest = _sealed_provider_receipt(
        provider_observation
    )
    attestation, _attestation_bytes, attestation_digest = _sealed_attestation_receipt(
        github_attestation
    )
    base_digest = _digest(base_bytes)
    provider_observed, provider_expires = _validate_provider_receipt(
        provider,
        receipt_digest=provider_digest,
        base=base,
        base_digest=base_digest,
        base_observed=base_observed,
        base_expires=base_expires,
        policy=policy,
    )
    attestation_verified, attestation_expires = _validate_attestation_receipt(
        attestation,
        receipt_digest=attestation_digest,
        base=base,
        base_digest=base_digest,
        base_observed=base_observed,
        base_expires=base_expires,
        policy=policy,
    )
    final_expires = min(base_expires, provider_expires, attestation_expires)
    now = _trusted_now(clock)
    if (
        now < max(base_observed, authenticated, provider_observed, attestation_verified)
        or now >= final_expires
    ):
        _fail("evidence_binding.stale")
    record = dict(base)
    record["expires_at"] = final_expires.strftime("%Y-%m-%dT%H:%M:%SZ")
    record["server_scope_authentication_receipt_sha256"] = _digest(
        server_authentication_receipt_bytes
    )
    record["provider_receipt_sha256"] = provider_digest
    record["attestation_receipt_sha256"] = attestation_digest
    binding = _mint_binding(record)
    result = {
        "schema_version": 1,
        "policy_id": policy["policy_id"],
        "evidence_binding_sha256": _digest(_canonical(record)),
        "activation_state": "blocked",
        "activation_blockers": list(policy["output_activation_blockers"]),
    }
    return result, binding


def compose_restore_evidence_binding() -> NoReturn:
    """Refuse until the exact policy and one trusted coordinator are active."""

    policy, _digest_value, _raw = load_composition_policy_document()
    validate_composition_policy(policy, executable=True)
    _fail("composition_inputs.unimplemented")


if __name__ == "__main__":
    compose_restore_evidence_binding()
