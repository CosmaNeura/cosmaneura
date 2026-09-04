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


@dataclass(frozen=True, slots=True)
class AuthenticatedRuntimeRecordMaterial:
    """Exact runtime-record bytes and their separately authenticated proof."""

    record_bytes: bytes
    signature_verification: runtime_binding.VerifiedExternalRuntimeRecordSignature


@dataclass(frozen=True, slots=True)
class ProvisionLaunchMaterial:
    """Exact launch inputs; authority remains in the enclosed sealed proofs."""

    authenticated_receipt: launch_binding.AuthenticatedBrokerLaunchReceipt
    launch_record_bytes: bytes
    runtime_record_bytes: bytes
    runtime_signature_verification: (
        runtime_binding.VerifiedExternalRuntimeRecordSignature
    )


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
    record_bytes = result.record_bytes
    proof = result.signature_verification
    record_policy = document["external_runtime_record"]["record"]
    signature_policy = document["external_runtime_record"]["signature"]
    root_policy = document["evidence_ingress"]["private_versioned_secret_source"][
        "secrets"
    ]["github_trusted_root"]
    if (
        type(record_bytes) is not bytes
        or not 1 <= len(record_bytes) <= record_policy["maximum_bytes"]
        or _digest(record_bytes) != reference.record_sha256
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
        runtime_record_bytes=bytes(runtime_material.record_bytes),
        runtime_signature_verification=runtime_material.signature_verification,
    )
