#!/usr/bin/env python3
"""Dormant one-shot private-WORM writer for a built controller receipt v2.

Only the sealed builder result selects content, identity, and destination.  A
successful result authenticates exact readback of this receipt in WORM storage;
it does not authenticate the Azure principal, network route, or broader provider
facts, and persisted receipt bytes do not carry the retained upstream authority.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import stat
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from typing import Any, NoReturn, Protocol

try:
    from scripts import build_staging_controller_receipt_v2 as builder
    from scripts import read_staging_runtime_record_material as runtime_ingress
    from scripts import validate_staging_controller_receipt_v2 as content
except ModuleNotFoundError:  # Direct `python scripts/...` import compatibility.
    import build_staging_controller_receipt_v2 as builder
    import read_staging_runtime_record_material as runtime_ingress
    import validate_staging_controller_receipt_v2 as content


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = (
    ROOT / "contracts/staging-rehearsal/v1/controller-receipt-v2-writer-policy.json"
)
POLICY_SHA256 = "21141564950aed5fea7d49e1c6b98f63b13e490b1380472aa24f071b2fdd44ed"
MAXIMUM_POLICY_BYTES = 64 * 1024
ETAG_RE = re.compile(r'^"[^"\r\n]+"$')
_RESULT_SEAL = object()
_FAILURE_CODES = frozenset(
    {
        "controller_receipt_writer.already_used",
        "controller_receipt_writer.authority_invalid",
        "controller_receipt_writer.clock_invalid",
        "controller_receipt_writer.clock_regressed",
        "controller_receipt_writer.configuration_invalid",
        "controller_receipt_writer.deadline",
        "controller_receipt_writer.expired",
        "controller_receipt_writer.identity_failed",
        "controller_receipt_writer.operation_failed",
        "controller_receipt_writer.policy_disabled",
        "controller_receipt_writer.policy_invalid",
        "controller_receipt_writer.readback_invalid",
        "controller_receipt_writer.result_invalid",
    }
)


class ControllerReceiptV2WriteFailure(RuntimeError):
    """Bounded refusal without credentials, URIs, record bytes, or provider bodies."""


def _fail(code: str) -> NoReturn:
    raise ControllerReceiptV2WriteFailure(code) from None


class _HttpOpener(Protocol):
    def open(self, request: Any, *, timeout: int) -> Any: ...


def _read_policy() -> dict[str, Any]:
    descriptor = -1
    try:
        before = POLICY_PATH.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(before.st_mode) & 0o022
            or not 1 <= before.st_size <= MAXIMUM_POLICY_BYTES
        ):
            _fail("controller_receipt_writer.policy_invalid")
        descriptor = os.open(
            POLICY_PATH,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        after = os.fstat(descriptor)
        raw = os.read(descriptor, MAXIMUM_POLICY_BYTES + 1)
        if (
            (before.st_dev, before.st_ino, before.st_size)
            != (after.st_dev, after.st_ino, after.st_size)
            or len(raw) != before.st_size
            or os.read(descriptor, 1)
            or hashlib.sha256(raw).hexdigest() != POLICY_SHA256
        ):
            _fail("controller_receipt_writer.policy_invalid")
    except ControllerReceiptV2WriteFailure:
        raise
    except OSError:
        _fail("controller_receipt_writer.policy_invalid")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        _fail("controller_receipt_writer.policy_invalid")
    if type(value) is not dict:
        _fail("controller_receipt_writer.policy_invalid")
    return value


def validate_controller_receipt_v2_writer_policy(
    value: Any, *, executable: bool = False
) -> dict[str, Any]:
    """Validate the exact digest-pinned dormant writer policy."""

    template = _read_policy()
    try:
        document = json.loads(content._canonical(value))
    except Exception:
        _fail("controller_receipt_writer.policy_invalid")
    activation = document.get("activation", {})
    transport = document.get("transport", {})
    provider = document.get("provider_contract", {})
    write = document.get("write", {})
    readback = document.get("readback", {})
    expected_pins = {
        "controller_receipt_v2": {
            "policy_id": "cosmaneura-staging-controller-receipt-v2",
            "sha256": f"sha256:{content.POLICY_SHA256}",
        },
        "controller_receipt_v2_builder": {
            "policy_id": "cosmaneura-staging-controller-receipt-v2-builder-v1",
            "sha256": f"sha256:{builder.POLICY_SHA256}",
        },
    }
    if (
        type(executable) is not bool
        or content._canonical(document) != content._canonical(template)
        or document.get("schema_version") != 1
        or type(document.get("schema_version")) is not int
        or document.get("policy_id")
        != "cosmaneura-staging-controller-receipt-v2-writer-v1"
        or document.get("closed_world") is not True
        or document.get("activation_ready") is not False
        or activation.get("controller_receipt_writer_implemented") is not True
        or any(
            value is not False
            for name, value in activation.items()
            if name != "controller_receipt_writer_implemented"
        )
        or document.get("upstream_policies") != expected_pins
        or document.get("input", {}).get("only_public_data_input") != "built_receipt"
        or document.get("input", {}).get(
            "caller_fields_digests_paths_policies_selectors_statuses_times_or_uris_permitted"
        )
        is not False
        or document.get("output", {}).get(
            "persisted_receipt_bytes_preserve_provider_provenance"
        )
        is not False
        or transport.get("maximum_storage_token_requests") != 1
        or transport.get("maximum_blob_put_requests") != 1
        or transport.get("maximum_blob_get_requests") != 1
        or transport.get("maximum_total_http_exchanges") != 3
        or transport.get("identity_opener_source") != "reviewed-private-factory-only"
        or transport.get("blob_opener_source") != "reviewed-private-factory-only"
        or any(
            transport.get(name) is not False
            for name in (
                "ambient_credentials_permitted",
                "ambient_proxy_permitted",
                "disk_materialization_permitted",
                "polling_permitted",
                "redirects_permitted",
                "retry_permitted",
            )
        )
        or write
        != {
            "blob_type": "BlockBlob",
            "conditional_header": {"If-None-Match": "*"},
            "content_md5_headers": ["Content-MD5", "x-ms-blob-content-md5"],
            "content_type": "application/json",
            "exact_canonical_receipt_bytes_required": True,
            "maximum_record_bytes": 65536,
            "method": "PUT",
            "request_query_parameters": [],
        }
        or provider.get("created", {}).get("status") != 201
        or provider.get("created", {}).get("accepted") is not True
        or provider.get("preexisting_exact") != {"accepted": False, "status_codes": []}
        or readback.get("successful_status") != 200
        or readback.get("minimum_retention_days") != 90
        or readback.get("exact_receipt_bytes_required") is not True
        or readback.get("last_modified_not_before_built_created_at_minus_clock_skew")
        is not True
        or readback.get("retained_until_must_be_future_at_validation") is not True
    ):
        _fail("controller_receipt_writer.policy_invalid")
    if executable:
        _fail("controller_receipt_writer.policy_disabled")
    return document


def load_controller_receipt_v2_writer_policy(
    *, executable: bool = False
) -> dict[str, Any]:
    return validate_controller_receipt_v2_writer_policy(
        _read_policy(), executable=executable
    )


@dataclass(frozen=True, slots=True)
class _AuthoritySnapshot:
    receipt_bytes: bytes = dataclass_field(repr=False)
    receipt_sha256: str
    record_chain_sha256: str
    authority_chain_sha256: str
    rehearsal_id: str
    created_at: datetime
    client_id: str = dataclass_field(repr=False)
    uri: str
    oauth_resource: str
    identity_contract_bytes: bytes = dataclass_field(repr=False)
    runtime_ca_bundle_path: str
    maximum_record_bytes: int
    maximum_clock_skew_seconds: int
    source_sha256: str


def _placeholders(value: Any) -> bool:
    if type(value) is str:
        return content.PLACEHOLDER_RE.fullmatch(value) is not None
    if type(value) is list:
        return any(_placeholders(item) for item in value)
    if type(value) is dict:
        return any(_placeholders(item) for item in value.values())
    return False


def _authority_snapshot(
    built_receipt: builder.BuiltControllerReceiptV2,
    policy: Mapping[str, Any],
) -> _AuthoritySnapshot:
    try:
        if (
            type(built_receipt) is not builder.BuiltControllerReceiptV2
            or not built_receipt._is_built()
        ):
            _fail("controller_receipt_writer.authority_invalid")
        production = built_receipt._isolation_inventory._database_observation._production_authority
        controller = content.validate_controller_receipt_v2_policy(
            content._parse_canonical(
                production._controller_policy, content.MAXIMUM_POLICY_BYTES
            )
        )
        source = controller["record_chain_source"]
        retrieval = source["retrieval"]
        receipt = content._parse_canonical(
            built_receipt.receipt_bytes, source["maximum_record_bytes"]
        )
        writer_identity = receipt["identity"]
        rehearsal_id = receipt["authorization"]["rehearsal_id"]
        container = source["container_names"]["controller_receipt"]
        name = source["blob_name_templates"]["controller_receipt"].format(
            rehearsal_id=rehearsal_id
        )
        uri = f"https://{source['account_host']}/{container}/{name}"
        parsed = urllib.parse.urlsplit(uri)
        account_name = source["account_resource_id"].rsplit("/", 1)[-1]
        expected_container_id = f"{source['account_resource_id']}/blobServices/default/containers/{container}"
        if (
            _placeholders(source)
            or policy["upstream_policies"]["controller_receipt_v2_builder"]["sha256"]
            != f"sha256:{builder.POLICY_SHA256}"
            or source["writer_identity_resource_id"]
            != writer_identity["provision_job_uami_resource_id"]
            or controller["fixed_bindings"]["provision_job_uami_client_id"]
            != writer_identity["provision_job_uami_client_id"]
            or controller["fixed_bindings"]["provision_job_resource_id"]
            != writer_identity["provision_job_resource_id"]
            or source["container_resource_ids"]["controller_receipt"]
            != expected_container_id
            or source["account_host"] != f"{account_name}.blob.core.windows.net"
            or source["oauth_resource"] != "https://storage.azure.com/"
            or source["service_version"] != policy["transport"]["service_api_version"]
            or source["content_type"] != policy["write"]["content_type"]
            or source["blob_type"] != policy["write"]["blob_type"]
            or source["create_if_absent_required"] is not True
            or source["overwrite_or_delete_permitted"] is not False
            or source["listing_permitted"] is not False
            or source["legal_hold_required"] is not True
            or source["server_encryption_required"] is not True
            or source["immutability_policy_mode"] != "Locked"
            or source["minimum_retention_days"]
            != policy["readback"]["minimum_retention_days"]
            or source["maximum_record_bytes"] != policy["write"]["maximum_record_bytes"]
            or retrieval["managed_identity_api_version"] != "2019-08-01"
            or retrieval["maximum_storage_token_requests"] != 1
            or retrieval["maximum_request_timeout_seconds"]
            != policy["transport"]["maximum_request_timeout_seconds"]
            or retrieval["runtime_ca_bundle_path"] == ""
            or parsed.scheme != "https"
            or parsed.hostname != source["account_host"]
            or parsed.netloc != source["account_host"]
            or parsed.path != f"/{container}/{rehearsal_id}.json"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.query
            or parsed.fragment
            or len(built_receipt.receipt_bytes) > source["maximum_record_bytes"]
            or content._canonical(receipt) != built_receipt.receipt_bytes
            or built_receipt.receipt_sha256
            != content._digest(built_receipt.receipt_bytes)
        ):
            _fail("controller_receipt_writer.authority_invalid")
        return _AuthoritySnapshot(
            receipt_bytes=bytes(built_receipt.receipt_bytes),
            receipt_sha256=built_receipt.receipt_sha256,
            record_chain_sha256=built_receipt.record_chain_sha256,
            authority_chain_sha256=built_receipt.authority_chain_sha256,
            rehearsal_id=rehearsal_id,
            created_at=built_receipt.created_at,
            client_id=writer_identity["provision_job_uami_client_id"],
            uri=uri,
            oauth_resource=source["oauth_resource"],
            identity_contract_bytes=content._canonical(retrieval),
            runtime_ca_bundle_path=retrieval["runtime_ca_bundle_path"],
            maximum_record_bytes=source["maximum_record_bytes"],
            maximum_clock_skew_seconds=controller["freshness"][
                "maximum_clock_skew_seconds"
            ],
            source_sha256=content._digest(content._canonical(source)),
        )
    except ControllerReceiptV2WriteFailure:
        raise
    except Exception:
        _fail("controller_receipt_writer.authority_invalid")


class _Timeline:
    __slots__ = ("_last", "_source")

    def __init__(self, source: Callable[[], float]) -> None:
        if not callable(source):
            _fail("controller_receipt_writer.configuration_invalid")
        self._source = source
        self._last: float | None = None

    def read(self) -> float:
        try:
            value = self._source()
        except Exception:
            _fail("controller_receipt_writer.clock_invalid")
        if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
            _fail("controller_receipt_writer.clock_invalid")
        current = float(value)
        if self._last is not None and current < self._last:
            _fail("controller_receipt_writer.clock_regressed")
        self._last = current
        return current


class _UtcTimeline:
    __slots__ = ("_last", "_source")

    def __init__(self, source: Callable[[], datetime]) -> None:
        if not callable(source):
            _fail("controller_receipt_writer.configuration_invalid")
        self._source = source
        self._last: datetime | None = None

    def read(self) -> datetime:
        try:
            value = self._source()
        except Exception:
            _fail("controller_receipt_writer.clock_invalid")
        if (
            type(value) is not datetime
            or value.tzinfo is None
            or value.utcoffset() != timedelta(0)
            or value.microsecond != 0
        ):
            _fail("controller_receipt_writer.clock_invalid")
        current = value.astimezone(UTC)
        if self._last is not None and current < self._last:
            _fail("controller_receipt_writer.clock_regressed")
        self._last = current
        return current


def _remaining(timeline: _Timeline, deadline: float, maximum: int) -> int:
    value = math.floor(deadline - timeline.read())
    if value < 1:
        _fail("controller_receipt_writer.deadline")
    return min(value, maximum)


def _header_values(headers: Any, name: str) -> tuple[str, ...]:
    try:
        raw = headers.get_all(name) if hasattr(headers, "get_all") else None
        if raw is None:
            value = headers.get(name)
            raw = [] if value is None else [value]
        values = tuple(raw)
    except Exception:
        _fail("controller_receipt_writer.readback_invalid")
    if any(
        type(value) is not str
        or not value
        or len(value) > 512
        or "\r" in value
        or "\n" in value
        for value in values
    ):
        _fail("controller_receipt_writer.readback_invalid")
    return values


def _single(headers: Any, name: str) -> str:
    values = _header_values(headers, name)
    if len(values) != 1:
        _fail("controller_receipt_writer.readback_invalid")
    return values[0]


def _http_date(value: str) -> datetime:
    try:
        result = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        _fail("controller_receipt_writer.readback_invalid")
    if result.tzinfo is None or result.microsecond != 0:
        _fail("controller_receipt_writer.readback_invalid")
    return result.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class _WireResponse:
    status: int
    body: bytes = dataclass_field(repr=False)
    headers: Any = dataclass_field(repr=False)


def _read_wire(response: Any, *, uri: str, maximum: int) -> _WireResponse:
    try:
        status = getattr(response, "status", getattr(response, "code", None))
        final_uri = response.geturl()
        headers = response.headers
        lengths = _header_values(headers, "Content-Length")
        encodings = _header_values(headers, "Content-Encoding")
        ranges = _header_values(headers, "Content-Range")
        transfers = _header_values(headers, "Transfer-Encoding")
        if (
            type(status) is not int
            or not 100 <= status <= 599
            or final_uri != uri
            or len(lengths) != 1
            or len(encodings) > 1
            or ranges
            or transfers
            or encodings
            and encodings[0].casefold() != "identity"
            or not lengths[0].isascii()
            or not lengths[0].isdigit()
            or lengths[0] != str(int(lengths[0]))
            or int(lengths[0]) > maximum
        ):
            _fail("controller_receipt_writer.readback_invalid")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(maximum + 1 - total)
            if type(chunk) is not bytes:
                _fail("controller_receipt_writer.readback_invalid")
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                _fail("controller_receipt_writer.readback_invalid")
            chunks.append(chunk)
        body = b"".join(chunks)
        if len(body) != int(lengths[0]):
            _fail("controller_receipt_writer.readback_invalid")
        return _WireResponse(status=status, body=body, headers=headers)
    except ControllerReceiptV2WriteFailure:
        raise
    except Exception:
        _fail("controller_receipt_writer.readback_invalid")


def _open(
    opener: _HttpOpener,
    request: urllib.request.Request,
    *,
    uri: str,
    maximum: int,
    timeout: int,
    deadline_factory: Callable[[int], Any],
) -> _WireResponse:
    with deadline_factory(timeout):
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return _read_wire(response, uri=uri, maximum=maximum)


def _put_once(
    opener: _HttpOpener,
    request: urllib.request.Request,
    *,
    uri: str,
    maximum: int,
    timeout: int,
    deadline_factory: Callable[[int], Any],
    policy: Mapping[str, Any],
    now: datetime,
    maximum_clock_skew_seconds: int,
) -> tuple[str, str] | None:
    """Return exact created evidence, or None after any dispatched uncertainty."""

    dispatched = False
    try:
        with deadline_factory(timeout):
            dispatched = True
            try:
                response = opener.open(request, timeout=timeout)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                wire = _read_wire(response, uri=uri, maximum=maximum)
        return _created_response(
            wire,
            policy=policy,
            now=now,
            maximum_clock_skew_seconds=maximum_clock_skew_seconds,
        )
    except Exception:
        if dispatched:
            return None
        raise


def _request_headers(
    *, token: str, now: datetime, version: str, content_type: str
) -> dict[str, str]:
    return {
        "Accept": content_type,
        "Accept-Encoding": "identity",
        "Authorization": f"Bearer {token}",
        "x-ms-date": format_datetime(now, usegmt=True),
        "x-ms-version": version,
    }


@dataclass(frozen=True, slots=True)
class _Readback:
    content_md5: str
    etag: str
    last_modified: str
    retained_until: str


def _verify_readback(
    response: _WireResponse,
    *,
    snapshot: _AuthoritySnapshot,
    policy: Mapping[str, Any],
    now: datetime,
) -> _Readback:
    headers = response.headers
    media = _single(headers, "Content-Type")
    md5 = _single(headers, "Content-MD5")
    etag = _single(headers, "ETag")
    modified_raw = _single(headers, "Last-Modified")
    retained_raw = _single(headers, "x-ms-immutability-policy-until-date")
    mode = _single(headers, "x-ms-immutability-policy-mode")
    hold = _single(headers, "x-ms-legal-hold")
    encrypted = _single(headers, "x-ms-server-encrypted")
    blob_type = _single(headers, "x-ms-blob-type")
    version = _single(headers, "x-ms-version")
    modified = _http_date(modified_raw)
    retained = _http_date(retained_raw)
    expected_md5 = base64.b64encode(
        hashlib.md5(snapshot.receipt_bytes, usedforsecurity=False).digest()
    ).decode("ascii")
    try:
        decoded_md5 = base64.b64decode(md5, validate=True)
    except (TypeError, ValueError):
        _fail("controller_receipt_writer.readback_invalid")
    if (
        response.status != policy["readback"]["successful_status"]
        or response.body != snapshot.receipt_bytes
        or tuple(part.strip().casefold() for part in media.split(";"))
        not in {("application/json",), ("application/json", "charset=utf-8")}
        or md5 != expected_md5
        or len(decoded_md5) != 16
        or ETAG_RE.fullmatch(etag) is None
        or mode.casefold() != "locked"
        or hold.casefold() != "true"
        or encrypted.casefold() != "true"
        or blob_type != policy["readback"]["blob_type"]
        or version != policy["transport"]["service_api_version"]
        or retained <= now
        or modified
        < snapshot.created_at - timedelta(seconds=snapshot.maximum_clock_skew_seconds)
        or modified > now + timedelta(seconds=snapshot.maximum_clock_skew_seconds)
        or retained - modified
        < timedelta(days=policy["readback"]["minimum_retention_days"])
    ):
        _fail("controller_receipt_writer.readback_invalid")
    return _Readback(
        content_md5=md5,
        etag=etag,
        last_modified=modified.strftime("%Y-%m-%dT%H:%M:%SZ"),
        retained_until=retained.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def _created_response(
    response: _WireResponse,
    *,
    policy: Mapping[str, Any],
    now: datetime,
    maximum_clock_skew_seconds: int,
) -> tuple[str, str] | None:
    try:
        created = policy["provider_contract"]["created"]
        etag = _single(response.headers, "ETag")
        modified = _http_date(_single(response.headers, "Last-Modified"))
        if (
            response.status != created["status"]
            or response.body
            or ETAG_RE.fullmatch(etag) is None
            or _single(response.headers, "x-ms-request-server-encrypted").casefold()
            != "true"
            or _single(response.headers, "x-ms-version")
            != policy["transport"]["service_api_version"]
            or abs(modified - now) > timedelta(seconds=maximum_clock_skew_seconds)
        ):
            return None
        return etag, modified.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


@dataclass(frozen=True, slots=True, init=False, repr=False)
class WrittenControllerReceiptV2:
    """Sealed exact readback result retaining its private builder authority."""

    outcome: str
    receipt_sha256: str
    record_chain_sha256: str
    authority_chain_sha256: str
    rehearsal_id: str
    content_md5: str
    etag: str
    last_modified: str
    retained_until: str
    validated_at: datetime
    arm_network_facts_authenticated: bool
    content_built_and_validated: bool
    controller_receipt_exists: bool
    full_three_record_source_authenticated: bool
    create_only_behavior_proven: bool
    database_role_authenticated: bool
    executable: bool
    intent_submission_source_authenticated: bool
    point_in_time_isolation_inventory_authenticated: bool
    private_route_reachability_authenticated: bool
    tls_transport_authenticated: bool
    writer_identity_use_authenticated: bool
    writer_private_route_use_authenticated: bool
    provider_facts_authenticated: bool
    execution_history_authenticated: bool
    blob_or_redis_copy_absence_proven: bool
    database_mutation_authority: bool
    sanitizer_authority: bool
    runtime_authority: bool
    workflow_integrated: bool
    _built_receipt: builder.BuiltControllerReceiptV2 = dataclass_field(
        repr=False, compare=False
    )
    _writer_policy_bytes: bytes = dataclass_field(repr=False, compare=False)
    _proof_sha256: str = dataclass_field(repr=False, compare=False)
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("controller_receipt_writer.result_invalid")

    def _is_authenticated(self) -> bool:
        try:
            policy = validate_controller_receipt_v2_writer_policy(
                content._parse_canonical(
                    self._writer_policy_bytes, MAXIMUM_POLICY_BYTES
                )
            )
            snapshot = _authority_snapshot(self._built_receipt, policy)
            projection = _result_projection(
                snapshot=snapshot,
                outcome=self.outcome,
                readback=_Readback(
                    content_md5=self.content_md5,
                    etag=self.etag,
                    last_modified=self.last_modified,
                    retained_until=self.retained_until,
                ),
                validated_at=self.validated_at,
            )
            return (
                type(self) is WrittenControllerReceiptV2
                and self._seal is _RESULT_SEAL
                and self.outcome in {"created", "reconciled_indeterminate"}
                and type(self.validated_at) is datetime
                and self.validated_at.tzinfo is not None
                and self.validated_at.utcoffset() == timedelta(0)
                and self.validated_at.microsecond == 0
                and self._built_receipt.created_at
                <= self.validated_at
                < min(
                    self._built_receipt.expires_at,
                    self._built_receipt.cleanup_not_after,
                )
                and all(
                    getattr(self, name) == value for name, value in projection.items()
                )
                and all(
                    getattr(self, name) is value
                    for name, value in policy["claims"].items()
                )
                and self._proof_sha256 == _result_proof_sha256(projection)
            )
        except Exception:
            return False


def _result_projection(
    *,
    snapshot: _AuthoritySnapshot,
    outcome: str,
    readback: _Readback,
    validated_at: datetime,
) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "receipt_sha256": snapshot.receipt_sha256,
        "record_chain_sha256": snapshot.record_chain_sha256,
        "authority_chain_sha256": snapshot.authority_chain_sha256,
        "rehearsal_id": snapshot.rehearsal_id,
        "content_md5": readback.content_md5,
        "etag": readback.etag,
        "last_modified": readback.last_modified,
        "retained_until": readback.retained_until,
        "validated_at": validated_at,
    }


def _result_proof_sha256(projection: Mapping[str, Any]) -> str:
    serializable = dict(projection)
    validated_at = serializable["validated_at"]
    if type(validated_at) is not datetime:
        _fail("controller_receipt_writer.result_invalid")
    serializable["validated_at"] = validated_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    return content._digest(content._canonical(serializable))


def _mint_result(
    *,
    built_receipt: builder.BuiltControllerReceiptV2,
    policy: Mapping[str, Any],
    snapshot: _AuthoritySnapshot,
    outcome: str,
    readback: _Readback,
    validated_at: datetime,
) -> WrittenControllerReceiptV2:
    projection = _result_projection(
        snapshot=snapshot,
        outcome=outcome,
        readback=readback,
        validated_at=validated_at,
    )
    result = object.__new__(WrittenControllerReceiptV2)
    for name, value in {**projection, **policy["claims"]}.items():
        object.__setattr__(result, name, value)
    object.__setattr__(result, "_built_receipt", built_receipt)
    object.__setattr__(result, "_writer_policy_bytes", content._canonical(policy))
    object.__setattr__(result, "_proof_sha256", _result_proof_sha256(projection))
    object.__setattr__(result, "_seal", _RESULT_SEAL)
    if not result._is_authenticated():
        _fail("controller_receipt_writer.result_invalid")
    return result


class ControllerReceiptV2Writer:
    """One-shot writer configured only by reviewed private dependencies."""

    __slots__ = (
        "_blob_opener",
        "_clock",
        "_deadline_factory",
        "_epoch_clock",
        "_identity_opener",
        "_lock",
        "_monotonic",
        "_used",
    )
    _blob_opener: _HttpOpener
    _clock: Callable[[], datetime]
    _deadline_factory: Callable[[int], Any]
    _epoch_clock: Callable[[], float]
    _identity_opener: _HttpOpener
    _lock: threading.Lock
    _monotonic: Callable[[], float]
    _used: bool

    def __init__(self) -> None:
        _fail("controller_receipt_writer.configuration_invalid")

    @classmethod
    def _from_reviewed_configuration(
        cls,
        *,
        clock: Callable[[], datetime],
        identity_opener: _HttpOpener,
        blob_opener: _HttpOpener,
        epoch_clock: Callable[[], float],
        monotonic: Callable[[], float],
        deadline_factory: Callable[[int], Any],
    ) -> ControllerReceiptV2Writer:
        if (
            cls is not ControllerReceiptV2Writer
            or not callable(clock)
            or not callable(getattr(identity_opener, "open", None))
            or not callable(getattr(blob_opener, "open", None))
            or not callable(epoch_clock)
            or not callable(monotonic)
            or not callable(deadline_factory)
        ):
            _fail("controller_receipt_writer.configuration_invalid")
        value = object.__new__(cls)
        value._clock = clock
        value._identity_opener = identity_opener
        value._blob_opener = blob_opener
        value._epoch_clock = epoch_clock
        value._monotonic = monotonic
        value._deadline_factory = deadline_factory
        value._lock = threading.Lock()
        value._used = False
        return value

    def _claim_use(self) -> None:
        with self._lock:
            if self._used:
                _fail("controller_receipt_writer.already_used")
            self._used = True

    def _write(
        self, *, built_receipt: builder.BuiltControllerReceiptV2
    ) -> WrittenControllerReceiptV2:
        self._claim_use()
        policy = load_controller_receipt_v2_writer_policy()
        snapshot = _authority_snapshot(built_receipt, policy)
        timeline = _Timeline(self._monotonic)
        trusted_utc = _UtcTimeline(self._clock)
        total_deadline = (
            timeline.read() + policy["transport"]["maximum_elapsed_seconds"]
        )

        def checkpoint() -> datetime:
            if _authority_snapshot(built_receipt, policy) != snapshot:
                _fail("controller_receipt_writer.authority_invalid")
            now = trusted_utc.read()
            if (
                not built_receipt.created_at
                <= now
                < min(built_receipt.expires_at, built_receipt.cleanup_not_after)
            ):
                _fail("controller_receipt_writer.expired")
            if timeline.read() > total_deadline:
                _fail("controller_receipt_writer.deadline")
            return now

        checkpoint()
        try:
            identity_opener = self._identity_opener
            blob_opener = self._blob_opener
            identity_contract = content._parse_canonical(
                snapshot.identity_contract_bytes, MAXIMUM_POLICY_BYTES
            )
            token = runtime_ingress._managed_identity_token(
                client_id=snapshot.client_id,
                resource=snapshot.oauth_resource,
                contract=identity_contract,
                identity_opener=identity_opener,
                identity_endpoint=os.environ.get("IDENTITY_ENDPOINT", ""),
                identity_header=os.environ.get("IDENTITY_HEADER", ""),
                epoch_timeline=runtime_ingress._Timeline(
                    self._epoch_clock,
                    "controller_receipt_writer.identity_clock_invalid",
                ),
                timeout_seconds=_remaining(
                    timeline,
                    total_deadline,
                    policy["transport"]["maximum_request_timeout_seconds"],
                ),
                deadline_factory=self._deadline_factory,
            )
        except ControllerReceiptV2WriteFailure:
            raise
        except Exception:
            _fail("controller_receipt_writer.identity_failed")
        put_now = checkpoint()
        timeout = _remaining(
            timeline,
            total_deadline,
            policy["transport"]["maximum_request_timeout_seconds"],
        )
        md5 = base64.b64encode(
            hashlib.md5(snapshot.receipt_bytes, usedforsecurity=False).digest()
        ).decode("ascii")
        headers = _request_headers(
            token=token,
            now=put_now,
            version=policy["transport"]["service_api_version"],
            content_type=policy["write"]["content_type"],
        )
        headers.update(
            {
                "Content-Length": str(len(snapshot.receipt_bytes)),
                "Content-MD5": md5,
                "Content-Type": policy["write"]["content_type"],
                "If-None-Match": "*",
                "x-ms-blob-content-md5": md5,
                "x-ms-blob-type": policy["write"]["blob_type"],
            }
        )
        request = urllib.request.Request(
            snapshot.uri,
            data=snapshot.receipt_bytes,
            headers=headers,
            method="PUT",
        )
        created = _put_once(
            blob_opener,
            request,
            uri=snapshot.uri,
            maximum=snapshot.maximum_record_bytes,
            timeout=timeout,
            deadline_factory=self._deadline_factory,
            policy=policy,
            now=put_now,
            maximum_clock_skew_seconds=snapshot.maximum_clock_skew_seconds,
        )
        checkpoint()
        get_now = checkpoint()
        get_request = urllib.request.Request(
            snapshot.uri,
            headers=_request_headers(
                token=token,
                now=get_now,
                version=policy["transport"]["service_api_version"],
                content_type=policy["write"]["content_type"],
            ),
            method="GET",
        )
        try:
            get_response = _open(
                blob_opener,
                get_request,
                uri=snapshot.uri,
                maximum=snapshot.maximum_record_bytes,
                timeout=_remaining(
                    timeline,
                    total_deadline,
                    policy["transport"]["maximum_request_timeout_seconds"],
                ),
                deadline_factory=self._deadline_factory,
            )
            readback = _verify_readback(
                get_response, snapshot=snapshot, policy=policy, now=get_now
            )
        except ControllerReceiptV2WriteFailure:
            raise
        except Exception:
            _fail("controller_receipt_writer.readback_invalid")
        validated_at = checkpoint()
        if created is not None and (
            readback.etag != created[0] or readback.last_modified != created[1]
        ):
            _fail("controller_receipt_writer.readback_invalid")
        return _mint_result(
            built_receipt=built_receipt,
            policy=policy,
            snapshot=snapshot,
            outcome="created" if created is not None else "reconciled_indeterminate",
            readback=readback,
            validated_at=validated_at,
        )

    def write(
        self, *, built_receipt: builder.BuiltControllerReceiptV2
    ) -> WrittenControllerReceiptV2:
        code: str | None = None
        try:
            return self._write(built_receipt=built_receipt)
        except Exception as error:
            code = (
                error.args[0]
                if type(error) is ControllerReceiptV2WriteFailure
                and len(error.args) == 1
                and type(error.args[0]) is str
                and error.args[0] in _FAILURE_CODES
                else "controller_receipt_writer.operation_failed"
            )
        raise ControllerReceiptV2WriteFailure(code) from None


def build_controller_receipt_v2_writer() -> NoReturn:
    """Refuse public construction while the pinned writer remains dormant."""

    load_controller_receipt_v2_writer_policy(executable=True)
    _fail("controller_receipt_writer.policy_disabled")
