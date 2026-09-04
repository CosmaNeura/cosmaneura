#!/usr/bin/env python3
"""Dormant exact private ingress for one signed staging runtime record.

This module is intentionally not wired into the trusted executor.  It accepts
only sealed receipt-derived runtime references, reads their exact private Blob
objects and the policy-pinned Key Vault secret version, and then invokes the
reviewed offline runtime-record authenticator in the same process.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn, Protocol

try:
    from scripts import authenticate_staging_runtime_record_signature as authentication
    from scripts import staging_restore_launch_material as launch_material
    from scripts import staging_restore_runtime_adapters as runtime_adapters
    from scripts import validate_staging_restore_launch_binding as launch_binding
    from scripts import validate_staging_restore_runtime_binding as runtime_binding
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    import authenticate_staging_runtime_record_signature as authentication
    import staging_restore_launch_material as launch_material
    import staging_restore_runtime_adapters as runtime_adapters
    import validate_staging_restore_launch_binding as launch_binding
    import validate_staging_restore_runtime_binding as runtime_binding


KEY_VAULT_SECRET_NAME_RE = re.compile(r"^[A-Za-z0-9-]{1,127}$")
KEY_VAULT_SECRET_VERSION_RE = re.compile(r"^[0-9a-f]{32}$")


class PrivateRuntimeMaterialFailure(RuntimeError):
    """Bounded refusal without credentials, URIs, or remote response bytes."""


def _fail(code: str) -> NoReturn:
    raise PrivateRuntimeMaterialFailure(code)


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


class _HttpOpener(Protocol):
    def open(self, request: Any, *, timeout: int) -> Any: ...


class _OfflineVerifier(Protocol):
    def verify(
        self,
        *,
        arguments: tuple[str, ...],
        files: Mapping[str, bytes],
        timeout_seconds: int,
        maximum_output_bytes: int,
    ) -> Any: ...


def _header_values(headers: Any, name: str) -> tuple[str, ...]:
    try:
        if hasattr(headers, "get_all"):
            raw = headers.get_all(name) or []
        else:
            value = headers.get(name)
            raw = [] if value is None else [value]
        values = tuple(raw)
    except Exception:
        _fail("runtime_ingress.http_response_invalid")
    if any(
        type(value) is not str
        or not value
        or len(value) > 512
        or "\r" in value
        or "\n" in value
        for value in values
    ):
        _fail("runtime_ingress.http_response_invalid")
    return values


def _read_wire_response(
    response: Any, *, expected_uri: str, maximum_bytes: int
) -> tuple[int, tuple[str, ...], bytes]:
    try:
        status = getattr(response, "status", getattr(response, "code", None))
        final_uri = response.geturl()
        content_types = _header_values(response.headers, "Content-Type")
        encodings = _header_values(response.headers, "Content-Encoding")
        lengths = _header_values(response.headers, "Content-Length")
        ranges = _header_values(response.headers, "Content-Range")
        transfers = _header_values(response.headers, "Transfer-Encoding")
    except PrivateRuntimeMaterialFailure:
        raise
    except Exception:
        raise PrivateRuntimeMaterialFailure(
            "runtime_ingress.http_response_invalid"
        ) from None
    if (
        type(status) is not int
        or not 100 <= status <= 599
        or final_uri != expected_uri
        or len(content_types) > 1
        or len(encodings) > 1
        or len(lengths) != 1
        or ranges
        or transfers
        or (
            encodings
            and tuple(value.strip().lower() for value in encodings) != ("identity",)
        )
    ):
        _fail("runtime_ingress.http_response_invalid")
    value = lengths[0]
    if not value.isascii() or not value.isdigit() or value != str(int(value)):
        _fail("runtime_ingress.http_response_invalid")
    declared_length = int(value)
    if declared_length > maximum_bytes:
        _fail("runtime_ingress.http_response_too_large")
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            chunk = response.read(maximum_bytes + 1 - total)
            if type(chunk) is not bytes:
                _fail("runtime_ingress.http_response_invalid")
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum_bytes:
                _fail("runtime_ingress.http_response_too_large")
    except PrivateRuntimeMaterialFailure:
        raise
    except Exception:
        raise PrivateRuntimeMaterialFailure(
            "runtime_ingress.http_response_unavailable"
        ) from None
    body = b"".join(chunks)
    if declared_length != len(body):
        _fail("runtime_ingress.http_response_invalid")
    return status, content_types, body


def _media_type_matches(values: tuple[str, ...], expected: str) -> bool:
    if len(values) != 1:
        return False
    parts = tuple(part.strip().lower() for part in values[0].split(";"))
    media_type = expected.lower()
    return parts in {(media_type,), (media_type, "charset=utf-8")}


def _json_object(raw: bytes, code: str) -> Mapping[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
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
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_nonfinite,
        )
    except PrivateRuntimeMaterialFailure:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise PrivateRuntimeMaterialFailure(code) from None
    if not isinstance(value, Mapping):
        _fail(code)
    return value


class _Timeline:
    __slots__ = ("_code", "_last", "_source")

    def __init__(
        self, source: Callable[[], float], code: str = "runtime_ingress.clock_invalid"
    ) -> None:
        if not callable(source):
            _fail(code)
        self._source = source
        self._code = code
        self._last: float | None = None

    def read(self) -> float:
        try:
            value = self._source()
        except Exception:
            raise PrivateRuntimeMaterialFailure(self._code) from None
        if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
            _fail(self._code)
        current = float(value)
        if self._last is not None and current < self._last:
            _fail(self._code)
        self._last = current
        return current


def _remaining_timeout(timeline: _Timeline, *, deadline: float, maximum: int) -> int:
    remaining = math.floor(deadline - timeline.read())
    if remaining < 1:
        _fail("runtime_ingress.deadline")
    return min(maximum, remaining)


def _open_exact(
    opener: _HttpOpener,
    request: urllib.request.Request,
    *,
    expected_uri: str,
    maximum_bytes: int,
    timeout_seconds: int,
    deadline_factory: Callable[[int], Any],
) -> tuple[int, tuple[str, ...], bytes]:
    try:
        with deadline_factory(timeout_seconds):
            try:
                response = opener.open(request, timeout=timeout_seconds)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                return _read_wire_response(
                    response,
                    expected_uri=expected_uri,
                    maximum_bytes=maximum_bytes,
                )
    except PrivateRuntimeMaterialFailure:
        raise
    except runtime_adapters.RuntimeAdapterFailure as error:
        code = (
            "runtime_ingress.deadline"
            if str(error) == "http_request.timeout"
            else "runtime_ingress.transport_unavailable"
        )
        raise PrivateRuntimeMaterialFailure(code) from None
    except Exception:
        raise PrivateRuntimeMaterialFailure(
            "runtime_ingress.transport_unavailable"
        ) from None


def _managed_identity_token(
    *,
    client_id: str,
    resource: str,
    contract: Mapping[str, Any],
    identity_opener: _HttpOpener,
    identity_endpoint: str,
    identity_header: str,
    epoch_timeline: _Timeline,
    timeout_seconds: int,
    deadline_factory: Callable[[int], Any],
) -> str:
    try:
        endpoint = runtime_adapters._validate_identity_endpoint(identity_endpoint)
    except runtime_adapters.RuntimeAdapterFailure:
        _fail("runtime_ingress.identity_configuration_invalid")
    if (
        type(client_id) is not str
        or runtime_adapters.UUID_RE.fullmatch(client_id) is None
        or type(resource) is not str
        or not resource
        or type(identity_header) is not str
        or runtime_adapters.SAFE_TOKEN_RE.fullmatch(identity_header) is None
        or len(identity_header) > 8_192
    ):
        _fail("runtime_ingress.identity_configuration_invalid")
    query = urllib.parse.urlencode(
        {
            "api-version": contract["managed_identity_api_version"],
            "resource": resource,
            "client_id": client_id,
        }
    )
    uri = f"{endpoint}?{query}"
    request = urllib.request.Request(
        uri,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Metadata": "true",
            "X-IDENTITY-HEADER": identity_header,
        },
        method="GET",
    )
    status, content_types, raw = _open_exact(
        identity_opener,
        request,
        expected_uri=uri,
        maximum_bytes=contract["managed_identity_response_maximum_bytes"],
        timeout_seconds=timeout_seconds,
        deadline_factory=deadline_factory,
    )
    if status != 200:
        _fail("runtime_ingress.identity_refused")
    if not _media_type_matches(content_types, "application/json"):
        _fail("runtime_ingress.identity_response_invalid")
    response = _json_object(raw, "runtime_ingress.identity_response_invalid")
    required = set(contract["managed_identity_response_required_fields"])
    token = response.get("access_token")
    expires_on = response.get("expires_on")
    if (
        not required <= set(response)
        or type(token) is not str
        or runtime_adapters.SAFE_TOKEN_RE.fullmatch(token) is None
        or response.get("token_type") != "Bearer"
        or response.get("resource") != resource
        or response.get("client_id") != client_id
    ):
        _fail("runtime_ingress.identity_response_invalid")
    if (
        type(expires_on) is not str
        or runtime_adapters.UNSIGNED_DECIMAL_RE.fullmatch(expires_on) is None
    ):
        _fail("runtime_ingress.identity_response_invalid")
    expiration = int(expires_on)
    now = epoch_timeline.read()
    if (
        expiration <= now + contract["managed_identity_expiry_skew_seconds"]
        or expiration
        > now + contract["managed_identity_maximum_token_lifetime_seconds"]
    ):
        _fail("runtime_ingress.identity_response_invalid")
    return token


def _default_identity_opener() -> Any:
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), runtime_adapters._NoRedirects()
    )


def _default_service_opener(ca_bundle_path: str) -> Any:
    context = runtime_adapters._runtime_tls_context(Path(ca_bundle_path))
    return runtime_adapters._default_opener(tls_context=context)


def _execution_matches(
    receipt: launch_binding.AuthenticatedBrokerLaunchReceipt,
) -> bool:
    return os.environ.get(launch_binding.EXECUTION_NAME_ENV) == receipt.execution_name


class ManagedIdentityPrivateRuntimeMaterialReader:
    """One-shot exact Blob/Key Vault reader followed by offline authentication."""

    __slots__ = (
        "_blob_opener",
        "_deadline_factory",
        "_epoch_timeline",
        "_identity_opener",
        "_lock",
        "_monotonic",
        "_used",
        "_vault_opener",
        "_verifier",
    )

    def __init__(
        self,
        *,
        verifier: _OfflineVerifier,
        identity_opener: _HttpOpener | None = None,
        blob_opener: _HttpOpener | None = None,
        vault_opener: _HttpOpener | None = None,
        epoch_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        deadline_factory: Callable[[int], Any] = (
            runtime_adapters._wall_clock_deadline
        ),
    ) -> None:
        if (
            not callable(getattr(verifier, "verify", None))
            or any(
                opener is not None and not callable(getattr(opener, "open", None))
                for opener in (identity_opener, blob_opener, vault_opener)
            )
            or not callable(epoch_clock)
            or not callable(monotonic)
            or not callable(deadline_factory)
        ):
            _fail("runtime_ingress.configuration_invalid")
        self._verifier = verifier
        self._identity_opener = identity_opener
        self._blob_opener = blob_opener
        self._vault_opener = vault_opener
        self._epoch_timeline = _Timeline(
            epoch_clock, "runtime_ingress.identity_clock_invalid"
        )
        self._monotonic = monotonic
        self._deadline_factory = deadline_factory
        self._lock = threading.Lock()
        self._used = False

    def _claim_use(self) -> None:
        with self._lock:
            if self._used:
                _fail("runtime_ingress.already_used")
            self._used = True

    def read_and_authenticate_runtime_record(
        self,
        *,
        policy: Any,
        authenticated_receipt: launch_binding.AuthenticatedBrokerLaunchReceipt,
        provision_reference: runtime_binding.ExternalRuntimeBindingReference,
        cleanup_reference: runtime_binding.ExternalRuntimeBindingReference,
        clock: Callable[[], datetime],
    ) -> launch_material.AuthenticatedRuntimeRecordMaterial:
        self._claim_use()
        try:
            validated = runtime_binding.validate_runtime_binding_policy(
                policy, executable=True
            )
            document = runtime_binding._parse_policy(
                runtime_binding._canonical(validated)
            )
        except Exception:
            raise PrivateRuntimeMaterialFailure(
                "runtime_ingress.policy_invalid"
            ) from None
        action = getattr(authenticated_receipt, "action", None)
        if (
            type(authenticated_receipt)
            is not launch_binding.AuthenticatedBrokerLaunchReceipt
            or not authenticated_receipt._is_authenticated()
            or action not in launch_binding.JOB_KINDS
            or not callable(clock)
            or type(authenticated_receipt.receipt_bytes) is not bytes
            or _digest(authenticated_receipt.receipt_bytes)
            != authenticated_receipt.receipt_sha256
        ):
            _fail("runtime_ingress.receipt_invalid")
        receipt_state = self._receipt_state(authenticated_receipt)
        self._validate_references(
            document=document,
            receipt=authenticated_receipt,
            provision_reference=provision_reference,
            cleanup_reference=cleanup_reference,
        )
        if not _execution_matches(authenticated_receipt):
            _fail("runtime_ingress.execution_identity_invalid")

        blob_contract = document["runtime_record_ingress"]["retrieval"]
        secret_source = document["evidence_ingress"]["private_versioned_secret_source"]
        secret_contract = secret_source["retrieval"]
        identity = document["identities"]["jobs"][action]
        identity_endpoint = os.environ.get("IDENTITY_ENDPOINT", "")
        identity_header = os.environ.get("IDENTITY_HEADER", "")
        timeline = _Timeline(self._monotonic)
        deadline = timeline.read() + blob_contract["maximum_elapsed_seconds"]
        identity_opener = self._identity_opener or _default_identity_opener()
        blob_opener = self._blob_opener or _default_service_opener(
            blob_contract["runtime_ca_bundle_path"]
        )
        vault_opener = self._vault_opener or _default_service_opener(
            secret_contract["runtime_ca_bundle_path"]
        )

        storage_token = _managed_identity_token(
            client_id=identity["client_id"],
            resource=document["runtime_record_ingress"]["oauth_resource"],
            contract=blob_contract,
            identity_opener=identity_opener,
            identity_endpoint=identity_endpoint,
            identity_header=identity_header,
            epoch_timeline=self._epoch_timeline,
            timeout_seconds=_remaining_timeout(
                timeline,
                deadline=deadline,
                maximum=blob_contract["maximum_request_timeout_seconds"],
            ),
            deadline_factory=self._deadline_factory,
        )
        self._checkpoint(authenticated_receipt, receipt_state, timeline, deadline)
        record_bytes = self._read_blob(
            opener=blob_opener,
            token=storage_token,
            uri=provision_reference.record_uri,
            expected_digest=provision_reference.record_sha256,
            expected_media_type=blob_contract["record_media_type"],
            maximum_bytes=document["external_runtime_record"]["record"][
                "maximum_bytes"
            ],
            contract=blob_contract,
            timeline=timeline,
            deadline=deadline,
            failure="runtime_ingress.record_invalid",
        )
        self._checkpoint(authenticated_receipt, receipt_state, timeline, deadline)
        bundle_bytes = self._read_blob(
            opener=blob_opener,
            token=storage_token,
            uri=provision_reference.signature_bundle_uri,
            expected_digest=provision_reference.signature_bundle_sha256,
            expected_media_type=blob_contract["signature_bundle_media_type"],
            maximum_bytes=authentication.MAXIMUM_BUNDLE_BYTES,
            contract=blob_contract,
            timeline=timeline,
            deadline=deadline,
            failure="runtime_ingress.signature_bundle_invalid",
        )
        self._checkpoint(authenticated_receipt, receipt_state, timeline, deadline)

        vault_token = _managed_identity_token(
            client_id=identity["client_id"],
            resource=secret_source["oauth_resource"],
            contract=secret_contract,
            identity_opener=identity_opener,
            identity_endpoint=identity_endpoint,
            identity_header=identity_header,
            epoch_timeline=self._epoch_timeline,
            timeout_seconds=_remaining_timeout(
                timeline,
                deadline=deadline,
                maximum=secret_contract["maximum_request_timeout_seconds"],
            ),
            deadline_factory=self._deadline_factory,
        )
        self._checkpoint(authenticated_receipt, receipt_state, timeline, deadline)
        trusted_root_bytes = self._read_trusted_root(
            opener=vault_opener,
            token=vault_token,
            secret_source=secret_source,
            contract=secret_contract,
            timeline=timeline,
            deadline=deadline,
        )
        self._checkpoint(authenticated_receipt, receipt_state, timeline, deadline)
        try:
            proof = authentication.authenticate_runtime_record_signature(
                document,
                provision_reference=provision_reference,
                cleanup_reference=cleanup_reference,
                record_bytes=record_bytes,
                signature_bundle_bytes=bundle_bytes,
                trusted_root_bytes=trusted_root_bytes,
                verifier=self._verifier,
                clock=clock,
            )
        except Exception:
            raise PrivateRuntimeMaterialFailure(
                "runtime_ingress.authentication_failed"
            ) from None
        self._checkpoint(authenticated_receipt, receipt_state, timeline, deadline)
        return launch_material.AuthenticatedRuntimeRecordMaterial(
            record_bytes=bytes(record_bytes), signature_verification=proof
        )

    @staticmethod
    def _receipt_state(
        receipt: launch_binding.AuthenticatedBrokerLaunchReceipt,
    ) -> tuple[Any, ...]:
        try:
            return (
                receipt.receipt_bytes,
                receipt.receipt_sha256,
                receipt.action,
                receipt.execution_name,
                receipt.launch_record_uri,
                receipt.launch_record_sha256,
                receipt.receipt_uri,
                receipt.signature_envelope_uri,
                receipt.signature_envelope_sha256,
                receipt.observed_at,
                *(
                    receipt.runtime_reference[field]
                    for field in runtime_binding.RUNTIME_REFERENCE_FIELDS
                ),
            )
        except Exception:
            raise PrivateRuntimeMaterialFailure(
                "runtime_ingress.receipt_invalid"
            ) from None

    @staticmethod
    def _validate_references(
        *,
        document: Mapping[str, Any],
        receipt: launch_binding.AuthenticatedBrokerLaunchReceipt,
        provision_reference: runtime_binding.ExternalRuntimeBindingReference,
        cleanup_reference: runtime_binding.ExternalRuntimeBindingReference,
    ) -> None:
        references = {
            "provision": provision_reference,
            "cleanup": cleanup_reference,
        }
        receipt_values = getattr(receipt, "runtime_reference", None)
        if not isinstance(receipt_values, Mapping):
            _fail("runtime_ingress.reference_invalid")
        for job_kind, reference in references.items():
            if (
                type(reference) is not runtime_binding.ExternalRuntimeBindingReference
                or not reference._is_bound()
                or reference.source_job_kind != job_kind
                or reference.source_job_resource_id
                != document["runtime"]["jobs"][job_kind]["resource_id"]
                or any(
                    getattr(reference, field, None) != receipt_values.get(field)
                    for field in runtime_binding.RUNTIME_REFERENCE_FIELDS
                )
            ):
                _fail("runtime_ingress.reference_invalid")
        if any(
            getattr(provision_reference, field) != getattr(cleanup_reference, field)
            for field in runtime_binding.RUNTIME_REFERENCE_FIELDS
        ):
            _fail("runtime_ingress.reference_invalid")
        ingress = document["runtime_record_ingress"]
        expected = {
            "record_uri": (
                f"https://{ingress['account_host']}/{ingress['container_name']}/"
                f"{provision_reference.record_sha256[7:]}.json"
            ),
            "signature_bundle_uri": (
                f"https://{ingress['account_host']}/{ingress['container_name']}/"
                f"{provision_reference.signature_bundle_sha256[7:]}.sigstore.json"
            ),
        }
        if any(
            getattr(provision_reference, name) != value
            for name, value in expected.items()
        ):
            _fail("runtime_ingress.reference_invalid")

    @staticmethod
    def _checkpoint(
        receipt: launch_binding.AuthenticatedBrokerLaunchReceipt,
        expected_receipt_state: tuple[Any, ...],
        timeline: _Timeline,
        deadline: float,
    ) -> None:
        if (
            not receipt._is_authenticated()
            or ManagedIdentityPrivateRuntimeMaterialReader._receipt_state(receipt)
            != expected_receipt_state
        ):
            _fail("runtime_ingress.receipt_invalid")
        if not _execution_matches(receipt):
            _fail("runtime_ingress.execution_identity_invalid")
        if timeline.read() > deadline:
            _fail("runtime_ingress.deadline")

    def _read_blob(
        self,
        *,
        opener: _HttpOpener,
        token: str,
        uri: str,
        expected_digest: str,
        expected_media_type: str,
        maximum_bytes: int,
        contract: Mapping[str, Any],
        timeline: _Timeline,
        deadline: float,
        failure: str,
    ) -> bytes:
        request = urllib.request.Request(
            uri,
            headers={
                "Accept": expected_media_type,
                "Accept-Encoding": contract["content_encoding"],
                "Authorization": f"Bearer {token}",
                "x-ms-version": contract["service_api_version"],
            },
            method=contract["method"],
        )
        try:
            status, content_types, body = _open_exact(
                opener,
                request,
                expected_uri=uri,
                maximum_bytes=maximum_bytes,
                timeout_seconds=_remaining_timeout(
                    timeline,
                    deadline=deadline,
                    maximum=contract["maximum_request_timeout_seconds"],
                ),
                deadline_factory=self._deadline_factory,
            )
        except PrivateRuntimeMaterialFailure:
            raise PrivateRuntimeMaterialFailure(failure) from None
        if (
            status != contract["successful_status"]
            or not _media_type_matches(content_types, expected_media_type)
            or not body
            or _digest(body) != expected_digest
        ):
            _fail(failure)
        return body

    def _read_trusted_root(
        self,
        *,
        opener: _HttpOpener,
        token: str,
        secret_source: Mapping[str, Any],
        contract: Mapping[str, Any],
        timeline: _Timeline,
        deadline: float,
    ) -> bytes:
        root = secret_source["secrets"]["github_trusted_root"]
        base_uri = root["versioned_https_uri"]
        parsed = urllib.parse.urlsplit(base_uri)
        parts = parsed.path.split("/")
        if (
            parsed.scheme != "https"
            or parsed.netloc != secret_source["vault_host"]
            or parsed.hostname != secret_source["vault_host"]
            or parsed.query
            or parsed.fragment
            or len(parts) != 4
            or parts[:2] != ["", "secrets"]
            or KEY_VAULT_SECRET_NAME_RE.fullmatch(parts[2]) is None
            or KEY_VAULT_SECRET_VERSION_RE.fullmatch(parts[3]) is None
            or parts[3] != root["version"]
        ):
            _fail("runtime_ingress.trusted_root_invalid")
        uri = f"{base_uri}?{urllib.parse.urlencode({'api-version': contract['api_version']})}"
        request = urllib.request.Request(
            uri,
            headers={
                "Accept": contract["response_media_type"],
                "Accept-Encoding": contract["content_encoding"],
                "Authorization": f"Bearer {token}",
            },
            method=contract["method"],
        )
        try:
            status, content_types, raw = _open_exact(
                opener,
                request,
                expected_uri=uri,
                maximum_bytes=contract["maximum_response_bytes"],
                timeout_seconds=_remaining_timeout(
                    timeline,
                    deadline=deadline,
                    maximum=contract["maximum_request_timeout_seconds"],
                ),
                deadline_factory=self._deadline_factory,
            )
            response = _json_object(raw, "runtime_ingress.trusted_root_invalid")
        except PrivateRuntimeMaterialFailure:
            raise PrivateRuntimeMaterialFailure(
                "runtime_ingress.trusted_root_invalid"
            ) from None
        required = set(contract["response_required_fields"])
        value = response.get("value")
        attributes = response.get("attributes")
        if (
            status != contract["successful_status"]
            or not _media_type_matches(content_types, contract["response_media_type"])
            or not required <= set(response)
            or response.get("id") != base_uri
            or type(value) is not str
            or not isinstance(attributes, Mapping)
            or attributes.get("enabled") is not True
        ):
            _fail("runtime_ingress.trusted_root_invalid")
        try:
            root_bytes = value.encode(contract["secret_value_encoding"])
        except (LookupError, UnicodeEncodeError):
            raise PrivateRuntimeMaterialFailure(
                "runtime_ingress.trusted_root_invalid"
            ) from None
        if (
            not root["minimum_bytes"] <= len(root_bytes) <= root["maximum_bytes"]
            or _digest(root_bytes) != root["sha256"]
        ):
            _fail("runtime_ingress.trusted_root_invalid")
        return root_bytes


def build_private_runtime_material_reader(
    policy: Any,
) -> ManagedIdentityPrivateRuntimeMaterialReader:
    """Build the dormant concrete reader without wiring or executing it."""

    try:
        document = runtime_binding.validate_runtime_binding_policy(
            policy, executable=True
        )
        verifier = document["offline_github_verifier"]
        concrete = runtime_adapters.DigestPinnedGitHubCliVerifier(
            executable_path=Path(verifier["executable_path"]),
            expected_sha256=verifier["executable_sha256"],
            expected_version=verifier["version"],
            profile=runtime_adapters.RUNTIME_RECORD_SIGNATURE_PROFILE,
        )
        return ManagedIdentityPrivateRuntimeMaterialReader(verifier=concrete)
    except Exception:
        raise PrivateRuntimeMaterialFailure(
            "runtime_ingress.configuration_invalid"
        ) from None
