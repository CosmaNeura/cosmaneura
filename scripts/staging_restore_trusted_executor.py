#!/usr/bin/env python3
"""Dormant same-process coordinator for a trusted staging restore submission.

The checked-in policy is deliberately disabled.  This module owns orchestration
only: concrete provider, verifier, marker-store, and ARM adapters remain a
separate reviewed activation slice.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from dataclasses import fields as dataclass_fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

try:
    from scripts import authenticate_staging_github_attestation as github_attestation
    from scripts import authenticate_staging_server_scope_receipt as server_auth
    from scripts import compose_staging_restore_evidence_binding as composition
    from scripts import read_staging_restore_evidence_material as evidence_ingress
    from scripts import read_staging_restore_launch_record as launch_record_ingress
    from scripts import read_staging_runtime_record_material as runtime_ingress
    from scripts import receive_staging_broker_launch_receipt as receipt_receiver
    from scripts import staging_restore_launch_material as launch_material
    from scripts import staging_restore_runtime_adapters as runtime_adapters
    from scripts import staging_restore_safe_submit as safe_submit
    from scripts import validate_staging_evidence_manifest as evidence_manifest
    from scripts import validate_staging_restore_launch_binding as launch_binding
    from scripts import validate_staging_restore_runtime_binding as runtime_binding
    from scripts import (
        verify_staging_source_provider_observation as provider_observation,
    )
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    import authenticate_staging_github_attestation as github_attestation
    import authenticate_staging_server_scope_receipt as server_auth
    import compose_staging_restore_evidence_binding as composition
    import read_staging_restore_evidence_material as evidence_ingress
    import read_staging_restore_launch_record as launch_record_ingress
    import read_staging_runtime_record_material as runtime_ingress
    import receive_staging_broker_launch_receipt as receipt_receiver
    import staging_restore_launch_material as launch_material
    import staging_restore_runtime_adapters as runtime_adapters
    import staging_restore_safe_submit as safe_submit
    import validate_staging_evidence_manifest as evidence_manifest
    import validate_staging_restore_launch_binding as launch_binding
    import validate_staging_restore_runtime_binding as runtime_binding
    import verify_staging_source_provider_observation as provider_observation


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = (
    ROOT / "contracts/staging-rehearsal/v1/restore-trusted-executor-policy.json"
)
POLICY_SHA256 = "8c6b3f00e0cae1d357dc2e0f6140f902560e8c35fc6c8d12e02db8a923eed549"
POLICY_TEMPLATE_SHA256 = (
    "c37b0006aba0ea5945d951b79d35fb1b3b93b4342156d44718baf3f8dc346cda"
)
# The factory is complete but deliberately unreachable from the public entry
# point until the separately reviewed workflow/private-runner activation slice.
PUBLIC_ENTRYPOINT_INTEGRATED = False
MAXIMUM_POLICY_BYTES = 256 * 1024
HEX_RE = re.compile(r"^[0-9a-f]{64}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
RESTORE_REQUEST_FIELDS = (
    "rehearsal_id",
    "evidence_binding",
    "cleanup_not_after",
    "repository",
    "workflow_sha",
    "run_id",
    "run_attempt",
)
SUBMIT_OUTCOME_FIELDS = (
    "status",
    "target_resource_id",
    "intent_sha256",
    "target_observed",
    "cleanup_required",
)
OUTCOME_STATUS_FLAGS = {
    "ready": frozenset({(True, False)}),
    "cleanup_marker_unproven": frozenset({(False, True), (True, True)}),
    "cleanup_pending": frozenset({(True, True)}),
    "pending_late_materialization": frozenset({(False, True)}),
}
TRUSTED_CLOCK_FAILURES = frozenset(
    {"trusted_clock.unavailable", "trusted_clock.invalid", "trusted_clock.regressed"}
)

DEPENDENCY_NAMES = (
    "receive_launch",
    "runtime_reference_from_receipt",
    "validate_launch_record",
    "validate_runtime_binding",
    "validate_launch_receipt",
    "read_private_inputs",
    "validate_snapshot",
    "authenticate_server",
    "observe_provider",
    "authenticate_attestation",
    "compose_binding",
    "request_from_binding",
    "submit",
)

ACTIVATION_FLAGS = (
    "trusted_executor_implemented",
    "runtime_adapter_implemented",
    "launch_binding_validator_implemented",
    "launch_mailbox_adapter_implemented",
    "launch_broker_adapter_implemented",
    "launch_receipt_validation_integrated",
    "source_provider_adapter_implemented",
    "offline_attestation_verifier_adapter_implemented",
    "safe_submit_adapter_implemented",
    "private_runner_binding_implemented",
    "workflow_integrated",
    "restore_activation_authorized",
)

ACTIVATION_BLOCKERS = (
    "restore_trusted_executor_policy_disabled",
    "upstream_policies_disabled",
    "trusted_executor_unimplemented",
    "runtime_adapter_unimplemented",
    "launch_binding_validator_unimplemented",
    "launch_mailbox_adapter_unimplemented",
    "launch_broker_adapter_unimplemented",
    "launch_receipt_validation_unintegrated",
    "source_provider_adapter_unimplemented",
    "offline_attestation_verifier_adapter_unimplemented",
    "safe_submit_adapter_unimplemented",
    "private_runner_binding_unimplemented",
    "restore_runtime_binding_policy_disabled",
    "external_runtime_record_unconfigured",
    "attestation_verifier_and_root_pins_unconfigured",
    "restore_envelope_policy_unconfigured",
    "restore_safe_submit_policy_unconfigured",
    "workflow_integration_unimplemented",
    "restore_activation_unimplemented",
)


class TrustedExecutorFailure(RuntimeError):
    """Bounded refusal safe to report without evidence or provider contents."""


def _fail(code: str) -> NoReturn:
    raise TrustedExecutorFailure(code)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        raise TrustedExecutorFailure("executor_policy.invalid") from None


def _load_mapping(raw: bytes, code: str) -> Mapping[str, Any]:
    if type(raw) is not bytes or not 0 < len(raw) <= MAXIMUM_POLICY_BYTES:
        _fail(code)

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail(code)
            result[key] = value
        return result

    def reject_nonfinite(_value: str) -> NoReturn:
        _fail(code)

    try:
        value = json.loads(
            raw,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonfinite,
            parse_float=reject_nonfinite,
        )
    except TrustedExecutorFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise TrustedExecutorFailure(code) from None
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _expected_policy_pins() -> dict[str, dict[str, str]]:
    return {
        "evidence_manifest": {
            "path": "contracts/staging-rehearsal/v1/evidence-manifest-policy.json",
            "policy_id": "cosmaneura-staging-evidence-manifest-v2",
            "sha256": f"sha256:{evidence_manifest.POLICY_SHA256}",
        },
        "server_scope_evidence": {
            "path": "contracts/staging-rehearsal/v1/server-scope-evidence-policy.json",
            "policy_id": "cosmaneura-staging-server-scope-evidence-v1",
            "sha256": f"sha256:{server_auth.server_scope.CANONICAL_POLICY_SHA256}",
        },
        "server_scope_authentication": {
            "path": "contracts/staging-rehearsal/v1/server-scope-authentication-policy.json",
            "policy_id": "cosmaneura-staging-server-scope-authentication-v1",
            "sha256": f"sha256:{server_auth.POLICY_SHA256}",
        },
        "source_provider_observation": {
            "path": "contracts/staging-rehearsal/v1/source-provider-observation-policy.json",
            "policy_id": "cosmaneura-staging-source-provider-observation-v1",
            "sha256": f"sha256:{provider_observation.POLICY_SHA256}",
        },
        "github_attestation_authentication": {
            "path": "contracts/staging-rehearsal/v1/github-attestation-authentication-policy.json",
            "policy_id": "cosmaneura-staging-github-attestation-authentication-v1",
            "sha256": f"sha256:{github_attestation.POLICY_SHA256}",
        },
        "restore_evidence_composition": {
            "path": "contracts/staging-rehearsal/v1/restore-evidence-composition-policy.json",
            "policy_id": "cosmaneura-staging-restore-evidence-binding-v1",
            "sha256": f"sha256:{composition.POLICY_SHA256}",
        },
        "restore_envelope": {
            "path": "contracts/staging-rehearsal/v1/restore-envelope-policy.json",
            "policy_id": "cosmaneura-staging-restore-envelope-v1",
            "sha256": f"sha256:{safe_submit.ENVELOPE_POLICY_SHA256}",
        },
        "restore_safe_submit": {
            "path": "contracts/staging-rehearsal/v1/restore-safe-submit-policy.json",
            "policy_id": "cosmaneura-staging-restore-safe-submit-v4",
            "sha256": f"sha256:{safe_submit.SAFE_SUBMIT_POLICY_SHA256}",
        },
        "restore_runtime_binding": {
            "path": "contracts/staging-rehearsal/v1/restore-runtime-binding-policy.json",
            "policy_id": "cosmaneura-staging-restore-runtime-binding-v1",
            "sha256": f"sha256:{runtime_binding.CANONICAL_POLICY_SHA256}",
        },
    }


def validate_executor_policy(policy: Any, *, executable: bool) -> Mapping[str, Any]:
    if not isinstance(policy, Mapping):
        _fail("executor_policy.invalid")
    ready = policy.get("activation_ready")
    activation = policy.get("activation")
    if (
        type(ready) is not bool
        or not isinstance(activation, Mapping)
        or set(activation) != set(ACTIVATION_FLAGS)
        or any(type(value) is not bool for value in activation.values())
    ):
        _fail("executor_policy.invalid")
    adapters = policy.get("adapters")
    if not isinstance(adapters, Mapping) or any(
        not isinstance(adapters.get(name), Mapping)
        for name in (
            "runtime",
            "launch_binding",
            "source_provider",
            "offline_attestation_verifier",
            "safe_submit",
        )
    ):
        _fail("executor_policy.invalid")
    launch_adapter = adapters["launch_binding"]
    implementation_bindings = {
        "runtime_adapter_implemented": adapters["runtime"].get("implemented"),
        "launch_binding_validator_implemented": launch_adapter.get("implemented"),
        "launch_mailbox_adapter_implemented": launch_adapter.get(
            "mailbox_transport_implemented"
        ),
        "launch_broker_adapter_implemented": launch_adapter.get(
            "broker_adapter_implemented"
        ),
        "launch_receipt_validation_integrated": launch_adapter.get(
            "launch_receipt_integration_implemented"
        ),
        "source_provider_adapter_implemented": adapters["source_provider"].get(
            "implemented"
        ),
        "offline_attestation_verifier_adapter_implemented": adapters[
            "offline_attestation_verifier"
        ].get("implemented"),
        "safe_submit_adapter_implemented": adapters["safe_submit"].get("implemented"),
    }
    if any(
        type(value) is not bool or activation[name] is not value
        for name, value in implementation_bindings.items()
    ):
        _fail("executor_policy.invalid")
    normalized = dict(policy)
    normalized["activation_ready"] = False
    normalized["activation"] = dict.fromkeys(activation, False)
    normalized["adapters"] = {name: dict(value) for name, value in adapters.items()}
    normalized_launch = normalized["adapters"]["launch_binding"]
    for name in (
        "runtime",
        "source_provider",
        "offline_attestation_verifier",
        "safe_submit",
    ):
        normalized["adapters"][name]["implemented"] = False
    for name in (
        "implemented",
        "mailbox_transport_implemented",
        "broker_adapter_implemented",
        "launch_receipt_integration_implemented",
    ):
        normalized_launch[name] = False
    if hashlib.sha256(_canonical(normalized)).hexdigest() != POLICY_TEMPLATE_SHA256:
        _fail("executor_policy.invalid")
    output = policy.get("output")
    if (
        policy.get("policy_pins") != _expected_policy_pins()
        or tuple(policy.get("activation_blockers", ())) != ACTIVATION_BLOCKERS
        or not isinstance(output, Mapping)
        or tuple(output.get("fields", ())) != SUBMIT_OUTCOME_FIELDS
        or tuple(output.get("allowed_statuses", ())) != tuple(OUTCOME_STATUS_FLAGS)
        or tuple(field.name for field in dataclass_fields(safe_submit.RestoreRequest))
        != RESTORE_REQUEST_FIELDS
        or tuple(field.name for field in dataclass_fields(safe_submit.SubmitOutcome))
        != SUBMIT_OUTCOME_FIELDS
        or tuple(safe_submit.RestoreRequest.__slots__) != RESTORE_REQUEST_FIELDS
        or tuple(safe_submit.SubmitOutcome.__slots__) != SUBMIT_OUTCOME_FIELDS
    ):
        _fail("executor_policy.invalid")
    all_active = all(value is True for value in activation.values())
    all_dormant = all(value is False for value in activation.values())
    if (ready and not all_active) or (not ready and not all_dormant):
        _fail("executor_policy.invalid")
    if executable and not ready:
        _fail("executor_policy.disabled")
    return policy


def _parse_policy_document(
    raw: bytes, claimed_sha256: str, *, executable: bool
) -> Mapping[str, Any]:
    if (
        type(raw) is not bytes
        or not isinstance(claimed_sha256, str)
        or HEX_RE.fullmatch(claimed_sha256) is None
        or hashlib.sha256(raw).hexdigest() != claimed_sha256
    ):
        _fail("executor_policy.hash_mismatch")
    return validate_executor_policy(
        _load_mapping(raw, "executor_policy.invalid"), executable=executable
    )


def load_executor_policy_document(
    path: Path = POLICY_PATH,
) -> tuple[Mapping[str, Any], str, bytes]:
    try:
        if path.resolve(strict=True) != POLICY_PATH.resolve(strict=True):
            _fail("executor_policy.untrusted_path")
        raw = path.read_bytes()
    except TrustedExecutorFailure:
        raise
    except OSError:
        raise TrustedExecutorFailure("executor_policy.read_failed") from None
    digest = hashlib.sha256(raw).hexdigest()
    if digest != POLICY_SHA256:
        _fail("executor_policy.hash_mismatch")
    return (
        _parse_policy_document(raw, digest, executable=False),
        digest,
        raw,
    )


ExecutorLaunchMaterial = launch_material.ProvisionLaunchMaterial


@dataclass(frozen=True, slots=True)
class ExecutorDependencies:
    safe_submit_config: safe_submit.SafeSubmitConfig
    receive_launch: Callable[..., Any]
    runtime_reference_from_receipt: Callable[..., Any]
    validate_launch_record: Callable[..., Any]
    validate_runtime_binding: Callable[..., Any]
    validate_launch_receipt: Callable[..., Any]
    read_private_inputs: Callable[..., Any]
    validate_snapshot: Callable[..., Any]
    authenticate_server: Callable[..., Any]
    observe_provider: Callable[..., Any]
    authenticate_attestation: Callable[..., Any]
    compose_binding: Callable[..., Any]
    request_from_binding: Callable[..., Any]
    submit: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class _PinnedRuntimeDocuments:
    runtime_policy: Mapping[str, Any]
    manifest_policy: Mapping[str, Any]
    manifest_policy_sha256: str
    server_scope_policy_bytes: bytes
    server_scope_policy_sha256: str
    authentication_policy_bytes: bytes
    authentication_policy_sha256: str
    provider_policy: Mapping[str, Any]
    provider_policy_bytes: bytes
    provider_policy_sha256: str
    attestation_policy: Mapping[str, Any]
    attestation_policy_bytes: bytes
    attestation_policy_sha256: str
    composition_policy_bytes: bytes
    composition_policy_sha256: str
    safe_submit_config: safe_submit.SafeSubmitConfig


@dataclass(frozen=True, slots=True)
class _ProvisionAuthoritySnapshot:
    receipt_sha256: str
    launch_record_uri: str
    launch_record_sha256: str
    execution_binding: str
    evidence_file_sha256s: tuple[tuple[str, str], ...]
    attestation_bundle_sha256: str
    server_scope_hmac_key_sha256: str
    trusted_root_sha256: str


@dataclass(frozen=True, slots=True)
class _ValidatedProvisionLaunch:
    runtime_policy: Mapping[str, Any]
    authenticated_receipt: launch_binding.AuthenticatedBrokerLaunchReceipt
    launch_record: launch_binding.ValidatedLaunchRecord
    authenticated_runtime_record: launch_material.AuthenticatedRuntimeRecordMaterial
    authority: _ProvisionAuthoritySnapshot


class _NonRegressingClock:
    __slots__ = ("_last", "_source")

    def __init__(self, source: Callable[[], datetime]) -> None:
        if not callable(source):
            _fail("trusted_clock.invalid")
        self._source = source
        self._last: datetime | None = None

    def __call__(self) -> datetime:
        try:
            value = self._source()
        except TrustedExecutorFailure:
            raise
        except Exception:
            raise TrustedExecutorFailure("trusted_clock.unavailable") from None
        if not isinstance(value, datetime) or value.tzinfo is None:
            _fail("trusted_clock.invalid")
        value = value.astimezone(UTC).replace(microsecond=0)
        if self._last is not None and value < self._last:
            _fail("trusted_clock.regressed")
        self._last = value
        return value


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _stage(call: Callable[..., Any], code: str, **kwargs: Any) -> Any:
    try:
        return call(**kwargs)
    except TrustedExecutorFailure as exc:
        if str(exc) in TRUSTED_CLOCK_FAILURES:
            raise
        raise TrustedExecutorFailure(code) from None
    except Exception:
        raise TrustedExecutorFailure(code) from None


def _provision_authority_snapshot(
    *,
    runtime_policy: Mapping[str, Any],
    authenticated_receipt: Any,
    launch_record: Any,
    authenticated_runtime_record: Any,
) -> _ProvisionAuthoritySnapshot:
    try:
        evidence_file_sha256s = launch_record.evidence_file_sha256s
        attestation_bundle_sha256 = launch_record.attestation_bundle_sha256
        server_scope_hmac_key_sha256 = launch_record.server_scope_hmac_key_sha256
        execution_binding = (
            f"{runtime_policy['runtime']['jobs']['provision']['resource_id']}"
            f"/executions/{authenticated_receipt.execution_name}"
        )
        trusted_root_bytes = authenticated_runtime_record.trusted_root_bytes
        trusted_root_sha256 = launch_binding._digest(trusted_root_bytes)
        valid = (
            type(authenticated_receipt)
            is launch_binding.AuthenticatedBrokerLaunchReceipt
            and authenticated_receipt._is_authenticated()
            and type(authenticated_receipt.receipt_bytes) is bytes
            and authenticated_receipt.receipt_sha256
            == launch_binding._digest(authenticated_receipt.receipt_bytes)
            and authenticated_receipt.action == "provision"
            and type(launch_record) is launch_binding.ValidatedLaunchRecord
            and launch_record._is_validated()
            and launch_record.action == "provision"
            and type(launch_record.record_bytes) is bytes
            and launch_record.record_sha256
            == launch_binding._digest(launch_record.record_bytes)
            and authenticated_receipt.launch_record_uri == launch_record.record_uri
            and authenticated_receipt.launch_record_sha256
            == launch_record.record_sha256
            and type(authenticated_runtime_record)
            is launch_material.AuthenticatedRuntimeRecordMaterial
            and authenticated_runtime_record._is_authenticated()
            and launch_record.runtime_record_sha256
            == launch_binding._digest(authenticated_runtime_record.record_bytes)
            and type(evidence_file_sha256s) is tuple
            and len(evidence_file_sha256s) == 6
            and all(
                type(item) is tuple
                and len(item) == 2
                and type(item[0]) is str
                and type(item[1]) is str
                and DIGEST_RE.fullmatch(item[1]) is not None
                for item in evidence_file_sha256s
            )
            and tuple(name for name, _digest_value in evidence_file_sha256s)
            == tuple(
                runtime_policy["launch_binding"]["launch_record"]["provision"][
                    "exact_evidence_file_names"
                ]
            )
            and type(attestation_bundle_sha256) is str
            and DIGEST_RE.fullmatch(attestation_bundle_sha256) is not None
            and type(server_scope_hmac_key_sha256) is str
            and DIGEST_RE.fullmatch(server_scope_hmac_key_sha256) is not None
            and type(launch_record.trusted_root_sha256) is str
            and launch_record.trusted_root_sha256 == trusted_root_sha256
            and authenticated_runtime_record.signature_verification.trusted_root_sha256
            == trusted_root_sha256
        )
    except Exception:
        _fail("launch_authority.invalid")
    if not valid:
        _fail("launch_authority.invalid")
    return _ProvisionAuthoritySnapshot(
        receipt_sha256=authenticated_receipt.receipt_sha256,
        launch_record_uri=authenticated_receipt.launch_record_uri,
        launch_record_sha256=launch_record.record_sha256,
        execution_binding=execution_binding,
        evidence_file_sha256s=evidence_file_sha256s,
        attestation_bundle_sha256=attestation_bundle_sha256,
        server_scope_hmac_key_sha256=server_scope_hmac_key_sha256,
        trusted_root_sha256=trusted_root_sha256,
    )


def _validate_provision_launch(
    *,
    executor_policy: Mapping[str, Any],
    runtime_policy: Any,
    dependencies: ExecutorDependencies,
    clock: Callable[[], datetime],
) -> _ValidatedProvisionLaunch:
    try:
        runtime_document = runtime_binding.validate_runtime_binding_policy(
            runtime_policy, executable=True
        )
    except Exception:
        _fail("runtime_policy.invalid")
    material = _stage(
        dependencies.receive_launch,
        "launch_receipt.failed",
        runtime_policy=runtime_document,
        clock=clock,
    )
    if type(material) is not launch_material.ProvisionLaunchMaterial:
        _fail("launch_receipt.result_invalid")
    try:
        runtime_material = material.authenticated_runtime_record
        authenticated_receipt = material.authenticated_receipt
        launch_record_bytes = material.launch_record_bytes
        signature = material.runtime_signature_verification
        runtime_record_bytes = material.runtime_record_bytes
        trusted_root_bytes = material.trusted_root_bytes
    except (AttributeError, TypeError):
        _fail("launch_receipt.result_invalid")
    if (
        type(authenticated_receipt)
        is not launch_binding.AuthenticatedBrokerLaunchReceipt
        or not authenticated_receipt._is_authenticated()
        or type(runtime_material)
        is not launch_material.AuthenticatedRuntimeRecordMaterial
        or type(runtime_record_bytes) is not bytes
        or not 1
        <= len(runtime_record_bytes)
        <= launch_material.MAXIMUM_RUNTIME_RECORD_BYTES
        or type(trusted_root_bytes) is not bytes
        or not 1
        <= len(trusted_root_bytes)
        <= launch_material.MAXIMUM_TRUSTED_ROOT_BYTES
        or not runtime_material._is_authenticated()
        or type(signature) is not runtime_binding.VerifiedExternalRuntimeRecordSignature
        or not signature._is_verified()
        or type(launch_record_bytes) is not bytes
    ):
        _fail("launch_receipt.result_invalid")
    references: dict[str, runtime_binding.ExternalRuntimeBindingReference] = {}
    for job_kind in ("provision", "cleanup"):
        reference = _stage(
            dependencies.runtime_reference_from_receipt,
            "runtime_reference.failed",
            policy=runtime_document,
            receipt=authenticated_receipt,
            source_job_kind=job_kind,
        )
        if (
            type(reference) is not runtime_binding.ExternalRuntimeBindingReference
            or not reference._is_bound()
            or reference.source_job_kind != job_kind
        ):
            _fail("runtime_reference.result_invalid")
        references[job_kind] = reference
    launch_record = _stage(
        dependencies.validate_launch_record,
        "launch_record.failed",
        policy=runtime_document,
        record_bytes=launch_record_bytes,
        record_sha256=authenticated_receipt.launch_record_sha256,
        record_uri=authenticated_receipt.launch_record_uri,
    )
    if (
        type(launch_record) is not launch_binding.ValidatedLaunchRecord
        or not launch_record._is_validated()
        or launch_record.record_bytes != launch_record_bytes
        or launch_record.action != "provision"
        or launch_binding._digest(trusted_root_bytes)
        != launch_record.trusted_root_authority.material_sha256
    ):
        _fail("launch_record.result_invalid")
    runtime_record = _stage(
        dependencies.validate_runtime_binding,
        "runtime_record.failed",
        policy=runtime_document,
        record_bytes=runtime_record_bytes,
        provision_reference=references["provision"],
        cleanup_reference=references["cleanup"],
        signature_verification=signature,
    )
    if (
        type(runtime_record) is not runtime_binding.ValidatedExternalRuntimeBinding
        or not runtime_record._is_validated()
        or runtime_record.record_bytes != runtime_record_bytes
    ):
        _fail("runtime_record.result_invalid")
    validation_now = clock()
    received = _stage(
        dependencies.validate_launch_receipt,
        "launch_binding.failed",
        policy=runtime_document,
        authenticated_receipt=authenticated_receipt,
        launch_record=launch_record,
        runtime_record=runtime_record,
        validation_now=validation_now,
    )
    expected_names = tuple(
        runtime_document["launch_binding"]["launch_record"]["provision"][
            "exact_evidence_file_names"
        ]
    )
    executor_names = tuple(
        executor_policy["inputs"]["evidence_bundle"]["required_files"]
    )
    evidence_sha256s = launch_record.evidence_file_sha256s
    ttl = launch_record.ttl_hours
    ttl_policy = executor_policy["inputs"]["ttl_hours"]
    expected_execution_resource_id = (
        f"{runtime_document['runtime']['jobs']['provision']['resource_id']}"
        f"/executions/{authenticated_receipt.execution_name}"
    )
    if (
        type(received) is not launch_binding.ValidatedReceivedLaunchReceipt
        or not received._is_validated()
        or received.action != "provision"
        or received.receipt_sha256 != authenticated_receipt.receipt_sha256
        or received.launch_record_sha256 != launch_record.record_sha256
        or received.runtime_record_sha256 != runtime_record.record_sha256
        or received.execution_resource_id != expected_execution_resource_id
        or received.validated_at != validation_now
        or not (
            launch_record.created_at
            <= received.observed_at
            <= authenticated_receipt.observed_at
            <= validation_now
            < launch_record.expires_at
        )
        or expected_names != executor_names
        or tuple(name for name, _digest_value in evidence_sha256s) != expected_names
        or any(
            type(digest) is not str or DIGEST_RE.fullmatch(digest) is None
            for _name, digest in evidence_sha256s
        )
        or type(launch_record.rehearsal_id) is not str
        or type(launch_record.attestation_bundle_sha256) is not str
        or DIGEST_RE.fullmatch(launch_record.attestation_bundle_sha256) is None
        or type(launch_record.server_scope_hmac_key_sha256) is not str
        or DIGEST_RE.fullmatch(launch_record.server_scope_hmac_key_sha256) is None
        or type(launch_record.trusted_root_sha256) is not str
        or DIGEST_RE.fullmatch(launch_record.trusted_root_sha256) is None
        or type(ttl) is not int
        or ttl < ttl_policy["minimum"]
        or ttl > ttl_policy["maximum"]
    ):
        _fail("launch_binding.result_invalid")
    authority = _provision_authority_snapshot(
        runtime_policy=runtime_document,
        authenticated_receipt=authenticated_receipt,
        launch_record=launch_record,
        authenticated_runtime_record=runtime_material,
    )
    return _ValidatedProvisionLaunch(
        runtime_policy=runtime_document,
        authenticated_receipt=authenticated_receipt,
        launch_record=launch_record,
        authenticated_runtime_record=runtime_material,
        authority=authority,
    )


def _validate_private_inputs(
    result: Any,
    *,
    launch: _ValidatedProvisionLaunch,
) -> evidence_ingress.PrivateProvisionInputs:
    try:
        authority = _provision_authority_snapshot(
            runtime_policy=launch.runtime_policy,
            authenticated_receipt=launch.authenticated_receipt,
            launch_record=launch.launch_record,
            authenticated_runtime_record=launch.authenticated_runtime_record,
        )
        valid = (
            authority == launch.authority
            and type(result) is evidence_ingress.PrivateProvisionInputs
            and result._is_bound_to(
                launch.runtime_policy,
                launch.authenticated_receipt,
                launch.launch_record,
            )
            and result.receipt_sha256 == authority.receipt_sha256
            and result.launch_record_sha256 == authority.launch_record_sha256
            and result.execution_binding == authority.execution_binding
            and tuple(name for name, _raw in result.evidence_entries)
            == tuple(name for name, _digest_value in authority.evidence_file_sha256s)
            and tuple(
                launch_binding._digest(raw) for _name, raw in result.evidence_entries
            )
            == tuple(digest for _name, digest in authority.evidence_file_sha256s)
            and launch_binding._digest(result.attestation_bundle_bytes)
            == authority.attestation_bundle_sha256
            and launch_binding._digest(result.server_scope_hmac_key_bytes)
            == authority.server_scope_hmac_key_sha256
            and launch_binding._digest(
                launch.authenticated_runtime_record.trusted_root_bytes
            )
            == authority.trusted_root_sha256
        )
    except Exception:
        _fail("private_inputs.result_invalid")
    if not valid:
        _fail("private_inputs.result_invalid")
    return result


def _validate_safe_submit_config(
    config: Any,
    executor_policy: Mapping[str, Any],
) -> safe_submit.SafeSubmitConfig:
    if (
        type(config) is not safe_submit.SafeSubmitConfig
        or config.policy_id
        != executor_policy["policy_pins"]["restore_safe_submit"]["policy_id"]
        or config.maximum_ttl_hours != executor_policy["inputs"]["ttl_hours"]["maximum"]
    ):
        _fail("safe_submit_config.invalid")
    return config


def _validate_request(
    request: Any,
    *,
    binding: safe_submit.RestoreEvidenceBinding,
    ttl_hours: int,
    config: safe_submit.SafeSubmitConfig,
    launch_record: launch_binding.ValidatedLaunchRecord,
) -> safe_submit.RestoreRequest:
    try:
        expected = safe_submit.request_from_binding(
            binding=binding, config=config, ttl_hours=ttl_hours
        )
        valid = (
            type(request) is safe_submit.RestoreRequest
            and type(request.rehearsal_id) is str
            and type(request.cleanup_not_after) is datetime
            and type(request.repository) is str
            and type(request.workflow_sha) is str
            and type(request.run_id) is str
            and type(request.run_attempt) is str
            and request.evidence_binding is binding
            and request.rehearsal_id == expected.rehearsal_id
            and request.cleanup_not_after == expected.cleanup_not_after
            and request.repository == expected.repository
            and request.workflow_sha == expected.workflow_sha
            and request.run_id == expected.run_id
            and request.run_attempt == expected.run_attempt
            and type(launch_record) is launch_binding.ValidatedLaunchRecord
            and launch_record._is_validated()
            and launch_record.action == "provision"
            and request.rehearsal_id == launch_record.rehearsal_id
            and request.workflow_sha == launch_record.workflow_sha
            and request.run_id == launch_record.run_id
            and request.run_attempt == launch_record.run_attempt
        )
    except Exception:
        _fail("request.result_invalid")
    if not valid:
        _fail("request.result_invalid")
    return expected


def _expected_outcome_identity(
    *, config: safe_submit.SafeSubmitConfig, request: safe_submit.RestoreRequest
) -> tuple[str, str]:
    try:
        submitter = object.__new__(safe_submit.SafeRestoreSubmitter)
        submitter.config = config
        intent = submitter._build_intent(request, require_fresh=False)
        target_resource_id = intent["target_server_resource_id"]
        intent_sha256 = (
            f"sha256:{hashlib.sha256(safe_submit._canonical(intent)).hexdigest()}"
        )
    except Exception:
        _fail("request.result_invalid")
    if (
        type(target_resource_id) is not str
        or DIGEST_RE.fullmatch(intent_sha256) is None
    ):
        _fail("request.result_invalid")
    return target_resource_id, intent_sha256


def _validate_outcome(
    outcome: Any,
    *,
    expected_target_resource_id: str,
    expected_intent_sha256: str,
    raw_values: tuple[bytes, ...],
) -> safe_submit.SubmitOutcome:
    try:
        valid = (
            type(outcome) is safe_submit.SubmitOutcome
            and type(outcome.status) is str
            and outcome.status in OUTCOME_STATUS_FLAGS
            and type(outcome.target_observed) is bool
            and type(outcome.cleanup_required) is bool
            and (outcome.target_observed, outcome.cleanup_required)
            in OUTCOME_STATUS_FLAGS.get(outcome.status, ())
            and type(outcome.target_resource_id) is str
            and outcome.target_resource_id == expected_target_resource_id
            and type(outcome.intent_sha256) is str
            and outcome.intent_sha256 == expected_intent_sha256
        )
    except Exception:
        _fail("submit.result_invalid")
    if not valid:
        _fail("submit.result_invalid")
    try:
        rendered_fields = (
            outcome.status.encode("utf-8"),
            outcome.target_resource_id.encode("utf-8"),
            outcome.intent_sha256.encode("utf-8"),
        )
    except UnicodeEncodeError:
        _fail("submit.result_invalid")
    if any(
        raw
        and any(
            rendered == raw or (len(raw) >= 8 and raw in rendered)
            for rendered in rendered_fields
        )
        for raw in raw_values
    ):
        _fail("submit.result_invalid")
    return safe_submit.SubmitOutcome(
        status=outcome.status,
        target_resource_id=outcome.target_resource_id,
        intent_sha256=outcome.intent_sha256,
        target_observed=outcome.target_observed,
        cleanup_required=outcome.cleanup_required,
    )


def _require_policy_pin(
    executor_policy: Mapping[str, Any],
    name: str,
    policy: Mapping[str, Any],
    digest: str,
) -> None:
    try:
        pin = executor_policy["policy_pins"][name]
        valid = (
            type(digest) is str
            and HEX_RE.fullmatch(digest) is not None
            and policy.get("policy_id") == pin["policy_id"]
            and f"sha256:{digest}" == pin["sha256"]
        )
    except Exception:
        _fail("dependencies.policy_invalid")
    if not valid:
        _fail("dependencies.policy_invalid")


def _load_pinned_runtime_documents(
    *,
    executor_policy: Mapping[str, Any],
    runtime_policy: Any,
) -> _PinnedRuntimeDocuments:
    """Load every child policy and validate every pin before adapter creation."""

    try:
        runtime_document = runtime_binding.validate_runtime_binding_policy(
            runtime_policy, executable=False
        )
        manifest_policy, manifest_sha256 = evidence_manifest.load_policy(
            evidence_manifest.POLICY_PATH
        )
        server_scope_policy, server_scope_sha256, server_scope_bytes = (
            server_auth.server_scope.read_policy_document()
        )
        authentication_policy, authentication_sha256, authentication_bytes = (
            server_auth.load_authentication_policy_document()
        )
        provider_policy, provider_sha256, provider_bytes = (
            provider_observation.load_provider_observation_policy_document()
        )
        attestation_policy, attestation_sha256, attestation_bytes = (
            github_attestation.load_attestation_policy_document()
        )
        composition_policy, composition_sha256, composition_bytes = (
            composition.load_composition_policy_document()
        )
        envelope_policy, safe_submit_policy = safe_submit.validate_canonical_contracts()
    except Exception:
        raise TrustedExecutorFailure("dependencies.policy_invalid") from None

    _require_policy_pin(
        executor_policy,
        "restore_runtime_binding",
        runtime_document,
        runtime_binding.CANONICAL_POLICY_SHA256,
    )
    for name, policy, digest in (
        ("evidence_manifest", manifest_policy, manifest_sha256),
        ("server_scope_evidence", server_scope_policy, server_scope_sha256),
        (
            "server_scope_authentication",
            authentication_policy,
            authentication_sha256,
        ),
        ("source_provider_observation", provider_policy, provider_sha256),
        (
            "github_attestation_authentication",
            attestation_policy,
            attestation_sha256,
        ),
        ("restore_evidence_composition", composition_policy, composition_sha256),
        (
            "restore_envelope",
            envelope_policy,
            safe_submit.ENVELOPE_POLICY_SHA256,
        ),
        (
            "restore_safe_submit",
            safe_submit_policy,
            safe_submit.SAFE_SUBMIT_POLICY_SHA256,
        ),
    ):
        _require_policy_pin(executor_policy, name, policy, digest)

    try:
        runtime_document = runtime_binding.validate_runtime_binding_policy(
            runtime_document, executable=True
        )
        server_auth.validate_authentication_policy(
            authentication_policy,
            executable=True,
            server_scope_policy_sha256=server_scope_sha256,
        )
        provider_observation.validate_provider_observation_policy(
            provider_policy, executable=True
        )
        github_attestation.validate_attestation_policy(
            attestation_policy, executable=True
        )
        composition.validate_composition_policy(composition_policy, executable=True)
        safe_submit_config = safe_submit.config_from_policies(
            envelope_policy, safe_submit_policy, executable=True
        )
    except TrustedExecutorFailure:
        raise
    except Exception:
        raise TrustedExecutorFailure("dependencies.policy_invalid") from None

    return _PinnedRuntimeDocuments(
        runtime_policy=runtime_document,
        manifest_policy=manifest_policy,
        manifest_policy_sha256=manifest_sha256,
        server_scope_policy_bytes=server_scope_bytes,
        server_scope_policy_sha256=server_scope_sha256,
        authentication_policy_bytes=authentication_bytes,
        authentication_policy_sha256=authentication_sha256,
        provider_policy=provider_policy,
        provider_policy_bytes=provider_bytes,
        provider_policy_sha256=provider_sha256,
        attestation_policy=attestation_policy,
        attestation_policy_bytes=attestation_bytes,
        attestation_policy_sha256=attestation_sha256,
        composition_policy_bytes=composition_bytes,
        composition_policy_sha256=composition_sha256,
        safe_submit_config=safe_submit_config,
    )


def _assemble_provision_dependencies(
    *,
    documents: _PinnedRuntimeDocuments,
    clock: Callable[[], datetime],
) -> ExecutorDependencies:
    """Construct one instance of every provision dependency after validation."""

    if (
        type(documents) is not _PinnedRuntimeDocuments
        or type(clock) is not _NonRegressingClock
    ):
        _fail("dependencies.invalid")
    try:
        runtime_policy = documents.runtime_policy
        receipt_transport = (
            receipt_receiver.ManagedIdentityPrivateBlobTransport.from_environment(
                runtime_policy, action="provision"
            )
        )
        launch_record_reader = launch_record_ingress.build_private_launch_record_reader(
            runtime_policy
        )
        runtime_material_reader = runtime_ingress.build_private_runtime_material_reader(
            runtime_policy
        )
        private_evidence_reader = (
            evidence_ingress.build_private_evidence_material_reader(
                runtime_policy, clock=clock
            )
        )
        provision_identity = runtime_policy["identities"]["jobs"]["provision"]
        token_source = runtime_adapters.ManagedIdentityTokenSource.from_environment(
            client_id=provision_identity["client_id"]
        )
        provider = runtime_adapters.AzureSourceObservationProvider(token_source)
        verifier_policy = documents.attestation_policy["verifier"]
        runtime_verifier_policy = runtime_policy["offline_github_verifier"]
        if (
            verifier_policy["version"] != runtime_verifier_policy["version"]
            or verifier_policy["executable_sha256"]
            != runtime_verifier_policy["executable_sha256"]
        ):
            _fail("dependencies.policy_invalid")
        evidence_verifier = runtime_adapters.DigestPinnedGitHubCliVerifier(
            executable_path=Path(runtime_verifier_policy["executable_path"]),
            expected_sha256=verifier_policy["executable_sha256"],
            expected_version=verifier_policy["version"],
            profile=runtime_adapters.EVIDENCE_ATTESTATION_PROFILE,
        )
        submit_runtime = safe_submit.build_policy_bound_runtime(
            config=documents.safe_submit_config,
            runtime_policy=runtime_policy,
            action="provision",
            trusted_clock=clock,
        )
        submitter = safe_submit.SafeRestoreSubmitter(
            submit_runtime.arm_transport,
            submit_runtime.marker_store,
            documents.safe_submit_config,
            clock=clock,
        )
        launch_dependencies = launch_material.LaunchMaterialDependencies(
            receipt_transport=receipt_transport,
            read_launch_record=launch_record_reader.read_launch_record,
            read_and_authenticate_runtime_record=(
                runtime_material_reader.read_and_authenticate_runtime_record
            ),
        )
    except TrustedExecutorFailure:
        raise
    except Exception:
        raise TrustedExecutorFailure("dependencies.construction_failed") from None

    def receive_launch(*, runtime_policy: Any, clock: Callable[[], datetime]) -> Any:
        return launch_material.receive_provision_launch_material(
            runtime_policy,
            dependencies=launch_dependencies,
            clock=clock,
        )

    def validate_snapshot(
        *,
        files: Mapping[str, bytes],
        validation_now: datetime,
        expected_file_sha256s: Mapping[str, str],
    ) -> Any:
        return evidence_manifest.validate_bundle_snapshot_bytes(
            policy=documents.manifest_policy,
            policy_sha256=documents.manifest_policy_sha256,
            files=files,
            now=validation_now,
            expected_file_sha256s=expected_file_sha256s,
        )

    def authenticate_server(**kwargs: Any) -> Any:
        return server_auth._authenticate_validated_bundle_with_binding_and_receipt(
            authentication_policy_bytes=documents.authentication_policy_bytes,
            authentication_policy_sha256=documents.authentication_policy_sha256,
            server_scope_policy_bytes=documents.server_scope_policy_bytes,
            server_scope_policy_sha256=documents.server_scope_policy_sha256,
            **kwargs,
        )

    def observe_provider(**kwargs: Any) -> Any:
        return provider_observation._verify_provider_observation_with_document(
            policy_bytes=documents.provider_policy_bytes,
            policy_sha256=documents.provider_policy_sha256,
            provider=provider,
            **kwargs,
        )

    def authenticate_attestation(**kwargs: Any) -> Any:
        return github_attestation._authenticate_github_attestation_with_document(
            policy_bytes=documents.attestation_policy_bytes,
            policy_sha256=documents.attestation_policy_sha256,
            verifier=evidence_verifier,
            **kwargs,
        )

    def compose_binding(**kwargs: Any) -> Any:
        return composition._compose_restore_evidence_binding_with_document(
            policy_bytes=documents.composition_policy_bytes,
            policy_sha256=documents.composition_policy_sha256,
            **kwargs,
        )

    def request_from_binding(**kwargs: Any) -> Any:
        return safe_submit.request_from_binding(
            config=documents.safe_submit_config, **kwargs
        )

    return ExecutorDependencies(
        safe_submit_config=documents.safe_submit_config,
        receive_launch=receive_launch,
        runtime_reference_from_receipt=(
            runtime_binding.ExternalRuntimeBindingReference._from_authenticated_broker_launch_receipt
        ),
        validate_launch_record=launch_binding.validate_launch_record,
        validate_runtime_binding=runtime_binding.validate_external_runtime_binding,
        validate_launch_receipt=launch_binding.validate_received_launch_receipt,
        read_private_inputs=private_evidence_reader.read_provision_inputs,
        validate_snapshot=validate_snapshot,
        authenticate_server=authenticate_server,
        observe_provider=observe_provider,
        authenticate_attestation=authenticate_attestation,
        compose_binding=compose_binding,
        request_from_binding=request_from_binding,
        submit=submitter.submit,
    )


def _runtime_dependencies(
    *,
    executor_policy: Mapping[str, Any],
    runtime_policy: Any,
    clock: Callable[[], datetime],
) -> ExecutorDependencies:
    documents = _load_pinned_runtime_documents(
        executor_policy=executor_policy,
        runtime_policy=runtime_policy,
    )
    return _assemble_provision_dependencies(documents=documents, clock=clock)


def _execute_trusted_restore_with_dependencies(
    *,
    policy_bytes: bytes,
    policy_sha256: str,
    runtime_policy: Any,
    dependencies: ExecutorDependencies,
    clock: Callable[[], datetime] = _utc_now,
) -> safe_submit.SubmitOutcome:
    policy = _parse_policy_document(policy_bytes, policy_sha256, executable=True)
    if type(dependencies) is not ExecutorDependencies:
        _fail("dependencies.invalid")
    if any(not callable(getattr(dependencies, name)) for name in DEPENDENCY_NAMES):
        _fail("dependencies.invalid")
    safe_submit_config = _validate_safe_submit_config(
        dependencies.safe_submit_config, policy
    )
    trusted_clock = (
        clock if type(clock) is _NonRegressingClock else _NonRegressingClock(clock)
    )
    try:
        launch = _validate_provision_launch(
            executor_policy=policy,
            runtime_policy=runtime_policy,
            dependencies=dependencies,
            clock=trusted_clock,
        )
    except TrustedExecutorFailure:
        raise
    except Exception:
        _fail("launch_binding.result_invalid")
    launch_record = launch.launch_record
    ttl_hours = launch_record.ttl_hours
    if type(ttl_hours) is not int:
        _fail("launch_binding.result_invalid")
    private_inputs = _stage(
        dependencies.read_private_inputs,
        "private_inputs.failed",
        policy=launch.runtime_policy,
        authenticated_receipt=launch.authenticated_receipt,
        launch_record=launch_record,
    )
    private_inputs = _validate_private_inputs(private_inputs, launch=launch)
    evidence_files = private_inputs.evidence_files()
    evidence_entries = private_inputs.evidence_entries
    if (
        type(evidence_files) is not dict
        or tuple(evidence_files.items()) != evidence_entries
    ):
        _fail("private_inputs.result_invalid")
    key = private_inputs.server_scope_hmac_key_bytes
    attestation_bytes = private_inputs.attestation_bundle_bytes
    trusted_root_bytes = launch.authenticated_runtime_record.trusted_root_bytes
    if (
        launch_binding._digest(key) != launch.authority.server_scope_hmac_key_sha256
        or launch_binding._digest(attestation_bytes)
        != launch.authority.attestation_bundle_sha256
        or launch_binding._digest(trusted_root_bytes)
        != launch.authority.trusted_root_sha256
    ):
        _fail("private_inputs.result_invalid")
    validation_now = trusted_clock()
    expected_file_sha256s = dict(launch.authority.evidence_file_sha256s)
    snapshot = _stage(
        dependencies.validate_snapshot,
        "manifest.failed",
        files=evidence_files,
        validation_now=validation_now,
        expected_file_sha256s=expected_file_sha256s,
    )
    if tuple(
        evidence_files.items()
    ) != evidence_entries or expected_file_sha256s != dict(
        launch.authority.evidence_file_sha256s
    ):
        _fail("manifest.result_invalid")
    _validate_private_inputs(private_inputs, launch=launch)
    if (
        type(snapshot) is not evidence_manifest.ValidatedEvidenceBundle
        or not snapshot._has_launch_digest_binding(
            launch.authority.evidence_file_sha256s
        )
    ):
        _fail("manifest.result_invalid")
    authentication = _stage(
        dependencies.authenticate_server,
        "server_authentication.failed",
        snapshot=snapshot,
        key=key,
        clock=trusted_clock,
    )
    if not isinstance(authentication, tuple) or len(authentication) != 3:
        _fail("server_authentication.result_invalid")
    _authentication_result, server_binding, server_receipt_bytes = authentication
    if (
        type(server_binding) is not server_auth.ServerAuthenticatedSafeSubmitBinding
        or not server_binding._is_authenticator_minted()
        or type(server_receipt_bytes) is not bytes
        or not server_receipt_bytes
    ):
        _fail("server_authentication.result_invalid")
    provider_result = _stage(
        dependencies.observe_provider,
        "provider_observation.failed",
        binding=server_binding,
        clock=trusted_clock,
    )
    if not isinstance(provider_result, tuple) or len(provider_result) != 2:
        _fail("provider_observation.result_invalid")
    _provider_result, provider_proof = provider_result
    if (
        type(provider_proof) is not provider_observation.VerifiedProviderObservation
        or not provider_proof._is_observer_minted()
    ):
        _fail("provider_observation.result_invalid")
    attestation_result = _stage(
        dependencies.authenticate_attestation,
        "github_attestation.failed",
        binding=server_binding,
        snapshot=snapshot,
        attestation_bundle_bytes=attestation_bytes,
        trusted_root_bytes=trusted_root_bytes,
        clock=trusted_clock,
    )
    if not isinstance(attestation_result, tuple) or len(attestation_result) != 2:
        _fail("github_attestation.result_invalid")
    _attestation_result, attestation_proof = attestation_result
    if (
        type(attestation_proof) is not github_attestation.VerifiedGitHubAttestation
        or not attestation_proof._is_authenticator_minted()
    ):
        _fail("github_attestation.result_invalid")
    composition_result = _stage(
        dependencies.compose_binding,
        "composition.failed",
        server_binding=server_binding,
        server_authentication_receipt_bytes=server_receipt_bytes,
        provider_observation=provider_proof,
        github_attestation=attestation_proof,
        clock=trusted_clock,
    )
    if not isinstance(composition_result, tuple) or len(composition_result) != 2:
        _fail("composition.result_invalid")
    _composition_result, binding = composition_result
    if (
        type(binding) is not safe_submit.RestoreEvidenceBinding
        or not binding._is_module_minted_binding()
    ):
        _fail("composition.result_invalid")
    request = _stage(
        dependencies.request_from_binding,
        "request.result_invalid",
        binding=binding,
        ttl_hours=ttl_hours,
    )
    request = _validate_request(
        request,
        binding=binding,
        ttl_hours=ttl_hours,
        config=safe_submit_config,
        launch_record=launch_record,
    )
    expected_target_resource_id, expected_intent_sha256 = _expected_outcome_identity(
        config=safe_submit_config, request=request
    )
    _validate_private_inputs(private_inputs, launch=launch)
    submit_now = trusted_clock()
    if not launch_record.created_at <= submit_now < launch_record.expires_at:
        _fail("launch_binding.expired")
    outcome = _stage(
        dependencies.submit,
        "submit.failed",
        request=request,
        clock=trusted_clock,
    )
    _validate_request(
        request,
        binding=binding,
        ttl_hours=ttl_hours,
        config=safe_submit_config,
        launch_record=launch_record,
    )
    _validate_private_inputs(private_inputs, launch=launch)
    return _validate_outcome(
        outcome,
        expected_target_resource_id=expected_target_resource_id,
        expected_intent_sha256=expected_intent_sha256,
        raw_values=(
            key,
            attestation_bytes,
            trusted_root_bytes,
            snapshot.result_bytes,
            snapshot.server_scope_bytes,
            snapshot.manifest_bytes,
            server_binding.record_bytes,
            server_receipt_bytes,
            provider_proof.receipt_bytes,
            attestation_proof.receipt_bytes,
        ),
    )


def execute_trusted_restore() -> safe_submit.SubmitOutcome:
    """Execute only after every exact policy and runtime adapter is activated."""

    if not PUBLIC_ENTRYPOINT_INTEGRATED:
        _fail("executor_policy.disabled")
    policy, policy_sha256, policy_bytes = load_executor_policy_document()
    validate_executor_policy(policy, executable=True)
    runtime_policy = runtime_binding.load_policy(executable=True)
    trusted_clock = _NonRegressingClock(_utc_now)
    dependencies = _runtime_dependencies(
        executor_policy=policy,
        runtime_policy=runtime_policy,
        clock=trusted_clock,
    )
    return _execute_trusted_restore_with_dependencies(
        policy_bytes=policy_bytes,
        policy_sha256=policy_sha256,
        runtime_policy=runtime_policy,
        dependencies=dependencies,
        clock=trusted_clock,
    )
