#!/usr/bin/env python3
"""Dormant same-process coordinator for a trusted staging restore submission.

The checked-in policy is deliberately disabled.  This module owns orchestration
only: concrete provider, verifier, marker-store, and ARM adapters remain a
separate reviewed activation slice.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
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
    import staging_restore_safe_submit as safe_submit
    import validate_staging_evidence_manifest as evidence_manifest
    import validate_staging_restore_launch_binding as launch_binding
    import validate_staging_restore_runtime_binding as runtime_binding
    import verify_staging_source_provider_observation as provider_observation


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = (
    ROOT / "contracts/staging-rehearsal/v1/restore-trusted-executor-policy.json"
)
POLICY_SHA256 = "ca2996d18593df0ba9c036de163f843a1955b4ba7b434a3984d879d9a2ea021b"
POLICY_TEMPLATE_SHA256 = (
    "a3233a06ad6e44444c9c470fb7e10284da7e9bd6b217e492028145b4aee2e766"
)
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


@dataclass(frozen=True, slots=True)
class ExecutorLaunchMaterial:
    """Exact launch inputs; this carrier grants no authority without sealed proofs."""

    authenticated_receipt: launch_binding.AuthenticatedBrokerLaunchReceipt
    launch_record_bytes: bytes
    runtime_record_bytes: bytes
    runtime_signature_verification: (
        runtime_binding.VerifiedExternalRuntimeRecordSignature
    )


@dataclass(frozen=True, slots=True)
class ExecutorDependencies:
    receive_launch: Callable[..., Any]
    runtime_reference_from_receipt: Callable[..., Any]
    validate_launch_record: Callable[..., Any]
    validate_runtime_binding: Callable[..., Any]
    validate_launch_receipt: Callable[..., Any]
    validate_snapshot: Callable[..., Any]
    authenticate_server: Callable[..., Any]
    observe_provider: Callable[..., Any]
    authenticate_attestation: Callable[..., Any]
    compose_binding: Callable[..., Any]
    request_from_binding: Callable[..., Any]
    submit: Callable[..., Any]


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


def _validate_directory(path: Path) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        _fail("input.untrusted_path")
    try:
        if path.resolve(strict=True) != path:
            _fail("input.untrusted_path")
        before = os.lstat(path)
        flags = (
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(path, flags)
        try:
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        raise TrustedExecutorFailure("input.untrusted_path") from None
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISDIR(before.st_mode)
        or not stat.S_ISDIR(after.st_mode)
        or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
        or after.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(after.st_mode) & 0o022
    ):
        _fail("input.untrusted_path")
    return path


def _read_input_file(
    path: Path,
    *,
    minimum: int,
    maximum: int,
    exact_mode: int | None,
    owner_only: bool,
) -> bytes:
    if not isinstance(path, Path) or not path.is_absolute():
        _fail("input.untrusted_path")
    descriptor = -1
    try:
        if path.resolve(strict=True) != path:
            _fail("input.untrusted_path")
        before = os.lstat(path)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        mode = stat.S_IMODE(opened.st_mode)
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_uid
            not in ({os.geteuid()} if owner_only else {0, os.geteuid()})
            or (exact_mode is not None and mode != exact_mode)
            or (exact_mode is None and mode & 0o022)
        ):
            _fail("input.untrusted_path")
        if opened.st_size < minimum or opened.st_size > maximum:
            _fail("input.invalid")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        final = os.fstat(descriptor)
        if (
            not minimum <= len(raw) <= maximum
            or len(raw) != opened.st_size
            or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
            != (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns)
        ):
            _fail("input.invalid")
        return raw
    except TrustedExecutorFailure:
        raise
    except OSError:
        raise TrustedExecutorFailure("input.untrusted_path") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _stage(call: Callable[..., Any], code: str, **kwargs: Any) -> Any:
    try:
        return call(**kwargs)
    except TrustedExecutorFailure as exc:
        if str(exc) in TRUSTED_CLOCK_FAILURES:
            raise
        raise TrustedExecutorFailure(code) from None
    except Exception:
        raise TrustedExecutorFailure(code) from None


def _validate_provision_launch(
    *,
    executor_policy: Mapping[str, Any],
    runtime_policy: Any,
    dependencies: ExecutorDependencies,
    clock: Callable[[], datetime],
) -> launch_binding.ValidatedLaunchRecord:
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
    if type(material) is not ExecutorLaunchMaterial:
        _fail("launch_receipt.result_invalid")
    authenticated_receipt = material.authenticated_receipt
    signature = material.runtime_signature_verification
    if (
        type(authenticated_receipt)
        is not launch_binding.AuthenticatedBrokerLaunchReceipt
        or not authenticated_receipt._is_authenticated()
        or type(signature) is not runtime_binding.VerifiedExternalRuntimeRecordSignature
        or not signature._is_verified()
        or type(material.launch_record_bytes) is not bytes
        or type(material.runtime_record_bytes) is not bytes
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
        record_bytes=material.launch_record_bytes,
        record_sha256=authenticated_receipt.launch_record_sha256,
        record_uri=authenticated_receipt.launch_record_uri,
    )
    if (
        type(launch_record) is not launch_binding.ValidatedLaunchRecord
        or not launch_record._is_validated()
        or launch_record.record_bytes != material.launch_record_bytes
        or launch_record.action != "provision"
    ):
        _fail("launch_record.result_invalid")
    runtime_record = _stage(
        dependencies.validate_runtime_binding,
        "runtime_record.failed",
        policy=runtime_document,
        record_bytes=material.runtime_record_bytes,
        provision_reference=references["provision"],
        cleanup_reference=references["cleanup"],
        signature_verification=signature,
    )
    if (
        type(runtime_record) is not runtime_binding.ValidatedExternalRuntimeBinding
        or not runtime_record._is_validated()
        or runtime_record.record_bytes != material.runtime_record_bytes
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
    return launch_record


def _canonical_safe_submit_config(
    executor_policy: Mapping[str, Any],
) -> safe_submit.SafeSubmitConfig:
    try:
        envelope, safe_policy = safe_submit.validate_canonical_contracts()
        config = safe_submit.config_from_policies(
            envelope, safe_policy, executable=False
        )
    except Exception:
        _fail("safe_submit_config.invalid")
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


def _execute_trusted_restore_with_dependencies(
    *,
    policy_bytes: bytes,
    policy_sha256: str,
    runtime_policy: Any,
    bundle_dir: Path,
    hmac_key_path: Path,
    attestation_bundle_path: Path,
    trusted_root_path: Path,
    dependencies: ExecutorDependencies,
    clock: Callable[[], datetime] = _utc_now,
) -> safe_submit.SubmitOutcome:
    policy = _parse_policy_document(policy_bytes, policy_sha256, executable=True)
    safe_submit_config = _canonical_safe_submit_config(policy)
    if type(dependencies) is not ExecutorDependencies:
        _fail("dependencies.invalid")
    if any(not callable(getattr(dependencies, name)) for name in DEPENDENCY_NAMES):
        _fail("dependencies.invalid")
    trusted_clock = _NonRegressingClock(clock)
    try:
        launch_record = _validate_provision_launch(
            executor_policy=policy,
            runtime_policy=runtime_policy,
            dependencies=dependencies,
            clock=trusted_clock,
        )
    except TrustedExecutorFailure:
        raise
    except Exception:
        _fail("launch_binding.result_invalid")
    inputs = policy["inputs"]
    ttl_hours = launch_record.ttl_hours
    if type(ttl_hours) is not int:
        _fail("launch_binding.result_invalid")
    bundle = _validate_directory(bundle_dir)
    hmac_policy = inputs["hmac_key"]
    key = _read_input_file(
        hmac_key_path,
        minimum=hmac_policy["minimum_bytes"],
        maximum=hmac_policy["maximum_bytes"],
        exact_mode=0o600,
        owner_only=True,
    )
    attestation_policy = inputs["attestation_bundle"]
    attestation_bytes = _read_input_file(
        attestation_bundle_path,
        minimum=attestation_policy["minimum_bytes"],
        maximum=attestation_policy["maximum_bytes"],
        exact_mode=None,
        owner_only=False,
    )
    root_policy = inputs["trusted_root"]
    trusted_root_bytes = _read_input_file(
        trusted_root_path,
        minimum=root_policy["minimum_bytes"],
        maximum=root_policy["maximum_bytes"],
        exact_mode=None,
        owner_only=False,
    )
    if (
        f"sha256:{hashlib.sha256(key).hexdigest()}"
        != launch_record.server_scope_hmac_key_sha256
        or f"sha256:{hashlib.sha256(attestation_bytes).hexdigest()}"
        != launch_record.attestation_bundle_sha256
        or f"sha256:{hashlib.sha256(trusted_root_bytes).hexdigest()}"
        != launch_record.trusted_root_sha256
    ):
        _fail("input.launch_binding")
    validation_now = trusted_clock()
    snapshot = _stage(
        dependencies.validate_snapshot,
        "manifest.failed",
        bundle_dir=bundle,
        validation_now=validation_now,
        expected_file_sha256s=dict(launch_record.evidence_file_sha256s),
    )
    if (
        type(snapshot) is not evidence_manifest.ValidatedEvidenceBundle
        or not snapshot._has_validated_manifest_snapshot()
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


def _runtime_dependencies() -> ExecutorDependencies:
    _fail("runtime_adapter.unimplemented")


def execute_trusted_restore(
    *,
    bundle_dir: Path,
    hmac_key_path: Path,
    attestation_bundle_path: Path,
    trusted_root_path: Path,
) -> safe_submit.SubmitOutcome:
    """Execute only after every exact policy and runtime adapter is activated."""

    policy, policy_sha256, policy_bytes = load_executor_policy_document()
    validate_executor_policy(policy, executable=True)
    runtime_policy = runtime_binding.load_policy(executable=True)
    dependencies = _runtime_dependencies()
    return _execute_trusted_restore_with_dependencies(
        policy_bytes=policy_bytes,
        policy_sha256=policy_sha256,
        runtime_policy=runtime_policy,
        bundle_dir=bundle_dir,
        hmac_key_path=hmac_key_path,
        attestation_bundle_path=attestation_bundle_path,
        trusted_root_path=trusted_root_path,
        dependencies=dependencies,
    )
