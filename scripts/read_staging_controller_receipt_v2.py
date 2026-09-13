#!/usr/bin/env python3
"""Dormant exact-chain reader with narrow private-WORM source authentication."""

from __future__ import annotations

import base64
import hashlib
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, NoReturn, Protocol

try:
    from scripts import read_staging_runtime_record_material as runtime_ingress
    from scripts import staging_restore_runtime_adapters as runtime_adapters
    from scripts import staging_restore_safe_submit as safe_submit
    from scripts import validate_staging_controller_receipt_v2 as content_contract
    from scripts import validate_staging_restore_launch_binding as launch_binding
    from scripts import validate_staging_restore_runtime_binding as runtime_binding
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    import read_staging_runtime_record_material as runtime_ingress
    import staging_restore_runtime_adapters as runtime_adapters
    import staging_restore_safe_submit as safe_submit
    import validate_staging_controller_receipt_v2 as content_contract
    import validate_staging_restore_launch_binding as launch_binding
    import validate_staging_restore_runtime_binding as runtime_binding


ACTIONS = frozenset({"provision", "cleanup"})
RECORD_KINDS = ("intent", "submission", "controller_receipt")
ETAG_RE = re.compile(r'^"[^"\r\n]+"$')
_SOURCE_AUTHENTICATION_SEAL = object()
_WORM_OBJECT_SEAL = object()


class PrivateControllerReceiptV2Failure(RuntimeError):
    """Bounded refusal without credentials, URIs, metadata, or record bytes."""


def _fail(code: str) -> NoReturn:
    raise PrivateControllerReceiptV2Failure(code) from None


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


class _HttpOpener(Protocol):
    def open(self, request: Any, *, timeout: int) -> Any: ...


@dataclass(frozen=True, slots=True)
class _AuthoritySnapshot:
    action: str
    rehearsal_id: str
    execution_name: str
    execution_binding: str
    reader_identity_resource_id: str
    reader_identity_client_id: str
    broker_launch_receipt_sha256: str
    launch_record_sha256: str
    launch_expires_at: datetime
    authenticated_receipt_observed_at: datetime
    launch_source: tuple[str, str, str]
    object_uris: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True, init=False)
class _AuthenticatedPrivateWormObject:
    kind: str
    uri: str
    body: bytes = dataclass_field(repr=False)
    sha256: str
    content_md5: str
    etag: str
    last_modified: str
    retained_until: str
    worm_evidence: tuple[str, str, str, str, str]
    _proof_sha256: str = dataclass_field(repr=False, compare=False)
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("controller_receipt_ingress.record_unsealed")

    def _is_authenticated(self) -> bool:
        try:
            modified = content_contract._utc(self.last_modified)
            retained = content_contract._utc(self.retained_until)
            projection = {
                "kind": self.kind,
                "uri": self.uri,
                "sha256": self.sha256,
                "content_md5": self.content_md5,
                "etag": self.etag,
                "last_modified": self.last_modified,
                "retained_until": self.retained_until,
                "worm_evidence": self.worm_evidence,
            }
            return (
                type(self) is _AuthenticatedPrivateWormObject
                and self._seal is _WORM_OBJECT_SEAL
                and self.kind in RECORD_KINDS
                and type(self.uri) is str
                and self.uri.startswith("https://")
                and type(self.body) is bytes
                and type(self.sha256) is str
                and content_contract.DIGEST_RE.fullmatch(self.sha256) is not None
                and _digest(self.body) == self.sha256
                and type(self.content_md5) is str
                and self.content_md5
                == base64.b64encode(
                    hashlib.md5(self.body, usedforsecurity=False).digest()
                ).decode("ascii")
                and type(self.etag) is str
                and ETAG_RE.fullmatch(self.etag) is not None
                and type(self.last_modified) is str
                and type(self.retained_until) is str
                and retained - modified >= timedelta(days=90)
                and self.worm_evidence
                == ("locked", "true", "true", "BlockBlob", "2021-12-02")
                and self._proof_sha256
                == _digest(content_contract._canonical(projection))
            )
        except Exception:
            return False


def _placeholder_values(value: Any) -> tuple[str, ...]:
    found: list[str] = []
    if type(value) is str and content_contract.PLACEHOLDER_RE.fullmatch(value):
        found.append(value)
    elif type(value) is list:
        for item in value:
            found.extend(_placeholder_values(item))
    elif type(value) is dict:
        for item in value.values():
            found.extend(_placeholder_values(item))
    return tuple(found)


def _validate_policies(
    runtime_policy: Any, controller_policy: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        runtime = runtime_binding.validate_runtime_binding_policy(
            runtime_policy, executable=True
        )
        controller = content_contract.validate_controller_receipt_v2_policy(
            controller_policy
        )
        envelope, safe = safe_submit.validate_canonical_contracts()
    except Exception:
        _fail("controller_receipt_ingress.policy_invalid")
    source = controller["record_chain_source"]
    status = controller["status"]
    marker = runtime["private_control_marker_store"]
    jobs = runtime["identities"]["jobs"]
    fixed = controller["fixed_bindings"]
    upstream = controller["upstream_policies"]
    runtime_receipt = runtime["authenticated_controller_receipt"]
    expected_container_ids = {
        kind: (
            f"{source['account_resource_id']}/blobServices/default/containers/"
            f"{source['container_names'][kind]}"
        )
        for kind in RECORD_KINDS
    }
    account_name = source["account_resource_id"].rsplit("/", 1)[-1]
    if (
        _placeholder_values(controller)
        or status["private_worm_reader_implemented"] is not True
        or status["content_validator_implemented"] is not True
        or status["controller_receipt_producer_implemented"] is not False
        or status["controller_observation_authorities_implemented"] is not False
        or status["runtime_consumer_integrated"] is not False
        or status["workflow_integrated"] is not False
        or status["executable"] is not False
        or upstream["safe_submit"]["policy_id"] != safe["policy_id"]
        or upstream["safe_submit"]["sha256"]
        != f"sha256:{safe_submit.SAFE_SUBMIT_POLICY_SHA256}"
        or upstream["restore_envelope"]["policy_id"] != envelope["policy_id"]
        or upstream["restore_envelope"]["sha256"]
        != f"sha256:{safe_submit.ENVELOPE_POLICY_SHA256}"
        or runtime_receipt["content_contract_policy_id"] != controller["policy_id"]
        or runtime_receipt["content_contract_policy_sha256"]
        != f"sha256:{content_contract.POLICY_SHA256}"
        or runtime_receipt["source_policy_id"] != safe["policy_id"]
        or source["reader_identity_resource_ids"]
        != {action: jobs[action]["resource_id"] for action in ACTIONS}
        or source["reader_identity_client_ids"]
        != {action: jobs[action]["client_id"] for action in ACTIONS}
        or source["writer_identity_resource_id"] != jobs["provision"]["resource_id"]
        or fixed["provision_job_resource_id"]
        != runtime["runtime"]["jobs"]["provision"]["resource_id"]
        or fixed["provision_job_uami_client_id"] != jobs["provision"]["client_id"]
        or fixed["provision_job_uami_principal_id"] != jobs["provision"]["principal_id"]
        or source["account_resource_id"] != marker["account_resource_id"]
        or content_contract._resource_type(source["account_resource_id"])
        != "microsoft.storage/storageaccounts"
        or source["account_host"] != marker["account_host"]
        or source["account_host"] != f"{account_name}.blob.core.windows.net"
        or source["oauth_resource"] != marker["oauth_resource"]
        or source["private_endpoint_resource_id"]
        != marker["private_endpoint_resource_id"]
        or any(
            source["container_names"][kind] != marker["container_names"][kind]
            or source["container_resource_ids"][kind]
            != marker["container_resource_ids"][kind]
            for kind in ("intent", "submission")
        )
        or source["container_resource_ids"] != expected_container_ids
        or source["controller_immutability_policy_resource_id"]
        != f"{expected_container_ids['controller_receipt']}/immutabilityPolicies/default"
        or len(set(source["container_resource_ids"].values())) != len(RECORD_KINDS)
        or len(set(source["reader_role_assignment_ids"].values())) != 2
        or source["writer_role_assignment_id"]
        in set(source["reader_role_assignment_ids"].values())
    ):
        _fail("controller_receipt_ingress.policy_invalid")
    return runtime, controller


def _canonical_launch_receipt(
    runtime: Mapping[str, Any], receipt: launch_binding.AuthenticatedBrokerLaunchReceipt
) -> Mapping[str, Any]:
    contract = runtime["launch_binding"]["launch_receipt"]
    value = launch_binding._parse(
        receipt.receipt_bytes,
        maximum=contract["maximum_bytes"],
        code="controller_receipt_ingress.authority_invalid",
    )
    launch_binding._mapping(
        value,
        set(contract["required_fields"]),
        "controller_receipt_ingress.authority_invalid",
    )
    return value


def _authority_snapshot(
    runtime: Mapping[str, Any],
    controller: Mapping[str, Any],
    authenticated_receipt: Any,
    launch_record: Any,
) -> _AuthoritySnapshot:
    try:
        if (
            type(authenticated_receipt)
            is not launch_binding.AuthenticatedBrokerLaunchReceipt
            or not authenticated_receipt._is_authenticated()
            or type(authenticated_receipt.receipt_bytes) is not bytes
            or _digest(authenticated_receipt.receipt_bytes)
            != authenticated_receipt.receipt_sha256
            or authenticated_receipt.action not in ACTIONS
            or type(launch_record) is not launch_binding.ValidatedLaunchRecord
            or not launch_record._is_validated()
            or launch_record.action != authenticated_receipt.action
            or type(launch_record.record_bytes) is not bytes
            or _digest(launch_record.record_bytes) != launch_record.record_sha256
            or launch_record.record_uri != authenticated_receipt.launch_record_uri
            or launch_record.record_sha256 != authenticated_receipt.launch_record_sha256
            or type(launch_record.rehearsal_id) is not str
            or content_contract.REHEARSAL_RE.fullmatch(launch_record.rehearsal_id)
            is None
            or (
                launch_record.action == "cleanup"
                and launch_record.cleanup_mode != "selected_rehearsal"
            )
        ):
            _fail("controller_receipt_ingress.authority_invalid")
        reviewed = launch_binding.validate_launch_record(
            runtime,
            launch_record.record_bytes,
            record_sha256=launch_record.record_sha256,
            record_uri=launch_record.record_uri,
        )
        if not reviewed._is_validated():
            _fail("controller_receipt_ingress.authority_invalid")
        launch_record = reviewed
        signed = _canonical_launch_receipt(runtime, authenticated_receipt)
        action = authenticated_receipt.action
        execution_name = authenticated_receipt.execution_name
        job = runtime["runtime"]["jobs"][action]
        identity = runtime["identities"]["jobs"][action]
        execution_binding = f"{job['resource_id']}/executions/{execution_name}"
        receipt_uri, signature_uri = launch_binding._execution_receipt_uris(
            dict(runtime), action, execution_name
        )
        reference = {
            field: signed[f"runtime_{field}"]
            for field in runtime_binding.RUNTIME_REFERENCE_FIELDS
        }
        observed_at = launch_binding._exact_time(
            authenticated_receipt.observed_at,
            "controller_receipt_ingress.authority_invalid",
        )
        signed_observed_at = launch_binding._time(
            signed["observed_at"], "controller_receipt_ingress.authority_invalid"
        )
        if (
            authenticated_receipt.receipt_bytes != launch_binding._canonical(signed)
            or signed["action"] != action
            or signed["selected_job_resource_id"] != job["resource_id"]
            or signed["execution_resource_id"] != execution_binding
            or signed["launch_record_uri"] != launch_record.record_uri
            or signed["launch_record_sha256"] != launch_record.record_sha256
            or signed["runtime_record_sha256"] != launch_record.runtime_record_sha256
            or signed["provision_job_projection_sha256"]
            != runtime["runtime"]["jobs"]["provision"]["configuration_template_sha256"]
            or not launch_record.created_at
            <= signed_observed_at
            <= observed_at
            < launch_record.expires_at
            or authenticated_receipt.action != action
            or authenticated_receipt.execution_name != execution_name
            or authenticated_receipt.launch_record_uri != launch_record.record_uri
            or authenticated_receipt.launch_record_sha256 != launch_record.record_sha256
            or authenticated_receipt.receipt_uri != receipt_uri
            or authenticated_receipt.signature_envelope_uri != signature_uri
            or runtime_binding.DIGEST_RE.fullmatch(
                authenticated_receipt.signature_envelope_sha256
            )
            is None
            or not isinstance(authenticated_receipt.runtime_reference, Mapping)
            or set(authenticated_receipt.runtime_reference) != set(reference)
            or any(
                authenticated_receipt.runtime_reference[field] != value
                for field, value in reference.items()
            )
            or controller["record_chain_source"]["reader_identity_resource_ids"][action]
            != identity["resource_id"]
            or controller["record_chain_source"]["reader_identity_client_ids"][action]
            != identity["client_id"]
        ):
            _fail("controller_receipt_ingress.authority_invalid")
        source = controller["record_chain_source"]
        rehearsal_id = launch_record.rehearsal_id
        uris = tuple(
            (
                kind,
                f"https://{source['account_host']}/"
                f"{source['container_names'][kind]}/"
                f"{source['blob_name_templates'][kind].format(rehearsal_id=rehearsal_id)}",
            )
            for kind in source["record_order"]
        )
        for kind, uri in uris:
            parsed = urllib.parse.urlsplit(uri)
            if (
                kind not in RECORD_KINDS
                or parsed.scheme != "https"
                or parsed.hostname != source["account_host"]
                or parsed.netloc != source["account_host"]
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port is not None
                or parsed.path
                != f"/{source['container_names'][kind]}/{rehearsal_id}.json"
                or parsed.query
                or parsed.fragment
            ):
                _fail("controller_receipt_ingress.authority_invalid")
        return _AuthoritySnapshot(
            action=action,
            rehearsal_id=rehearsal_id,
            execution_name=execution_name,
            execution_binding=execution_binding,
            reader_identity_resource_id=identity["resource_id"],
            reader_identity_client_id=identity["client_id"],
            broker_launch_receipt_sha256=authenticated_receipt.receipt_sha256,
            launch_record_sha256=launch_record.record_sha256,
            launch_expires_at=launch_record.expires_at,
            authenticated_receipt_observed_at=observed_at,
            launch_source=(
                launch_record.workflow_sha,
                launch_record.run_id,
                launch_record.run_attempt,
            ),
            object_uris=uris,
        )
    except PrivateControllerReceiptV2Failure:
        raise
    except Exception:
        _fail("controller_receipt_ingress.authority_invalid")


class _Timeline:
    __slots__ = ("_last", "_source")

    def __init__(self, source: Callable[[], float]) -> None:
        if not callable(source):
            _fail("controller_receipt_ingress.configuration_invalid")
        self._source = source
        self._last: float | None = None

    def read(self) -> float:
        try:
            value = self._source()
        except Exception:
            _fail("controller_receipt_ingress.clock_invalid")
        if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
            _fail("controller_receipt_ingress.clock_invalid")
        current = float(value)
        if self._last is not None and current < self._last:
            _fail("controller_receipt_ingress.clock_invalid")
        self._last = current
        return current


class _TrustedUtcTimeline:
    __slots__ = ("_last", "_source")

    def __init__(self, source: Callable[[], datetime]) -> None:
        if not callable(source):
            _fail("controller_receipt_ingress.configuration_invalid")
        self._source = source
        self._last: datetime | None = None

    def read(self) -> datetime:
        try:
            value = self._source()
        except Exception:
            _fail("controller_receipt_ingress.trusted_clock_invalid")
        if (
            type(value) is not datetime
            or value.tzinfo is None
            or value.utcoffset() != timedelta(0)
            or value.microsecond != 0
        ):
            _fail("controller_receipt_ingress.trusted_clock_invalid")
        current = value.astimezone(UTC)
        if self._last is not None and current < self._last:
            _fail("controller_receipt_ingress.trusted_clock_regressed")
        self._last = current
        return current


def _remaining_timeout(timeline: _Timeline, *, deadline: float, maximum: int) -> int:
    remaining = math.floor(deadline - timeline.read())
    if remaining < 1:
        _fail("controller_receipt_ingress.deadline")
    return min(maximum, remaining)


def _header_values(headers: Any, name: str) -> tuple[str, ...]:
    try:
        raw = headers.get_all(name) if hasattr(headers, "get_all") else None
        if raw is None:
            value = headers.get(name)
            raw = [] if value is None else [value]
        values = tuple(raw)
    except Exception:
        _fail("controller_receipt_ingress.record_invalid")
    if any(
        type(value) is not str
        or not value
        or len(value) > 512
        or "\r" in value
        or "\n" in value
        for value in values
    ):
        _fail("controller_receipt_ingress.record_invalid")
    return values


def _http_date(value: str) -> datetime:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        _fail("controller_receipt_ingress.record_invalid")
    if parsed.tzinfo is None or parsed.microsecond != 0:
        _fail("controller_receipt_ingress.record_invalid")
    return parsed.astimezone(UTC)


def _metadata_digest(
    objects: tuple[_AuthenticatedPrivateWormObject, ...],
) -> str:
    return _digest(
        content_contract._canonical(
            [
                {
                    "kind": item.kind,
                    "uri": item.uri,
                    "sha256": item.sha256,
                    "content_md5": item.content_md5,
                    "etag": item.etag,
                    "last_modified": item.last_modified,
                    "retained_until": item.retained_until,
                    "worm_evidence": item.worm_evidence,
                }
                for item in objects
            ]
        )
    )


def _content_snapshot(
    content: content_contract.ValidatedControllerReceiptV2Content,
) -> dict[str, Any]:
    return {
        "intent_sha256": content.intent_sha256,
        "submission_sha256": content.submission_sha256,
        "receipt_sha256": content.receipt_sha256,
        "policy_binding_sha256": content.policy_binding_sha256,
        **{name: getattr(content, name) for name in content_contract.RUNTIME_FIELDS},
        "expires_at": content.expires_at,
        "cleanup_not_after": content.cleanup_not_after,
        "provenance_authenticated": content.provenance_authenticated,
    }


def _carrier_digest(
    *,
    content: content_contract.ValidatedControllerReceiptV2Content,
    authority: _AuthoritySnapshot,
    validated_at: datetime,
    objects: tuple[_AuthenticatedPrivateWormObject, ...],
) -> str:
    return _digest(
        content_contract._canonical(
            {
                "action": authority.action,
                "broker_launch_receipt_sha256": authority.broker_launch_receipt_sha256,
                "content": _content_snapshot(content),
                "execution_binding": authority.execution_binding,
                "launch_record_sha256": authority.launch_record_sha256,
                "metadata_sha256": _metadata_digest(objects),
                "provider_facts_authenticated": False,
                "record_source_authenticated": True,
                "rehearsal_id": authority.rehearsal_id,
                "runtime_authority": False,
                "writer_identity_or_private_route_proven": False,
                "validated_at": validated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
    )


@dataclass(frozen=True, slots=True, init=False, repr=False)
class AuthenticatedControllerReceiptV2RecordChainSource:
    """Exact content with authenticated WORM provenance, but no provider truth."""

    validated_content: content_contract.ValidatedControllerReceiptV2Content = (
        dataclass_field(repr=False)
    )
    action: str
    rehearsal_id: str
    execution_binding: str
    broker_launch_receipt_sha256: str
    launch_record_sha256: str
    validated_at: datetime
    record_source_authenticated: bool
    provider_facts_authenticated: bool
    runtime_authority: bool
    writer_identity_or_private_route_proven: bool
    _objects: tuple[_AuthenticatedPrivateWormObject, ...] = dataclass_field(
        repr=False, compare=False
    )
    _carrier_sha256: str = dataclass_field(repr=False, compare=False)
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("controller_receipt_ingress.result_unsealed")

    def _is_authenticated_for(
        self,
        *,
        runtime_policy: Any,
        controller_policy: Any,
        authenticated_receipt: launch_binding.AuthenticatedBrokerLaunchReceipt,
        launch_record: launch_binding.ValidatedLaunchRecord,
    ) -> bool:
        try:
            runtime, controller = _validate_policies(runtime_policy, controller_policy)
            authority = _authority_snapshot(
                runtime, controller, authenticated_receipt, launch_record
            )
            refreshed = content_contract._revalidate_controller_receipt_v2_content(
                self.validated_content, now=self.validated_at
            )
            return (
                type(self) is AuthenticatedControllerReceiptV2RecordChainSource
                and self._seal is _SOURCE_AUTHENTICATION_SEAL
                and self.action == authority.action
                and self.rehearsal_id == authority.rehearsal_id
                and self.execution_binding == authority.execution_binding
                and self.broker_launch_receipt_sha256
                == authority.broker_launch_receipt_sha256
                and self.launch_record_sha256 == authority.launch_record_sha256
                and os.environ.get(launch_binding.EXECUTION_NAME_ENV)
                == authority.execution_name
                and type(self.validated_at) is datetime
                and self.validated_at.tzinfo is not None
                and self.validated_at.utcoffset() == timedelta(0)
                and self.validated_at.microsecond == 0
                and content_contract._canonical(_content_snapshot(refreshed))
                == content_contract._canonical(
                    _content_snapshot(self.validated_content)
                )
                and self.record_source_authenticated is True
                and self.provider_facts_authenticated is False
                and self.runtime_authority is False
                and self.writer_identity_or_private_route_proven is False
                and all(item._is_authenticated() for item in self._objects)
                and tuple(item.kind for item in self._objects) == RECORD_KINDS
                and tuple(item.uri for item in self._objects)
                == tuple(uri for _kind, uri in authority.object_uris)
                and tuple(item.sha256 for item in self._objects)
                == (
                    refreshed.intent_sha256,
                    refreshed.submission_sha256,
                    refreshed.receipt_sha256,
                )
                and self._carrier_sha256
                == _carrier_digest(
                    content=refreshed,
                    authority=authority,
                    validated_at=self.validated_at,
                    objects=self._objects,
                )
                and _content_matches_authority(
                    refreshed, authority, authenticated_receipt
                )
            )
        except Exception:
            return False


def _content_matches_authority(
    content: content_contract.ValidatedControllerReceiptV2Content,
    authority: _AuthoritySnapshot,
    authenticated_receipt: launch_binding.AuthenticatedBrokerLaunchReceipt,
) -> bool:
    try:
        receipt = content_contract._parse_canonical(
            content.receipt_bytes, content_contract.MAXIMUM_POLICY_BYTES
        )
        if receipt["authorization"]["rehearsal_id"] != authority.rehearsal_id:
            return False
        if authority.action == "cleanup":
            return True
        if (
            tuple(
                receipt["source"][name]
                for name in ("workflow_sha", "run_id", "run_attempt")
            )
            != authority.launch_source
        ):
            return False
        signed = launch_binding._parse(
            authenticated_receipt.receipt_bytes,
            maximum=65_536,
            code="controller_receipt_ingress.authority_invalid",
        )
        expected = {
            "launch_record_sha256": authority.launch_record_sha256,
            "broker_launch_receipt_sha256": authority.broker_launch_receipt_sha256,
            "runtime_record_sha256": signed["runtime_record_sha256"],
            "runtime_signature_bundle_sha256": signed[
                "runtime_signature_bundle_sha256"
            ],
            "execution_resource_id": authority.execution_binding,
            "execution_name": authority.execution_name,
            "launch_expires_at": authority.launch_expires_at.strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "provision_job_configuration_template_sha256": signed[
                "provision_job_projection_sha256"
            ],
            "provision_job_image_reference": signed["provision_job_image_reference"],
        }
        return receipt["launch"] == expected
    except Exception:
        return False


def _read_worm_object(
    *,
    opener: _HttpOpener,
    token: str,
    kind: str,
    uri: str,
    source: Mapping[str, Any],
    now: datetime,
    maximum_clock_skew_seconds: int,
    timeout_seconds: int,
    deadline_factory: Callable[[int], Any],
) -> _AuthenticatedPrivateWormObject:
    retrieval = source["retrieval"]
    request = urllib.request.Request(
        uri,
        headers={
            "Accept": retrieval["record_media_type"],
            "Accept-Encoding": retrieval["content_encoding"],
            "Authorization": f"Bearer {token}",
            "x-ms-version": retrieval["service_api_version"],
        },
        method=retrieval["method"],
    )
    try:
        with deadline_factory(timeout_seconds):
            try:
                response = opener.open(request, timeout=timeout_seconds)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                status = getattr(response, "status", getattr(response, "code", None))
                final_uri = response.geturl()
                headers = response.headers
                content_types = _header_values(headers, "Content-Type")
                encodings = _header_values(headers, "Content-Encoding")
                lengths = _header_values(headers, "Content-Length")
                ranges = _header_values(headers, "Content-Range")
                transfers = _header_values(headers, "Transfer-Encoding")
                md5s = _header_values(headers, "Content-MD5")
                etags = _header_values(headers, "ETag")
                modified = _header_values(headers, "Last-Modified")
                retained = _header_values(
                    headers, "x-ms-immutability-policy-until-date"
                )
                modes = _header_values(headers, "x-ms-immutability-policy-mode")
                holds = _header_values(headers, "x-ms-legal-hold")
                encrypted = _header_values(headers, "x-ms-server-encrypted")
                blob_types = _header_values(headers, "x-ms-blob-type")
                versions = _header_values(headers, "x-ms-version")
                if (
                    type(status) is not int
                    or status != retrieval["successful_status"]
                    or final_uri != uri
                    or not runtime_ingress._media_type_matches(
                        content_types, retrieval["record_media_type"]
                    )
                    or len(encodings) > 1
                    or (
                        encodings
                        and encodings[0].strip().lower()
                        != retrieval["content_encoding"]
                    )
                    or len(lengths) != 1
                    or ranges
                    or transfers
                    or any(
                        len(values) != 1
                        for values in (
                            md5s,
                            etags,
                            modified,
                            retained,
                            modes,
                            holds,
                            encrypted,
                            blob_types,
                            versions,
                        )
                    )
                ):
                    _fail("controller_receipt_ingress.record_invalid")
                length = lengths[0]
                if (
                    not length.isascii()
                    or not length.isdigit()
                    or length != str(int(length))
                    or not 1 <= int(length) <= source["maximum_record_bytes"]
                ):
                    _fail("controller_receipt_ingress.record_invalid")
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = response.read(source["maximum_record_bytes"] + 1 - total)
                    if type(chunk) is not bytes:
                        _fail("controller_receipt_ingress.record_invalid")
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > source["maximum_record_bytes"]:
                        _fail("controller_receipt_ingress.record_invalid")
                body = b"".join(chunks)
        if int(length) != len(body):
            _fail("controller_receipt_ingress.record_invalid")
        expected_md5 = base64.b64encode(
            hashlib.md5(body, usedforsecurity=False).digest()
        ).decode("ascii")
        try:
            decoded_md5 = base64.b64decode(md5s[0], validate=True)
        except (ValueError, TypeError):
            _fail("controller_receipt_ingress.record_invalid")
        last_modified = _http_date(modified[0])
        retained_until = _http_date(retained[0])
        if (
            md5s[0] != expected_md5
            or len(decoded_md5) != 16
            or not ETAG_RE.fullmatch(etags[0])
            or modes[0].casefold()
            != retrieval["immutability_policy_mode_required"].casefold()
            or holds[0].casefold() != "true"
            or encrypted[0].casefold() != "true"
            or blob_types[0] != retrieval["blob_type_required"]
            or versions[0] != retrieval["service_api_version"]
            or last_modified > now + timedelta(seconds=maximum_clock_skew_seconds)
            or retained_until - last_modified
            < timedelta(days=retrieval["minimum_retention_days"])
        ):
            _fail("controller_receipt_ingress.record_invalid")
        projection = {
            "kind": kind,
            "uri": uri,
            "sha256": _digest(body),
            "content_md5": md5s[0],
            "etag": etags[0],
            "last_modified": last_modified.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "retained_until": retained_until.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "worm_evidence": (
                modes[0].casefold(),
                holds[0].casefold(),
                encrypted[0].casefold(),
                blob_types[0],
                versions[0],
            ),
        }
        authenticated = object.__new__(_AuthenticatedPrivateWormObject)
        for name, value in {**projection, "body": bytes(body)}.items():
            object.__setattr__(authenticated, name, value)
        object.__setattr__(
            authenticated,
            "_proof_sha256",
            _digest(content_contract._canonical(projection)),
        )
        object.__setattr__(authenticated, "_seal", _WORM_OBJECT_SEAL)
        if not authenticated._is_authenticated():
            _fail("controller_receipt_ingress.record_invalid")
        return authenticated
    except PrivateControllerReceiptV2Failure:
        raise
    except Exception:
        _fail("controller_receipt_ingress.record_invalid")


class ManagedIdentityPrivateControllerReceiptV2Reader:
    """One-shot exact-chain reader with authenticated private-WORM provenance."""

    __slots__ = (
        "_blob_opener",
        "_clock",
        "_controller_policy",
        "_deadline_factory",
        "_epoch_clock",
        "_identity_opener",
        "_lock",
        "_monotonic",
        "_runtime_policy",
        "_used",
    )

    def __init__(
        self,
        *,
        runtime_policy: Any,
        controller_policy: Any,
        clock: Callable[[], datetime],
        identity_opener: _HttpOpener | None = None,
        blob_opener: _HttpOpener | None = None,
        epoch_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        deadline_factory: Callable[[int], Any] = runtime_adapters._wall_clock_deadline,
    ) -> None:
        if (
            not callable(clock)
            or any(
                opener is not None and not callable(getattr(opener, "open", None))
                for opener in (identity_opener, blob_opener)
            )
            or not callable(epoch_clock)
            or not callable(monotonic)
            or not callable(deadline_factory)
        ):
            _fail("controller_receipt_ingress.configuration_invalid")
        runtime, controller = _validate_policies(runtime_policy, controller_policy)
        self._runtime_policy = runtime_binding._canonical(runtime)
        self._controller_policy = content_contract._canonical(controller)
        self._clock = clock
        self._identity_opener = identity_opener
        self._blob_opener = blob_opener
        self._epoch_clock = epoch_clock
        self._monotonic = monotonic
        self._deadline_factory = deadline_factory
        self._lock = threading.Lock()
        self._used = False

    def _claim_use(self) -> None:
        with self._lock:
            if self._used:
                _fail("controller_receipt_ingress.already_used")
            self._used = True

    def _validated_policies(self) -> tuple[dict[str, Any], dict[str, Any]]:
        return _validate_policies(
            runtime_binding._parse_policy(self._runtime_policy),
            content_contract._parse_canonical(
                self._controller_policy, content_contract.MAXIMUM_POLICY_BYTES
            ),
        )

    def _checkpoint(
        self,
        authenticated_receipt: launch_binding.AuthenticatedBrokerLaunchReceipt,
        launch_record: launch_binding.ValidatedLaunchRecord,
        snapshot: _AuthoritySnapshot,
        trusted_utc: _TrustedUtcTimeline,
        timeline: _Timeline,
        deadline: float,
    ) -> datetime:
        runtime, controller = self._validated_policies()
        if (
            _authority_snapshot(
                runtime, controller, authenticated_receipt, launch_record
            )
            != snapshot
        ):
            _fail("controller_receipt_ingress.authority_invalid")
        if os.environ.get(launch_binding.EXECUTION_NAME_ENV) != snapshot.execution_name:
            _fail("controller_receipt_ingress.execution_identity_invalid")
        now = trusted_utc.read()
        if now < snapshot.authenticated_receipt_observed_at:
            _fail("controller_receipt_ingress.trusted_clock_invalid")
        if timeline.read() > deadline:
            _fail("controller_receipt_ingress.deadline")
        return now

    def read_and_authenticate(
        self,
        *,
        authenticated_receipt: launch_binding.AuthenticatedBrokerLaunchReceipt,
        launch_record: launch_binding.ValidatedLaunchRecord,
    ) -> AuthenticatedControllerReceiptV2RecordChainSource:
        """Read and authenticate one exact chain without caller selectors."""

        self._claim_use()
        runtime, controller = self._validated_policies()
        snapshot = _authority_snapshot(
            runtime, controller, authenticated_receipt, launch_record
        )
        source = controller["record_chain_source"]
        retrieval = source["retrieval"]
        timeline = _Timeline(self._monotonic)
        trusted_utc = _TrustedUtcTimeline(self._clock)
        deadline = timeline.read() + retrieval["maximum_elapsed_seconds"]
        identity_endpoint = os.environ.get("IDENTITY_ENDPOINT", "")
        identity_header = os.environ.get("IDENTITY_HEADER", "")
        try:
            identity_opener = (
                self._identity_opener or runtime_ingress._default_identity_opener()
            )
            blob_opener = self._blob_opener or runtime_ingress._default_service_opener(
                retrieval["runtime_ca_bundle_path"]
            )
            epoch_timeline = runtime_ingress._Timeline(
                self._epoch_clock, "controller_receipt_ingress.identity_clock_invalid"
            )
        except Exception:
            _fail("controller_receipt_ingress.configuration_invalid")

        def checkpoint() -> datetime:
            return self._checkpoint(
                authenticated_receipt,
                launch_record,
                snapshot,
                trusted_utc,
                timeline,
                deadline,
            )

        checkpoint()
        try:
            token = runtime_ingress._managed_identity_token(
                client_id=snapshot.reader_identity_client_id,
                resource=source["oauth_resource"],
                contract=retrieval,
                identity_opener=identity_opener,
                identity_endpoint=identity_endpoint,
                identity_header=identity_header,
                epoch_timeline=epoch_timeline,
                timeout_seconds=_remaining_timeout(
                    timeline,
                    deadline=deadline,
                    maximum=retrieval["maximum_request_timeout_seconds"],
                ),
                deadline_factory=self._deadline_factory,
            )
        except PrivateControllerReceiptV2Failure:
            raise
        except Exception:
            _fail("controller_receipt_ingress.identity_failed")
        checkpoint()
        records: dict[str, bytes] = {}
        objects: list[_AuthenticatedPrivateWormObject] = []
        for kind, uri in snapshot.object_uris:
            now = checkpoint()
            authenticated = _read_worm_object(
                opener=blob_opener,
                token=token,
                kind=kind,
                uri=uri,
                source=source,
                now=now,
                maximum_clock_skew_seconds=controller["freshness"][
                    "maximum_clock_skew_seconds"
                ],
                timeout_seconds=_remaining_timeout(
                    timeline,
                    deadline=deadline,
                    maximum=retrieval["maximum_request_timeout_seconds"],
                ),
                deadline_factory=self._deadline_factory,
            )
            records[kind] = authenticated.body
            objects.append(authenticated)
            checkpoint()
        validated_at = checkpoint()
        try:
            content = content_contract.validate_controller_receipt_v2_content(
                controller,
                intent_bytes=records["intent"],
                submission_bytes=records["submission"],
                receipt_bytes=records["controller_receipt"],
                clock=lambda: validated_at,
            )
            if snapshot.action == "provision":
                content = content_contract.require_fresh_controller_receipt_v2_content(
                    content, clock=lambda: validated_at
                )
        except Exception:
            _fail("controller_receipt_ingress.content_invalid")
        if not _content_matches_authority(content, snapshot, authenticated_receipt):
            _fail("controller_receipt_ingress.authority_invalid")
        object_tuple = tuple(objects)
        result = object.__new__(AuthenticatedControllerReceiptV2RecordChainSource)
        values = {
            "validated_content": content,
            "action": snapshot.action,
            "rehearsal_id": snapshot.rehearsal_id,
            "execution_binding": snapshot.execution_binding,
            "broker_launch_receipt_sha256": snapshot.broker_launch_receipt_sha256,
            "launch_record_sha256": snapshot.launch_record_sha256,
            "validated_at": validated_at,
            "record_source_authenticated": True,
            "provider_facts_authenticated": False,
            "runtime_authority": False,
            "writer_identity_or_private_route_proven": False,
            "_objects": object_tuple,
            "_carrier_sha256": _carrier_digest(
                content=content,
                authority=snapshot,
                validated_at=validated_at,
                objects=object_tuple,
            ),
        }
        for name, value in values.items():
            object.__setattr__(result, name, value)
        object.__setattr__(result, "_seal", _SOURCE_AUTHENTICATION_SEAL)
        checkpoint()
        if not result._is_authenticated_for(
            runtime_policy=runtime,
            controller_policy=controller,
            authenticated_receipt=authenticated_receipt,
            launch_record=launch_record,
        ):
            _fail("controller_receipt_ingress.result_invalid")
        return result


def build_private_controller_receipt_v2_reader(
    *,
    runtime_policy: Any,
    controller_policy: Any,
    clock: Callable[[], datetime],
) -> ManagedIdentityPrivateControllerReceiptV2Reader:
    """Build the dormant reader without wiring it into a runtime consumer."""

    return ManagedIdentityPrivateControllerReceiptV2Reader(
        runtime_policy=runtime_policy,
        controller_policy=controller_policy,
        clock=clock,
    )
