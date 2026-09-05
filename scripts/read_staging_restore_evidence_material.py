#!/usr/bin/env python3
"""Dormant exact private ingress for provision evidence and HMAC material.

The reader accepts only a sealed broker receipt and its validated provision
launch record.  It performs exact-version private reads and returns immutable
bytes; it is intentionally not wired into the trusted executor.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import math
import os
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime
from typing import Any, NoReturn, Protocol

try:
    from scripts import read_staging_runtime_record_material as runtime_ingress
    from scripts import staging_restore_runtime_adapters as runtime_adapters
    from scripts import validate_staging_restore_launch_binding as launch_binding
    from scripts import validate_staging_restore_runtime_binding as runtime_binding
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    import read_staging_runtime_record_material as runtime_ingress
    import staging_restore_runtime_adapters as runtime_adapters
    import validate_staging_restore_launch_binding as launch_binding
    import validate_staging_restore_runtime_binding as runtime_binding


PROVISION_ACTION = "provision"
MAXIMUM_EVIDENCE_FILE_BYTES = 8_388_608
MAXIMUM_TOTAL_EVIDENCE_BYTES = 50_331_648
MAXIMUM_ATTESTATION_BYTES = 2_097_152
MINIMUM_HMAC_KEY_BYTES = 32
MAXIMUM_HMAC_KEY_BYTES = 4_096
_PRIVATE_PROVISION_INPUTS_SEAL = object()


class PrivateEvidenceMaterialFailure(RuntimeError):
    """Bounded refusal without credentials, selectors, or response bytes."""


def _fail(code: str) -> NoReturn:
    raise PrivateEvidenceMaterialFailure(code)


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


class _HttpOpener(Protocol):
    def open(self, request: Any, *, timeout: int) -> Any: ...


@dataclass(frozen=True, slots=True)
class _BlobSelector:
    logical_name: str
    uri: str
    version_id: str
    sha256: str
    media_type: str
    maximum_bytes: int


@dataclass(frozen=True, slots=True)
class _SecretSelector:
    uri: str
    version: str
    encoding: str
    material_sha256: str
    minimum_bytes: int
    maximum_bytes: int


@dataclass(frozen=True, slots=True)
class _AuthoritySnapshot:
    receipt_sha256: str
    launch_record_sha256: str
    execution_name: str
    execution_binding: str
    launch_created_at: datetime
    launch_expires_at: datetime
    signed_receipt_observed_at: datetime
    authenticated_receipt_observed_at: datetime
    evidence: tuple[_BlobSelector, ...]
    attestation: _BlobSelector
    hmac_key: _SecretSelector


def _inputs_digest(
    *,
    evidence_entries: tuple[tuple[str, bytes], ...],
    attestation_bundle_bytes: bytes,
    server_scope_hmac_key_bytes: bytes,
    receipt_sha256: str,
    launch_record_sha256: str,
    execution_binding: str,
) -> str:
    projection = {
        "evidence": [
            {"logical_name": name, "sha256": _digest(raw)}
            for name, raw in evidence_entries
        ],
        "attestation_bundle_sha256": _digest(attestation_bundle_bytes),
        "server_scope_hmac_key_sha256": _digest(server_scope_hmac_key_bytes),
        "receipt_sha256": receipt_sha256,
        "launch_record_sha256": launch_record_sha256,
        "execution_binding": execution_binding,
    }
    return _digest(runtime_binding._canonical(projection))


@dataclass(frozen=True, slots=True, init=False)
class PrivateProvisionInputs:
    """Sealed immutable material for one future same-process provision call."""

    evidence_entries: tuple[tuple[str, bytes], ...] = dataclass_field(repr=False)
    attestation_bundle_bytes: bytes = dataclass_field(repr=False)
    server_scope_hmac_key_bytes: bytes = dataclass_field(repr=False)
    receipt_sha256: str
    launch_record_sha256: str
    execution_binding: str
    _material_sha256: str = dataclass_field(repr=False, compare=False)
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("evidence_ingress.result_unsealed")

    @classmethod
    def _from_exact_reads(
        cls,
        *,
        evidence_entries: tuple[tuple[str, bytes], ...],
        attestation_bundle_bytes: bytes,
        server_scope_hmac_key_bytes: bytes,
        receipt_sha256: str,
        launch_record_sha256: str,
        execution_binding: str,
    ) -> PrivateProvisionInputs:
        if (
            cls is not PrivateProvisionInputs
            or type(evidence_entries) is not tuple
            or len(evidence_entries) != 6
            or any(
                type(entry) is not tuple
                or len(entry) != 2
                or type(entry[0]) is not str
                or type(entry[1]) is not bytes
                or not 1 <= len(entry[1]) <= MAXIMUM_EVIDENCE_FILE_BYTES
                for entry in evidence_entries
            )
            or len({entry[0] for entry in evidence_entries}) != 6
            or sum(len(entry[1]) for entry in evidence_entries)
            > MAXIMUM_TOTAL_EVIDENCE_BYTES
            or type(attestation_bundle_bytes) is not bytes
            or not 1 <= len(attestation_bundle_bytes) <= MAXIMUM_ATTESTATION_BYTES
            or type(server_scope_hmac_key_bytes) is not bytes
            or not MINIMUM_HMAC_KEY_BYTES
            <= len(server_scope_hmac_key_bytes)
            <= MAXIMUM_HMAC_KEY_BYTES
            or any(
                type(value) is not str
                or runtime_binding.DIGEST_RE.fullmatch(value) is None
                for value in (receipt_sha256, launch_record_sha256)
            )
            or type(execution_binding) is not str
            or not execution_binding
        ):
            _fail("evidence_ingress.result_invalid")
        copied_entries = tuple((name, bytes(raw)) for name, raw in evidence_entries)
        attestation = bytes(attestation_bundle_bytes)
        hmac_key = bytes(server_scope_hmac_key_bytes)
        material_sha256 = _inputs_digest(
            evidence_entries=copied_entries,
            attestation_bundle_bytes=attestation,
            server_scope_hmac_key_bytes=hmac_key,
            receipt_sha256=receipt_sha256,
            launch_record_sha256=launch_record_sha256,
            execution_binding=execution_binding,
        )
        value = object.__new__(cls)
        object.__setattr__(value, "evidence_entries", copied_entries)
        object.__setattr__(value, "attestation_bundle_bytes", attestation)
        object.__setattr__(value, "server_scope_hmac_key_bytes", hmac_key)
        object.__setattr__(value, "receipt_sha256", receipt_sha256)
        object.__setattr__(value, "launch_record_sha256", launch_record_sha256)
        object.__setattr__(value, "execution_binding", execution_binding)
        object.__setattr__(value, "_material_sha256", material_sha256)
        object.__setattr__(value, "_seal", _PRIVATE_PROVISION_INPUTS_SEAL)
        return value

    def _is_sealed(self) -> bool:
        try:
            return (
                type(self) is PrivateProvisionInputs
                and self._seal is _PRIVATE_PROVISION_INPUTS_SEAL
                and type(self._material_sha256) is str
                and runtime_binding.DIGEST_RE.fullmatch(self._material_sha256)
                is not None
                and self._material_sha256
                == _inputs_digest(
                    evidence_entries=self.evidence_entries,
                    attestation_bundle_bytes=self.attestation_bundle_bytes,
                    server_scope_hmac_key_bytes=self.server_scope_hmac_key_bytes,
                    receipt_sha256=self.receipt_sha256,
                    launch_record_sha256=self.launch_record_sha256,
                    execution_binding=self.execution_binding,
                )
            )
        except Exception:
            return False

    def evidence_files(self) -> dict[str, bytes]:
        """Return a detached exact mapping for the in-memory bundle validator."""

        if not self._is_sealed():
            _fail("evidence_ingress.result_invalid")
        return {name: bytes(raw) for name, raw in self.evidence_entries}

    def _is_bound_to(
        self,
        policy: Any,
        authenticated_receipt: launch_binding.AuthenticatedBrokerLaunchReceipt,
        launch_record: launch_binding.ValidatedLaunchRecord,
    ) -> bool:
        """Recheck this carrier against its canonical receipt/record authority."""

        try:
            document = runtime_binding.validate_runtime_binding_policy(
                policy, executable=True
            )
            snapshot = _authority_snapshot(
                document, authenticated_receipt, launch_record
            )
            return (
                self._is_sealed()
                and _execution_matches(snapshot.execution_name)
                and self.receipt_sha256 == snapshot.receipt_sha256
                and self.launch_record_sha256 == snapshot.launch_record_sha256
                and self.execution_binding == snapshot.execution_binding
                and tuple(name for name, _raw in self.evidence_entries)
                == tuple(item.logical_name for item in snapshot.evidence)
                and tuple(_digest(raw) for _name, raw in self.evidence_entries)
                == tuple(item.sha256 for item in snapshot.evidence)
                and all(
                    len(raw) <= selector.maximum_bytes
                    for (_name, raw), selector in zip(
                        self.evidence_entries, snapshot.evidence, strict=True
                    )
                )
                and _digest(self.attestation_bundle_bytes)
                == snapshot.attestation.sha256
                and len(self.attestation_bundle_bytes)
                <= snapshot.attestation.maximum_bytes
                and _digest(self.server_scope_hmac_key_bytes)
                == snapshot.hmac_key.material_sha256
                and snapshot.hmac_key.minimum_bytes
                <= len(self.server_scope_hmac_key_bytes)
                <= snapshot.hmac_key.maximum_bytes
            )
        except Exception:
            return False


class _Timeline:
    __slots__ = ("_last", "_source")

    def __init__(self, source: Callable[[], float]) -> None:
        if not callable(source):
            _fail("evidence_ingress.configuration_invalid")
        self._source = source
        self._last: float | None = None

    def read(self) -> float:
        try:
            value = self._source()
        except Exception:
            raise PrivateEvidenceMaterialFailure(
                "evidence_ingress.clock_invalid"
            ) from None
        if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
            _fail("evidence_ingress.clock_invalid")
        current = float(value)
        if self._last is not None and current < self._last:
            _fail("evidence_ingress.clock_invalid")
        self._last = current
        return current


class _TrustedUtcTimeline:
    __slots__ = ("_last", "_source")

    def __init__(self, source: Callable[[], datetime]) -> None:
        if not callable(source):
            _fail("evidence_ingress.trusted_clock_invalid")
        self._source = source
        self._last: datetime | None = None

    def read(self) -> datetime:
        try:
            current = launch_binding._exact_time(
                self._source(), "evidence_ingress.trusted_clock_invalid"
            )
        except Exception:
            raise PrivateEvidenceMaterialFailure(
                "evidence_ingress.trusted_clock_invalid"
            ) from None
        if self._last is not None and current < self._last:
            _fail("evidence_ingress.trusted_clock_regressed")
        self._last = current
        return current


def _remaining_timeout(timeline: _Timeline, *, deadline: float, maximum: int) -> int:
    remaining = math.floor(deadline - timeline.read())
    if remaining < 1:
        _fail("evidence_ingress.deadline")
    return min(maximum, remaining)


def _execution_matches(execution_name: str) -> bool:
    return os.environ.get(launch_binding.EXECUTION_NAME_ENV) == execution_name


def _canonical_receipt(
    document: Mapping[str, Any],
    receipt: launch_binding.AuthenticatedBrokerLaunchReceipt,
) -> Mapping[str, Any]:
    contract = document["launch_binding"]["launch_receipt"]
    signed = launch_binding._parse(
        receipt.receipt_bytes,
        maximum=contract["maximum_bytes"],
        code="evidence_ingress.receipt_invalid",
    )
    launch_binding._mapping(
        signed,
        set(contract["required_fields"]),
        "evidence_ingress.receipt_invalid",
    )
    return signed


def _authority_snapshot(
    document: Mapping[str, Any],
    receipt: Any,
    launch_record: Any,
) -> _AuthoritySnapshot:
    try:
        if (
            type(receipt) is not launch_binding.AuthenticatedBrokerLaunchReceipt
            or not receipt._is_authenticated()
            or type(receipt.receipt_bytes) is not bytes
            or _digest(receipt.receipt_bytes) != receipt.receipt_sha256
            or receipt.action != PROVISION_ACTION
            or type(launch_record) is not launch_binding.ValidatedLaunchRecord
            or not launch_record._is_validated()
            or launch_record.action != PROVISION_ACTION
            or type(launch_record.record_bytes) is not bytes
            or _digest(launch_record.record_bytes) != launch_record.record_sha256
            or receipt.launch_record_uri != launch_record.record_uri
            or receipt.launch_record_sha256 != launch_record.record_sha256
            or launch_record.runtime_record_sha256
            != receipt.runtime_reference.get("record_sha256")
        ):
            _fail("evidence_ingress.authority_invalid")
        reviewed_record = launch_binding.validate_launch_record(
            document,
            launch_record.record_bytes,
            record_sha256=launch_record.record_sha256,
            record_uri=launch_record.record_uri,
        )
        if reviewed_record != launch_record or not reviewed_record._is_validated():
            _fail("evidence_ingress.authority_invalid")
        launch_record = reviewed_record
        receipt_snapshot = runtime_ingress.ManagedIdentityPrivateRuntimeMaterialReader._authority_snapshot(
            document, receipt
        )
        if (
            receipt_snapshot.receipt_sha256 != receipt.receipt_sha256
            or receipt_snapshot.action != PROVISION_ACTION
            or receipt_snapshot.execution_name != receipt.execution_name
        ):
            _fail("evidence_ingress.authority_invalid")
        signed = _canonical_receipt(document, receipt)
        execution_name = receipt.execution_name
        job_id = document["runtime"]["jobs"][PROVISION_ACTION]["resource_id"]
        execution_binding = f"{job_id}/executions/{execution_name}"
        signed_receipt_observed_at = launch_binding._time(
            signed["observed_at"], "evidence_ingress.authority_invalid"
        )
        authenticated_receipt_observed_at = launch_binding._exact_time(
            receipt.observed_at, "evidence_ingress.authority_invalid"
        )
        reference = {
            field: signed[f"runtime_{field}"]
            for field in runtime_binding.RUNTIME_REFERENCE_FIELDS
        }
        if (
            receipt.receipt_bytes != launch_binding._canonical(signed)
            or type(signed["schema_version"]) is not int
            or signed["schema_version"] != 1
            or signed["action"] != PROVISION_ACTION
            or signed["selected_job_resource_id"] != job_id
            or signed["execution_resource_id"] != execution_binding
            or signed["launch_record_uri"] != launch_record.record_uri
            or signed["launch_record_sha256"] != launch_record.record_sha256
            or signed["runtime_record_sha256"] != launch_record.runtime_record_sha256
            or not launch_record.created_at
            <= signed_receipt_observed_at
            <= authenticated_receipt_observed_at
            < launch_record.expires_at
            or type(execution_name) is not str
            or launch_binding.EXECUTION_NAME_RE.fullmatch(execution_name) is None
            or not isinstance(receipt.runtime_reference, Mapping)
            or set(receipt.runtime_reference)
            != set(runtime_binding.RUNTIME_REFERENCE_FIELDS)
            or any(
                receipt.runtime_reference[field] != value
                for field, value in reference.items()
            )
        ):
            _fail("evidence_ingress.authority_invalid")
        expected_names = tuple(
            document["evidence_ingress"]["private_restore_evidence_container"][
                "objects"
            ]["exact_evidence_file_names"]
        )
        authorities = launch_record.evidence_file_authorities
        attestation = launch_record.attestation_bundle_authority
        hmac_key = launch_record.server_scope_hmac_key_authority
        if (
            type(authorities) is not tuple
            or len(authorities) != len(expected_names)
            or tuple(item.logical_name for item in authorities) != expected_names
            or any(
                type(item) is not launch_binding.ValidatedVersionedBlobAuthority
                or not item._is_validated()
                for item in authorities
            )
            or type(attestation) is not launch_binding.ValidatedVersionedBlobAuthority
            or not attestation._is_validated()
            or attestation.logical_name != "github_attestation_bundle"
            or type(hmac_key) is not launch_binding.ValidatedVersionedSecretAuthority
            or not hmac_key._is_validated()
            or hmac_key.logical_name != "server_scope_hmac_key"
            or hmac_key.encoding != "base64url-no-padding"
        ):
            _fail("evidence_ingress.authority_invalid")
        blob = document["evidence_ingress"]["private_restore_evidence_container"]
        retrieval = blob["retrieval"]
        maximum = blob["objects"]["evidence_file_contract"]["maximum_bytes_each"]
        evidence = tuple(
            _blob_selector(
                item,
                document=document,
                rehearsal_id=launch_record.rehearsal_id,
                media_type=retrieval["evidence_file_media_types"][item.logical_name],
                maximum_bytes=maximum,
            )
            for item in authorities
        )
        attestation_selector = _blob_selector(
            attestation,
            document=document,
            rehearsal_id=launch_record.rehearsal_id,
            media_type=retrieval["github_attestation_bundle_media_type"],
            maximum_bytes=blob["objects"]["github_attestation_bundle_contract"][
                "maximum_bytes"
            ],
        )
        secret = document["evidence_ingress"]["private_versioned_secret_source"]
        secret_policy = secret["secrets"]["server_scope_hmac_key"]
        launch_binding._versioned_secret(
            hmac_key.versioned_https_uri,
            host=secret["vault_host"],
            version=hmac_key.version,
            code="evidence_ingress.authority_invalid",
        )
        if (
            hmac_key.versioned_https_uri != secret_policy["versioned_https_uri"]
            or hmac_key.version != secret_policy["version"]
        ):
            _fail("evidence_ingress.authority_invalid")
        return _AuthoritySnapshot(
            receipt_sha256=receipt.receipt_sha256,
            launch_record_sha256=launch_record.record_sha256,
            execution_name=execution_name,
            execution_binding=execution_binding,
            launch_created_at=launch_record.created_at,
            launch_expires_at=launch_record.expires_at,
            signed_receipt_observed_at=signed_receipt_observed_at,
            authenticated_receipt_observed_at=authenticated_receipt_observed_at,
            evidence=evidence,
            attestation=attestation_selector,
            hmac_key=_SecretSelector(
                uri=hmac_key.versioned_https_uri,
                version=hmac_key.version,
                encoding=hmac_key.encoding,
                material_sha256=hmac_key.material_sha256,
                minimum_bytes=secret_policy["minimum_bytes"],
                maximum_bytes=secret_policy["maximum_bytes"],
            ),
        )
    except PrivateEvidenceMaterialFailure:
        raise
    except Exception:
        raise PrivateEvidenceMaterialFailure(
            "evidence_ingress.authority_invalid"
        ) from None


def _blob_selector(
    authority: launch_binding.ValidatedVersionedBlobAuthority,
    *,
    document: Mapping[str, Any],
    rehearsal_id: str | None,
    media_type: str,
    maximum_bytes: int,
) -> _BlobSelector:
    if type(rehearsal_id) is not str:
        _fail("evidence_ingress.authority_invalid")
    blob = document["evidence_ingress"]["private_restore_evidence_container"]
    uri, version = launch_binding._versioned_blob(
        authority.versioned_https_uri,
        host=blob["account_host"],
        container=blob["container_name"],
        rehearsal_id=rehearsal_id,
        code="evidence_ingress.authority_invalid",
    )
    if version != authority.version_id:
        _fail("evidence_ingress.authority_invalid")
    return _BlobSelector(
        logical_name=authority.logical_name,
        uri=uri,
        version_id=version,
        sha256=authority.sha256,
        media_type=media_type,
        maximum_bytes=maximum_bytes,
    )


class ManagedIdentityPrivateEvidenceMaterialReader:
    """Burn-on-entry reader for seven Blobs and one exact-version secret."""

    __slots__ = (
        "_blob_opener",
        "_clock",
        "_deadline_factory",
        "_epoch_clock",
        "_identity_opener",
        "_lock",
        "_monotonic",
        "_used",
        "_vault_opener",
    )

    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
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
            not callable(clock)
            or any(
                opener is not None and not callable(getattr(opener, "open", None))
                for opener in (identity_opener, blob_opener, vault_opener)
            )
            or not callable(epoch_clock)
            or not callable(monotonic)
            or not callable(deadline_factory)
        ):
            _fail("evidence_ingress.configuration_invalid")
        self._clock = clock
        self._identity_opener = identity_opener
        self._blob_opener = blob_opener
        self._vault_opener = vault_opener
        self._epoch_clock = epoch_clock
        self._monotonic = monotonic
        self._deadline_factory = deadline_factory
        self._lock = threading.Lock()
        self._used = False

    def _claim_use(self) -> None:
        with self._lock:
            if self._used:
                _fail("evidence_ingress.already_used")
            self._used = True

    def read_provision_inputs(
        self,
        policy: Any,
        authenticated_receipt: launch_binding.AuthenticatedBrokerLaunchReceipt,
        launch_record: launch_binding.ValidatedLaunchRecord,
    ) -> PrivateProvisionInputs:
        """Read exact provision inputs without accepting any caller selector."""

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
            raise PrivateEvidenceMaterialFailure(
                "evidence_ingress.policy_invalid"
            ) from None
        snapshot = _authority_snapshot(document, authenticated_receipt, launch_record)
        blob = document["evidence_ingress"]["private_restore_evidence_container"]
        retrieval = blob["retrieval"]
        secret_source = document["evidence_ingress"]["private_versioned_secret_source"]
        secret_contract = secret_source["retrieval"]
        identity = document["identities"]["jobs"][PROVISION_ACTION]
        identity_endpoint = os.environ.get("IDENTITY_ENDPOINT", "")
        identity_header = os.environ.get("IDENTITY_HEADER", "")
        timeline = _Timeline(self._monotonic)
        trusted_utc = _TrustedUtcTimeline(self._clock)
        deadline = timeline.read() + retrieval["maximum_elapsed_seconds"]
        try:
            identity_opener = (
                self._identity_opener or runtime_ingress._default_identity_opener()
            )
            blob_opener = self._blob_opener or runtime_ingress._default_service_opener(
                retrieval["runtime_ca_bundle_path"]
            )
            vault_opener = (
                self._vault_opener
                or runtime_ingress._default_service_opener(
                    secret_contract["runtime_ca_bundle_path"]
                )
            )
            epoch_timeline = runtime_ingress._Timeline(
                self._epoch_clock, "runtime_ingress.identity_clock_invalid"
            )
        except Exception:
            raise PrivateEvidenceMaterialFailure(
                "evidence_ingress.configuration_invalid"
            ) from None

        def checkpoint() -> None:
            if (
                _authority_snapshot(document, authenticated_receipt, launch_record)
                != snapshot
            ):
                _fail("evidence_ingress.authority_invalid")
            if not _execution_matches(snapshot.execution_name):
                _fail("evidence_ingress.execution_identity_invalid")
            instant = trusted_utc.read()
            if not (
                snapshot.launch_created_at
                <= snapshot.signed_receipt_observed_at
                <= snapshot.authenticated_receipt_observed_at
                <= instant
                < snapshot.launch_expires_at
            ):
                _fail("evidence_ingress.launch_expired")
            if timeline.read() > deadline:
                _fail("evidence_ingress.deadline")

        checkpoint()
        storage_token = self._token(
            client_id=identity["client_id"],
            resource=blob["oauth_resource"],
            contract=retrieval,
            opener=identity_opener,
            identity_endpoint=identity_endpoint,
            identity_header=identity_header,
            epoch_timeline=epoch_timeline,
            timeline=timeline,
            deadline=deadline,
            failure="evidence_ingress.storage_identity_failed",
        )
        checkpoint()
        entries: list[tuple[str, bytes]] = []
        total = 0
        for selector in snapshot.evidence:
            raw = self._read_blob(
                opener=blob_opener,
                token=storage_token,
                selector=selector,
                contract=retrieval,
                timeline=timeline,
                deadline=deadline,
                failure="evidence_ingress.evidence_object_invalid",
            )
            total += len(raw)
            if total > retrieval["maximum_total_evidence_bytes"]:
                _fail("evidence_ingress.evidence_object_invalid")
            entries.append((selector.logical_name, raw))
            checkpoint()
        attestation = self._read_blob(
            opener=blob_opener,
            token=storage_token,
            selector=snapshot.attestation,
            contract=retrieval,
            timeline=timeline,
            deadline=deadline,
            failure="evidence_ingress.attestation_object_invalid",
        )
        checkpoint()
        vault_token = self._token(
            client_id=identity["client_id"],
            resource=secret_source["oauth_resource"],
            contract=secret_contract,
            opener=identity_opener,
            identity_endpoint=identity_endpoint,
            identity_header=identity_header,
            epoch_timeline=epoch_timeline,
            timeline=timeline,
            deadline=deadline,
            failure="evidence_ingress.vault_identity_failed",
        )
        checkpoint()
        hmac_key = self._read_hmac_key(
            opener=vault_opener,
            token=vault_token,
            selector=snapshot.hmac_key,
            secret_source=secret_source,
            contract=secret_contract,
            timeline=timeline,
            deadline=deadline,
        )
        checkpoint()
        result = PrivateProvisionInputs._from_exact_reads(
            evidence_entries=tuple(entries),
            attestation_bundle_bytes=attestation,
            server_scope_hmac_key_bytes=hmac_key,
            receipt_sha256=snapshot.receipt_sha256,
            launch_record_sha256=snapshot.launch_record_sha256,
            execution_binding=snapshot.execution_binding,
        )
        if not result._is_bound_to(document, authenticated_receipt, launch_record):
            _fail("evidence_ingress.result_invalid")
        checkpoint()
        return result

    def _token(
        self,
        *,
        client_id: str,
        resource: str,
        contract: Mapping[str, Any],
        opener: _HttpOpener,
        identity_endpoint: str,
        identity_header: str,
        epoch_timeline: runtime_ingress._Timeline,
        timeline: _Timeline,
        deadline: float,
        failure: str,
    ) -> str:
        try:
            return runtime_ingress._managed_identity_token(
                client_id=client_id,
                resource=resource,
                contract=contract,
                identity_opener=opener,
                identity_endpoint=identity_endpoint,
                identity_header=identity_header,
                epoch_timeline=epoch_timeline,
                timeout_seconds=_remaining_timeout(
                    timeline,
                    deadline=deadline,
                    maximum=contract["maximum_request_timeout_seconds"],
                ),
                deadline_factory=self._deadline_factory,
            )
        except PrivateEvidenceMaterialFailure:
            raise
        except Exception:
            raise PrivateEvidenceMaterialFailure(failure) from None

    def _read_blob(
        self,
        *,
        opener: _HttpOpener,
        token: str,
        selector: _BlobSelector,
        contract: Mapping[str, Any],
        timeline: _Timeline,
        deadline: float,
        failure: str,
    ) -> bytes:
        request = urllib.request.Request(
            selector.uri,
            headers={
                "Accept": selector.media_type,
                "Accept-Encoding": contract["content_encoding"],
                "Authorization": f"Bearer {token}",
                "x-ms-version": contract["service_api_version"],
            },
            method=contract["method"],
        )
        try:
            status, content_types, body = runtime_ingress._open_exact(
                opener,
                request,
                expected_uri=selector.uri,
                maximum_bytes=selector.maximum_bytes,
                timeout_seconds=_remaining_timeout(
                    timeline,
                    deadline=deadline,
                    maximum=contract["maximum_request_timeout_seconds"],
                ),
                deadline_factory=self._deadline_factory,
            )
        except PrivateEvidenceMaterialFailure:
            raise
        except Exception:
            raise PrivateEvidenceMaterialFailure(failure) from None
        if (
            status != contract["successful_status"]
            or not runtime_ingress._media_type_matches(
                content_types, selector.media_type
            )
            or not body
            or _digest(body) != selector.sha256
        ):
            _fail(failure)
        return body

    def _read_hmac_key(
        self,
        *,
        opener: _HttpOpener,
        token: str,
        selector: _SecretSelector,
        secret_source: Mapping[str, Any],
        contract: Mapping[str, Any],
        timeline: _Timeline,
        deadline: float,
    ) -> bytes:
        try:
            launch_binding._versioned_secret(
                selector.uri,
                host=secret_source["vault_host"],
                version=selector.version,
                code="evidence_ingress.hmac_secret_invalid",
            )
            uri = f"{selector.uri}?{urllib.parse.urlencode({'api-version': contract['api_version']})}"
            request = urllib.request.Request(
                uri,
                headers={
                    "Accept": contract["response_media_type"],
                    "Accept-Encoding": contract["content_encoding"],
                    "Authorization": f"Bearer {token}",
                },
                method=contract["method"],
            )
            status, content_types, raw = runtime_ingress._open_exact(
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
            response = runtime_ingress._json_object(
                raw, "evidence_ingress.hmac_secret_invalid"
            )
        except PrivateEvidenceMaterialFailure:
            raise
        except Exception:
            raise PrivateEvidenceMaterialFailure(
                "evidence_ingress.hmac_secret_invalid"
            ) from None
        encoded = response.get("value")
        attributes = response.get("attributes")
        required = set(contract["response_required_fields"])
        if (
            status != contract["successful_status"]
            or not runtime_ingress._media_type_matches(
                content_types, contract["response_media_type"]
            )
            or not required <= set(response)
            or response.get("id") != selector.uri
            or type(encoded) is not str
            or not encoded
            or "=" in encoded
            or not encoded.isascii()
            or not isinstance(attributes, Mapping)
            or attributes.get("enabled") is not True
        ):
            _fail("evidence_ingress.hmac_secret_invalid")
        try:
            padding = "=" * (-len(encoded) % 4)
            decoded = base64.b64decode(encoded + padding, altchars=b"-_", validate=True)
            canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
        except (ValueError, binascii.Error, UnicodeDecodeError):
            raise PrivateEvidenceMaterialFailure(
                "evidence_ingress.hmac_secret_invalid"
            ) from None
        if (
            canonical != encoded
            or not selector.minimum_bytes <= len(decoded) <= selector.maximum_bytes
            or _digest(decoded) != selector.material_sha256
        ):
            _fail("evidence_ingress.hmac_secret_invalid")
        return bytes(decoded)


def build_private_evidence_material_reader(
    policy: Any,
    *,
    clock: Callable[[], datetime],
) -> ManagedIdentityPrivateEvidenceMaterialReader:
    """Build the dormant concrete reader without wiring or executing it."""

    try:
        document = runtime_binding.validate_runtime_binding_policy(
            policy, executable=True
        )
        launch_binding.validate_launch_binding_policy(document)
        if (
            document["evidence_ingress"]["private_restore_evidence_container"][
                "reader_status"
            ]
            != "dormant_implemented_unintegrated"
        ):
            _fail("evidence_ingress.configuration_invalid")
        return ManagedIdentityPrivateEvidenceMaterialReader(clock=clock)
    except PrivateEvidenceMaterialFailure:
        raise
    except Exception:
        raise PrivateEvidenceMaterialFailure(
            "evidence_ingress.configuration_invalid"
        ) from None
