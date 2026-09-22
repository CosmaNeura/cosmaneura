"""Dormant receipt-to-executor handoff for one provision launch.

The checked-in runtime remains disabled.  This module joins the reviewed
mailbox receiver to the trusted executor's private launch-material seam while
leaving exact record ingress and external-runtime signature authentication as
separately reviewed injected dependencies.
"""

from __future__ import annotations

import hashlib
import os
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime
from typing import Any, NoReturn

try:
    from scripts import receive_staging_broker_launch_receipt as receipt_receiver
    from scripts import validate_staging_restore_launch_binding as launch_binding
    from scripts import validate_staging_restore_runtime_binding as runtime_binding
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    import receive_staging_broker_launch_receipt as receipt_receiver
    import validate_staging_restore_launch_binding as launch_binding
    import validate_staging_restore_runtime_binding as runtime_binding


PROVISION_ACTION = "provision"
MAXIMUM_RUNTIME_RECORD_BYTES = 65_536
MAXIMUM_TRUSTED_ROOT_BYTES = 25_600


class LaunchMaterialFailure(RuntimeError):
    """Bounded refusal safe to report without receipt or record contents."""


def _fail(code: str) -> NoReturn:
    raise LaunchMaterialFailure(code)


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _execution_identity_matches(
    receipt: launch_binding.AuthenticatedBrokerLaunchReceipt,
) -> bool:
    return os.environ.get(launch_binding.EXECUTION_NAME_ENV) == receipt.execution_name


def _stage(call: Callable[..., Any], code: str, **kwargs: Any) -> Any:
    try:
        return call(**kwargs)
    except Exception:  # noqa: BLE001 - erase dependency-controlled error text.
        raise LaunchMaterialFailure(code) from None


_AUTHENTICATED_RUNTIME_MATERIAL_SEAL = object()


def _runtime_signature_proof_is_exact(
    proof: Any,
) -> bool:
    try:
        return (
            type(proof) is runtime_binding.VerifiedExternalRuntimeRecordSignature
            and proof._is_verified()
            and all(
                type(value) is str
                and runtime_binding.DIGEST_RE.fullmatch(value) is not None
                for value in (
                    proof.record_sha256,
                    proof.bundle_sha256,
                    proof.trusted_root_sha256,
                )
            )
            and type(proof.certificate_oidc_issuer) is str
            and proof.certificate_oidc_issuer
            == "https://token.actions.githubusercontent.com"
            and type(proof.certificate_identity) is str
            and bool(proof.certificate_identity)
        )
    except (AttributeError, TypeError):
        return False


def _runtime_signature_proof_sha256(proof: Any) -> str:
    return _digest(
        runtime_binding._canonical(
            {
                name: getattr(proof, name)
                for name in (
                    "record_sha256",
                    "bundle_sha256",
                    "trusted_root_sha256",
                    "certificate_oidc_issuer",
                    "certificate_identity",
                )
            }
        )
    )


@dataclass(frozen=True, slots=True, init=False)
class AuthenticatedRuntimeRecordMaterial:
    """Opaque exact runtime-record/root bytes and their authenticated proof."""

    record_bytes: bytes = dataclass_field(repr=False)
    trusted_root_bytes: bytes = dataclass_field(repr=False)
    signature_verification: runtime_binding.VerifiedExternalRuntimeRecordSignature
    _signature_proof_sha256: str = dataclass_field(repr=False, compare=False)
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("launch_material.runtime_record_result_invalid")

    @classmethod
    def _from_authenticated_read(
        cls,
        *,
        record_bytes: bytes,
        trusted_root_bytes: bytes,
        signature_verification: runtime_binding.VerifiedExternalRuntimeRecordSignature,
    ) -> AuthenticatedRuntimeRecordMaterial:
        if (
            cls is not AuthenticatedRuntimeRecordMaterial
            or type(record_bytes) is not bytes
            or not 1 <= len(record_bytes) <= MAXIMUM_RUNTIME_RECORD_BYTES
            or type(trusted_root_bytes) is not bytes
            or not 1 <= len(trusted_root_bytes) <= MAXIMUM_TRUSTED_ROOT_BYTES
            or not _runtime_signature_proof_is_exact(signature_verification)
            or _digest(record_bytes) != signature_verification.record_sha256
            or _digest(trusted_root_bytes) != signature_verification.trusted_root_sha256
        ):
            _fail("launch_material.runtime_record_result_invalid")
        value = object.__new__(cls)
        object.__setattr__(value, "record_bytes", bytes(record_bytes))
        object.__setattr__(value, "trusted_root_bytes", bytes(trusted_root_bytes))
        object.__setattr__(value, "signature_verification", signature_verification)
        object.__setattr__(
            value,
            "_signature_proof_sha256",
            _runtime_signature_proof_sha256(signature_verification),
        )
        object.__setattr__(value, "_seal", _AUTHENTICATED_RUNTIME_MATERIAL_SEAL)
        return value

    def _is_authenticated(self) -> bool:
        try:
            proof = self.signature_verification
            return (
                type(self) is AuthenticatedRuntimeRecordMaterial
                and self._seal is _AUTHENTICATED_RUNTIME_MATERIAL_SEAL
                and type(self.record_bytes) is bytes
                and 1 <= len(self.record_bytes) <= MAXIMUM_RUNTIME_RECORD_BYTES
                and type(self.trusted_root_bytes) is bytes
                and 1 <= len(self.trusted_root_bytes) <= MAXIMUM_TRUSTED_ROOT_BYTES
                and _runtime_signature_proof_is_exact(proof)
                and type(self._signature_proof_sha256) is str
                and runtime_binding.DIGEST_RE.fullmatch(self._signature_proof_sha256)
                is not None
                and self._signature_proof_sha256
                == _runtime_signature_proof_sha256(proof)
                and _digest(self.record_bytes) == proof.record_sha256
                and _digest(self.trusted_root_bytes) == proof.trusted_root_sha256
            )
        except (AttributeError, TypeError):
            return False


@dataclass(frozen=True, slots=True)
class ProvisionLaunchMaterial:
    """Exact launch inputs; authority remains in the enclosed sealed proofs."""

    authenticated_receipt: launch_binding.AuthenticatedBrokerLaunchReceipt
    launch_record_bytes: bytes
    authenticated_runtime_record: AuthenticatedRuntimeRecordMaterial

    @property
    def runtime_record_bytes(self) -> bytes:
        return self.authenticated_runtime_record.record_bytes

    @property
    def trusted_root_bytes(self) -> bytes:
        return self.authenticated_runtime_record.trusted_root_bytes

    @property
    def runtime_signature_verification(
        self,
    ) -> runtime_binding.VerifiedExternalRuntimeRecordSignature:
        return self.authenticated_runtime_record.signature_verification


@dataclass(frozen=True, slots=True)
class LaunchMaterialDependencies:
    """Future exact-read/authentication capabilities, without caller selectors."""

    receipt_transport: receipt_receiver.LaunchReceiptMailboxTransport
    read_launch_record: Callable[..., Any]
    read_and_authenticate_runtime_record: Callable[..., Any]


def _validate_runtime_material(
    result: Any,
    *,
    document: dict[str, Any],
    reference: runtime_binding.ExternalRuntimeBindingReference,
) -> AuthenticatedRuntimeRecordMaterial:
    if type(result) is not AuthenticatedRuntimeRecordMaterial:
        _fail("launch_material.runtime_record_result_invalid")
    try:
        record_bytes = result.record_bytes
        trusted_root_bytes = result.trusted_root_bytes
        proof = result.signature_verification
    except AttributeError:
        _fail("launch_material.runtime_record_result_invalid")
    record_policy = document["external_runtime_record"]["record"]
    root_policy = document["evidence_ingress"]["private_versioned_secret_source"][
        "secrets"
    ]["github_trusted_root"]
    if (
        type(record_bytes) is not bytes
        or not 1 <= len(record_bytes) <= record_policy["maximum_bytes"]
        or type(trusted_root_bytes) is not bytes
        or not root_policy["minimum_bytes"]
        <= len(trusted_root_bytes)
        <= root_policy["maximum_bytes"]
    ):
        _fail("launch_material.runtime_record_result_invalid")
    signature_policy = document["external_runtime_record"]["signature"]
    if (
        not result._is_authenticated()
        or _digest(record_bytes) != reference.record_sha256
        or _digest(trusted_root_bytes) != root_policy["sha256"]
        or type(proof) is not runtime_binding.VerifiedExternalRuntimeRecordSignature
        or not proof._is_verified()
        or proof.record_sha256 != reference.record_sha256
        or proof.bundle_sha256 != reference.signature_bundle_sha256
        or proof.trusted_root_sha256 != root_policy["sha256"]
        or proof.certificate_oidc_issuer != signature_policy["certificate_oidc_issuer"]
        or proof.certificate_identity != signature_policy["certificate_identity"]
    ):
        _fail("launch_material.runtime_record_result_invalid")
    return result


def _validate_runtime_reference_ingress(
    reference: runtime_binding.ExternalRuntimeBindingReference,
    *,
    document: dict[str, Any],
) -> None:
    ingress = document["runtime_record_ingress"]
    expected_host = ingress["account_host"]
    container = ingress["container_name"]
    for uri_name, digest_name, suffix in (
        ("record_uri", "record_sha256", ".json"),
        ("signature_bundle_uri", "signature_bundle_sha256", ".sigstore.json"),
    ):
        value = getattr(reference, uri_name)
        digest = getattr(reference, digest_name)
        try:
            parsed = urllib.parse.urlsplit(value)
            port = parsed.port
        except (TypeError, ValueError):
            _fail("launch_material.runtime_reference_binding_invalid")
        expected_path = f"/{container}/{digest[7:]}{suffix}"
        expected_uri = f"https://{expected_host}{expected_path}"
        if (
            value != expected_uri
            or parsed.scheme != "https"
            or parsed.hostname != expected_host
            or parsed.netloc != expected_host
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
            or parsed.path != expected_path
            or parsed.query
            or parsed.fragment
        ):
            _fail("launch_material.runtime_reference_binding_invalid")


def receive_provision_launch_material(
    runtime_policy: Any,
    *,
    dependencies: LaunchMaterialDependencies,
    clock: Callable[[], datetime],
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> ProvisionLaunchMaterial:
    """Receive one provision receipt and assemble its exact executor material.

    The two injected sources receive sealed objects, not caller-selected paths,
    URIs, digests, or actions.  Concrete private-Blob, Key Vault, and external
    Sigstore adapters remain intentionally absent from the executable path.
    """

    if (
        type(dependencies) is not LaunchMaterialDependencies
        or not callable(
            getattr(dependencies.receipt_transport, "get_exact_private_blob", None)
        )
        or not callable(dependencies.read_launch_record)
        or not callable(dependencies.read_and_authenticate_runtime_record)
        or not callable(clock)
        or not callable(monotonic)
        or not callable(sleeper)
    ):
        _fail("launch_material.dependencies_invalid")
    try:
        document = runtime_binding.validate_runtime_binding_policy(
            runtime_policy, executable=True
        )
    except Exception:  # noqa: BLE001 - erase validator-controlled error text.
        raise LaunchMaterialFailure("launch_material.policy_invalid") from None

    authenticated_receipt = _stage(
        receipt_receiver.receive_broker_launch_receipt,
        "launch_material.receipt_failed",
        policy=document,
        action=PROVISION_ACTION,
        transport=dependencies.receipt_transport,
        monotonic=monotonic,
        sleeper=sleeper,
        wall_clock=clock,
    )
    if (
        type(authenticated_receipt)
        is not launch_binding.AuthenticatedBrokerLaunchReceipt
        or not authenticated_receipt._is_authenticated()
        or authenticated_receipt.action != PROVISION_ACTION
        or type(authenticated_receipt.receipt_bytes) is not bytes
        or _digest(authenticated_receipt.receipt_bytes)
        != authenticated_receipt.receipt_sha256
    ):
        _fail("launch_material.receipt_result_invalid")

    references: dict[str, runtime_binding.ExternalRuntimeBindingReference] = {}
    for job_kind in launch_binding.JOB_KINDS:
        reference = _stage(
            runtime_binding.ExternalRuntimeBindingReference._from_authenticated_broker_launch_receipt,
            "launch_material.runtime_reference_failed",
            policy=document,
            receipt=authenticated_receipt,
            source_job_kind=job_kind,
        )
        if (
            type(reference) is not runtime_binding.ExternalRuntimeBindingReference
            or not reference._is_bound()
            or reference.source_job_kind != job_kind
        ):
            _fail("launch_material.runtime_reference_result_invalid")
        references[job_kind] = reference
    if any(
        getattr(references[PROVISION_ACTION], field)
        != getattr(references["cleanup"], field)
        for field in runtime_binding.RUNTIME_REFERENCE_FIELDS
    ):
        _fail("launch_material.runtime_reference_result_invalid")
    for reference in references.values():
        _validate_runtime_reference_ingress(reference, document=document)

    launch_record_bytes = _stage(
        dependencies.read_launch_record,
        "launch_material.launch_record_failed",
        policy=document,
        authenticated_receipt=authenticated_receipt,
    )
    if not _execution_identity_matches(authenticated_receipt):
        _fail("launch_material.execution_identity_invalid")
    launch_maximum = document["launch_binding"]["launch_record"]["maximum_bytes"]
    if (
        type(launch_record_bytes) is not bytes
        or not 1 <= len(launch_record_bytes) <= launch_maximum
        or _digest(launch_record_bytes) != authenticated_receipt.launch_record_sha256
    ):
        _fail("launch_material.launch_record_result_invalid")

    runtime_material = _stage(
        dependencies.read_and_authenticate_runtime_record,
        "launch_material.runtime_record_failed",
        policy=document,
        authenticated_receipt=authenticated_receipt,
        provision_reference=references[PROVISION_ACTION],
        cleanup_reference=references["cleanup"],
        clock=clock,
    )
    runtime_material = _validate_runtime_material(
        runtime_material,
        document=document,
        reference=references[PROVISION_ACTION],
    )
    if not _execution_identity_matches(authenticated_receipt):
        _fail("launch_material.execution_identity_invalid")
    return ProvisionLaunchMaterial(
        authenticated_receipt=authenticated_receipt,
        launch_record_bytes=bytes(launch_record_bytes),
        authenticated_runtime_record=runtime_material,
    )
