#!/usr/bin/env python3
"""Dormant one-shot private reader for one authenticated launch record.

The reader derives the only permitted Blob URI from a sealed provision
receipt.  It is intentionally not wired into the trusted executor.
"""

from __future__ import annotations

import hashlib
import os
import threading
import urllib.parse
from collections.abc import Mapping
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


class PrivateLaunchRecordFailure(RuntimeError):
    """Bounded refusal without credentials, receipt bytes, or response bytes."""


def _fail(code: str) -> NoReturn:
    raise PrivateLaunchRecordFailure(code)


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


@dataclass(frozen=True, slots=True)
class _AuthoritySnapshot:
    receipt_bytes: bytes
    receipt_sha256: str
    action: str
    execution_name: str
    launch_record_uri: str
    launch_record_sha256: str
    runtime_reference: tuple[tuple[str, str], ...]
    receipt_uri: str
    signature_envelope_uri: str
    signature_envelope_sha256: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class _RequestSnapshot:
    action: str
    object_kind: str
    uri: str
    method: str
    oauth_resource: str
    account_resource_id: str
    container_resource_id: str
    managed_identity_resource_id: str
    managed_identity_client_id: str
    private_endpoint_resource_id: str
    service_api_version: str
    accept: str
    optional_charset: str
    accept_encoding: str
    content_length_required: bool
    content_range_permitted: bool
    transfer_encoding_permitted: bool
    redirects_permitted: bool
    maximum_bytes: int
    attempt_number: int
    timeout_seconds: int


def _request_snapshot(request: Any) -> _RequestSnapshot:
    try:
        if (
            type(request) is not receipt_receiver.AuthenticatedMailboxGetRequest
            or not request._is_sealed()
        ):
            _fail("launch_record_ingress.request_invalid")
        return _RequestSnapshot(
            **{
                name: getattr(request, name)
                for name in _RequestSnapshot.__dataclass_fields__
            }
        )
    except PrivateLaunchRecordFailure:
        raise
    except Exception:
        raise PrivateLaunchRecordFailure(
            "launch_record_ingress.request_invalid"
        ) from None


def _expected_request_snapshot(
    document: Mapping[str, Any], authority: _AuthoritySnapshot
) -> _RequestSnapshot:
    mailbox = document["launch_binding"]["mailbox"]
    retrieval = mailbox["launch_record_retrieval"]
    identity = document["identities"]["jobs"][PROVISION_ACTION]
    return _RequestSnapshot(
        action=PROVISION_ACTION,
        object_kind="launch_record",
        uri=authority.launch_record_uri,
        method=retrieval["method"],
        oauth_resource=mailbox["oauth_resource"],
        account_resource_id=mailbox["account_resource_id"],
        container_resource_id=mailbox["container_resource_ids"][PROVISION_ACTION],
        managed_identity_resource_id=identity["resource_id"],
        managed_identity_client_id=identity["client_id"],
        private_endpoint_resource_id=mailbox["private_endpoint_resource_id"],
        service_api_version=retrieval["service_api_version"],
        accept=retrieval["successful_media_type"],
        optional_charset=retrieval["optional_charset"],
        accept_encoding=retrieval["content_encoding"],
        content_length_required=retrieval["content_length_required"],
        content_range_permitted=retrieval["content_range_permitted"],
        transfer_encoding_permitted=retrieval["transfer_encoding_permitted"],
        redirects_permitted=retrieval["redirects_permitted"],
        maximum_bytes=document["launch_binding"]["launch_record"]["maximum_bytes"],
        attempt_number=1,
        timeout_seconds=retrieval["maximum_request_timeout_seconds"],
    )


def _authority_snapshot(
    document: Mapping[str, Any], receipt: Any
) -> _AuthoritySnapshot:
    try:
        if (
            type(receipt) is not launch_binding.AuthenticatedBrokerLaunchReceipt
            or not receipt._is_authenticated()
            or type(receipt.receipt_bytes) is not bytes
            or _digest(receipt.receipt_bytes) != receipt.receipt_sha256
            or type(receipt.execution_name) is not str
            or launch_binding.EXECUTION_NAME_RE.fullmatch(receipt.execution_name)
            is None
            or receipt.action != PROVISION_ACTION
        ):
            _fail("launch_record_ingress.receipt_invalid")
        contract = document["launch_binding"]["launch_receipt"]
        signed = launch_binding._parse(
            receipt.receipt_bytes,
            maximum=contract["maximum_bytes"],
            code="launch_record_ingress.receipt_invalid",
        )
        launch_binding._mapping(
            signed,
            set(contract["required_fields"]),
            "launch_record_ingress.receipt_invalid",
        )
        digest = signed["launch_record_sha256"]
        if (
            type(digest) is not str
            or launch_binding.DIGEST_RE.fullmatch(digest) is None
        ):
            _fail("launch_record_ingress.receipt_invalid")
        mailbox = document["launch_binding"]["mailbox"]
        object_path = mailbox["launch_record_path_template"].format(
            launch_record_sha256_without_prefix=digest[7:]
        )
        expected_uri = (
            f"https://{mailbox['account_host']}/"
            f"{mailbox['container_names'][PROVISION_ACTION]}/{object_path}"
        )
        expected_receipt_uri, expected_signature_uri = (
            launch_binding._execution_receipt_uris(
                dict(document), PROVISION_ACTION, receipt.execution_name
            )
        )
        expected_job_id = document["runtime"]["jobs"][PROVISION_ACTION]["resource_id"]
        runtime_reference = tuple(
            (field, signed[f"runtime_{field}"])
            for field in runtime_binding.RUNTIME_REFERENCE_FIELDS
        )
        if (
            receipt.receipt_bytes != launch_binding._canonical(signed)
            or type(signed["schema_version"]) is not int
            or signed["schema_version"] != 1
            or signed["action"] != PROVISION_ACTION
            or signed["selected_job_resource_id"] != expected_job_id
            or signed["execution_resource_id"]
            != f"{expected_job_id}/executions/{receipt.execution_name}"
            or signed["launch_record_uri"] != expected_uri
            or receipt.launch_record_uri != expected_uri
            or receipt.launch_record_sha256 != digest
            or receipt.receipt_uri != expected_receipt_uri
            or receipt.signature_envelope_uri != expected_signature_uri
            or not isinstance(receipt.runtime_reference, Mapping)
            or set(receipt.runtime_reference)
            != set(runtime_binding.RUNTIME_REFERENCE_FIELDS)
            or any(
                receipt.runtime_reference[field] != value
                for field, value in runtime_reference
            )
        ):
            _fail("launch_record_ingress.receipt_invalid")
        parsed = urllib.parse.urlsplit(expected_uri)
        if (
            parsed.scheme != "https"
            or parsed.hostname != mailbox["account_host"]
            or parsed.netloc != mailbox["account_host"]
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.path
            != f"/{mailbox['container_names'][PROVISION_ACTION]}/{object_path}"
            or parsed.query
            or parsed.fragment
        ):
            _fail("launch_record_ingress.receipt_invalid")
        runtime_binding._pin(
            receipt.signature_envelope_sha256,
            "digest",
            "launch_record_ingress.receipt_invalid",
        )
        for field, value in runtime_reference:
            runtime_binding._pin(
                value,
                "https_uri" if field.endswith("uri") else "digest",
                "launch_record_ingress.receipt_invalid",
            )
        instant = launch_binding._exact_time(
            receipt.observed_at, "launch_record_ingress.receipt_invalid"
        )
        return _AuthoritySnapshot(
            receipt_bytes=bytes(receipt.receipt_bytes),
            receipt_sha256=receipt.receipt_sha256,
            action=receipt.action,
            execution_name=receipt.execution_name,
            launch_record_uri=expected_uri,
            launch_record_sha256=digest,
            runtime_reference=runtime_reference,
            receipt_uri=receipt.receipt_uri,
            signature_envelope_uri=receipt.signature_envelope_uri,
            signature_envelope_sha256=receipt.signature_envelope_sha256,
            observed_at=instant,
        )
    except PrivateLaunchRecordFailure:
        raise
    except Exception:
        raise PrivateLaunchRecordFailure(
            "launch_record_ingress.receipt_invalid"
        ) from None


class ManagedIdentityPrivateLaunchRecordReader:
    """One-shot authenticated-receipt-derived private Blob reader."""

    __slots__ = ("_lock", "_transport", "_used")

    def __init__(
        self, transport: receipt_receiver.LaunchReceiptMailboxTransport
    ) -> None:
        if not callable(getattr(transport, "get_exact_private_blob", None)):
            _fail("launch_record_ingress.configuration_invalid")
        self._transport = transport
        self._lock = threading.Lock()
        self._used = False

    def _claim_use(self) -> None:
        with self._lock:
            if self._used:
                _fail("launch_record_ingress.already_used")
            self._used = True

    @staticmethod
    def _checkpoint(
        document: Mapping[str, Any],
        receipt: launch_binding.AuthenticatedBrokerLaunchReceipt,
        snapshot: _AuthoritySnapshot,
    ) -> None:
        if _authority_snapshot(document, receipt) != snapshot:
            _fail("launch_record_ingress.receipt_invalid")
        if os.environ.get(launch_binding.EXECUTION_NAME_ENV) != snapshot.execution_name:
            _fail("launch_record_ingress.execution_identity_invalid")

    def read_launch_record(
        self,
        *,
        policy: Any,
        authenticated_receipt: launch_binding.AuthenticatedBrokerLaunchReceipt,
    ) -> bytes:
        """Read the single exact provision launch object, once."""

        self._claim_use()
        try:
            validated = runtime_binding.validate_runtime_binding_policy(
                policy, executable=True
            )
            document = runtime_binding._parse_policy(
                runtime_binding._canonical(validated)
            )
            launch_binding.validate_launch_binding_policy(document)
        except Exception:
            raise PrivateLaunchRecordFailure(
                "launch_record_ingress.policy_invalid"
            ) from None
        snapshot = _authority_snapshot(document, authenticated_receipt)
        self._checkpoint(document, authenticated_receipt, snapshot)
        try:
            request = receipt_receiver._sealed_provision_launch_record_request(
                document=document,
                authenticated_receipt=authenticated_receipt,
                authority_receipt_sha256=snapshot.receipt_sha256,
                authority_execution_name=snapshot.execution_name,
                authority_launch_record_uri=snapshot.launch_record_uri,
                authority_launch_record_sha256=snapshot.launch_record_sha256,
            )
        except Exception:
            raise PrivateLaunchRecordFailure(
                "launch_record_ingress.request_invalid"
            ) from None
        expected_request = _expected_request_snapshot(document, snapshot)
        if _request_snapshot(request) != expected_request:
            _fail("launch_record_ingress.request_invalid")
        try:
            response = self._transport.get_exact_private_blob(request)
        except Exception:
            raise PrivateLaunchRecordFailure(
                "launch_record_ingress.transport_failed"
            ) from None
        self._checkpoint(document, authenticated_receipt, snapshot)
        retrieval = document["launch_binding"]["mailbox"]["launch_record_retrieval"]
        try:
            if (
                _request_snapshot(request) != expected_request
                or type(response)
                is not receipt_receiver.AuthenticatedMailboxGetResponse
                or not response._is_authenticated_for(request)
            ):
                _fail("launch_record_ingress.response_invalid")
            body = response.body
            if (
                response.status_code != retrieval["successful_status"]
                or type(body) is not bytes
                or not 1
                <= len(body)
                <= document["launch_binding"]["launch_record"]["maximum_bytes"]
                or _digest(body) != snapshot.launch_record_sha256
            ):
                _fail("launch_record_ingress.response_invalid")
        except PrivateLaunchRecordFailure:
            raise
        except Exception:
            raise PrivateLaunchRecordFailure(
                "launch_record_ingress.response_invalid"
            ) from None
        self._checkpoint(document, authenticated_receipt, snapshot)
        return bytes(body)


def build_private_launch_record_reader(
    policy: Any,
) -> ManagedIdentityPrivateLaunchRecordReader:
    """Build the dormant reader without wiring or executing it."""

    try:
        runtime_binding.validate_runtime_binding_policy(policy, executable=True)
        transport = receipt_receiver.ManagedIdentityPrivateBlobTransport.from_provision_launch_record_environment(
            policy
        )
        return ManagedIdentityPrivateLaunchRecordReader(transport)
    except Exception:
        raise PrivateLaunchRecordFailure(
            "launch_record_ingress.configuration_invalid"
        ) from None
