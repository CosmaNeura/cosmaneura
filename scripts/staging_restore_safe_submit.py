#!/usr/bin/env python3
"""Dormant model for one PostgreSQL PITR submission and reconciliation.

The current workflow does not import this module.  A future reviewed binding can
use it only after the canonical policies are activated and marker-store atomicity
is proven.  Provider errors and responses are never rendered by this module.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from time import sleep
from typing import Any, Callable, Mapping, NoReturn, Protocol

try:
    from scripts import staging_restore_runtime_adapters as runtime_adapters
    from scripts import validate_staging_restore_runtime_binding as runtime_binding
except ModuleNotFoundError:  # pragma: no cover - direct script import fallback
    import staging_restore_runtime_adapters as runtime_adapters
    import validate_staging_restore_runtime_binding as runtime_binding

ROOT = Path(__file__).resolve().parents[1]
POLICY_ROOT = ROOT / "contracts/staging-rehearsal/v1"
ENVELOPE_POLICY_PATH = POLICY_ROOT / "restore-envelope-policy.json"
SAFE_SUBMIT_POLICY_PATH = POLICY_ROOT / "restore-safe-submit-policy.json"
EVIDENCE_MANIFEST_POLICY_PATH = POLICY_ROOT / "evidence-manifest-policy.json"
ENVELOPE_POLICY_SHA256 = (
    "6a80a9681bf2527b7c69f48c896baa01a7b5face3ddb415c1300632dab020d92"
)
SAFE_SUBMIT_POLICY_SHA256 = (
    "dd437ff02e201f01e8ec097345b444f8fc7786a7964b9345af097d3e1464f501"
)
EVIDENCE_MANIFEST_POLICY_SHA256 = (
    "8b1cd30a90782d5596b44415e0abb9e934c78aeaca9bc7960e226d1feee8df53"
)

REHEARSAL_RE = re.compile(r"^[0-9a-f]{12}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
RUN_RE = re.compile(r"^[1-9][0-9]*$")
REVISION_RE = re.compile(r"^[0-9a-f]{12}$")
MANIFEST_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{11,63}$")
UTC_RE = re.compile(r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
PLACEHOLDER_RE = re.compile(r"^CONFIGURE_EXACT_[A-Z0-9_]+$")
EXPECTED_PLACEHOLDERS = (
    "CONFIGURE_EXACT_CLEANUP_CLIENT_UUID",
    "CONFIGURE_EXACT_CLEANUP_CONTROL_READER_ROLE_DEFINITION_ID",
    "CONFIGURE_EXACT_CLEANUP_CONTROL_READER_ROLE_PERMISSIONS_SHA256",
    "CONFIGURE_EXACT_CLEANUP_PRINCIPAL_UUID",
    "CONFIGURE_EXACT_CLEANUP_TARGET_ROLE_DEFINITION_ID",
    "CONFIGURE_EXACT_CLEANUP_TARGET_ROLE_PERMISSIONS_SHA256",
    "CONFIGURE_EXACT_FIREWALL_DENY_POLICY_DEFINITION_ID",
    "CONFIGURE_EXACT_FIREWALL_DENY_POLICY_RULE_SHA256",
    "CONFIGURE_EXACT_PROVISION_CLIENT_UUID",
    "CONFIGURE_EXACT_PROVISION_CONTROL_READER_ROLE_DEFINITION_ID",
    "CONFIGURE_EXACT_PROVISION_CONTROL_READER_ROLE_PERMISSIONS_SHA256",
    "CONFIGURE_EXACT_PROVISION_PRINCIPAL_UUID",
    "CONFIGURE_EXACT_PROVISION_SOURCE_ROLE_DEFINITION_ID",
    "CONFIGURE_EXACT_PROVISION_SOURCE_ROLE_PERMISSIONS_SHA256",
    "CONFIGURE_EXACT_PROVISION_TARGET_ROLE_DEFINITION_ID",
    "CONFIGURE_EXACT_PROVISION_TARGET_ROLE_PERMISSIONS_SHA256",
)
MANAGEMENT_ROOT = "https://management.azure.com"
ARM_OAUTH_RESOURCE = f"{MANAGEMENT_ROOT}/"
BLOB_OAUTH_RESOURCE = "https://storage.azure.com/"
BLOB_SERVICE_VERSION = "2021-12-02"
MAXIMUM_ARM_RESPONSE_BYTES = 1_048_576
RUNTIME_CA_BUNDLE_PATH = runtime_adapters.RUNTIME_CA_BUNDLE_PATH
IDENTITY_API_VERSION = runtime_adapters.IDENTITY_API_VERSION
MAXIMUM_TOKEN_BYTES = runtime_adapters.MAXIMUM_TOKEN_BYTES
UUID_RE = runtime_adapters.UUID_RE
SAFE_TOKEN_RE = runtime_adapters.SAFE_TOKEN_RE
UNSIGNED_DECIMAL_RE = runtime_adapters.UNSIGNED_DECIMAL_RE
BLOB_HOST_RE = re.compile(r"^[a-z0-9]{3,24}\.blob\.core\.windows\.net$")
CONTAINER_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$")
ETAG_RE = re.compile(r'^"[^"\r\n]+"$')
ARM_ETAG_RE = re.compile(r'^(?:W/)?"[^"\r\n]+"$')
MARKER_KINDS = frozenset({"intent", "submission", "cleanup"})
RUNTIME_ACTIONS = frozenset({"provision", "cleanup"})
POSTGRES_TYPE = "microsoft.dbforpostgresql/flexibleservers"
UNKNOWN = object()
CLEANUP_PRESENT_UNPROVEN = object()
ISOLATED = "isolated"
ISOLATION_TRANSIENT = "transient"
ISOLATION_REFUSED = "refused"


class SafeSubmitError(Exception):
    """A bounded refusal that contains no provider response text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ArmOutcomeUnknown(Exception):
    """The caller cannot know whether ARM accepted the request."""


class MarkerOutcomeUnknown(Exception):
    """The caller cannot prove a marker present or absent."""


class MarkerPresentInvalid(SafeSubmitError):
    """The exact marker path exists but its bytes/properties are untrusted."""


class MarkerConflict(SafeSubmitError):
    """The exact marker path already contains different bytes."""


def _fail(code: str) -> NoReturn:
    raise SafeSubmitError(code)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _lower_id(value: object) -> str:
    return str(value or "").rstrip("/").lower()


def _location(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _id(*parts: str) -> str:
    return "/" + "/".join(part.strip("/") for part in parts)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        _fail("timestamp_timezone_required")
    return (
        value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _parse_utc(value: object, code: str) -> datetime:
    if not isinstance(value, str) or not UTC_RE.fullmatch(value):
        _fail(code)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        _fail(code)


def _parse_arm_utc(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _read_object(path: Path, expected_sha256: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        if _sha256(raw) != expected_sha256:
            _fail("policy_digest_mismatch")
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail("policy_read_failed")
    if not isinstance(value, dict):
        _fail("policy_invalid")
    return value


def _placeholder_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [
            item for nested in value.values() for item in _placeholder_values(nested)
        ]
    if isinstance(value, list):
        return [item for nested in value for item in _placeholder_values(nested)]
    if isinstance(value, str) and PLACEHOLDER_RE.fullmatch(value):
        return [value]
    return []


@dataclass(frozen=True)
class ArmResponse:
    status: int
    body: Any = None
    headers: Mapping[str, str] | None = None


class ArmTransport(Protocol):
    def request(
        self,
        method: str,
        resource_id: str,
        *,
        api_version: str,
        body: dict[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: int,
    ) -> ArmResponse: ...


class MarkerStore(Protocol):
    def read(self, kind: str, rehearsal_id: str) -> bytes | None: ...

    def create_if_absent(self, kind: str, rehearsal_id: str, raw: bytes) -> bytes: ...

    def list_intent_ids(self) -> tuple[str, ...]: ...


class _HttpOpener(Protocol):
    def open(self, request: Any, *, timeout: int) -> Any: ...


class _DeadlineFactory(Protocol):
    def __call__(self, timeout_seconds: int) -> AbstractContextManager[None]: ...


@dataclass(frozen=True)
class _WireResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _runtime_tls_context(
    ca_bundle_path: Path = RUNTIME_CA_BUNDLE_PATH,
) -> ssl.SSLContext:
    try:
        return runtime_adapters._runtime_tls_context(ca_bundle_path)
    except runtime_adapters.RuntimeAdapterFailure:
        raise SafeSubmitError("transport_tls_configuration_invalid") from None


def _hardened_opener(*, tls_context: ssl.SSLContext | None = None) -> Any:
    """Use only the immutable-image CA roots and no ambient proxy route."""

    context = tls_context if tls_context is not None else _runtime_tls_context()
    if not isinstance(context, ssl.SSLContext):
        _fail("transport_tls_configuration_invalid")
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _RefuseRedirects(),
        urllib.request.HTTPSHandler(context=context),
    )


@contextmanager
def _wall_clock_deadline(timeout_seconds: int) -> Iterator[None]:
    """Bound one complete synchronous identity-plus-service exchange."""

    try:
        with runtime_adapters._wall_clock_deadline(timeout_seconds):
            yield
    except runtime_adapters.RuntimeAdapterFailure as error:
        code = (
            "transport_deadline"
            if str(error) == "http_request.timeout"
            else "transport_deadline_unavailable"
        )
        raise SafeSubmitError(code) from None


def _validate_identity_endpoint(endpoint: str) -> str:
    try:
        validated = runtime_adapters._validate_identity_endpoint(endpoint)
    except runtime_adapters.RuntimeAdapterFailure:
        raise SafeSubmitError("managed_identity_configuration_invalid") from None
    if urllib.parse.urlsplit(validated).path != "/msi/token":
        _fail("managed_identity_configuration_invalid")
    return validated


def _clock_value(clock: Callable[[], float], code: str) -> float:
    try:
        value = clock()
    except Exception:
        raise SafeSubmitError(code) from None
    if (
        type(value) not in {int, float}
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < 0
    ):
        _fail(code)
    return float(value)


def _header_values(headers: Any, name: str) -> tuple[str, ...]:
    try:
        if hasattr(headers, "get_all"):
            raw = headers.get_all(name) or []
        else:
            value = headers.get(name)
            raw = [] if value is None else [value]
        values = tuple(raw)
    except Exception:
        _fail("http_response_invalid")
    if any(
        type(value) is not str
        or not value
        or len(value) > 4_096
        or "\r" in value
        or "\n" in value
        for value in values
    ):
        _fail("http_response_invalid")
    return values


def _read_wire_response(
    response: Any, *, expected_url: str, maximum_bytes: int
) -> _WireResponse:
    try:
        status = getattr(response, "status", getattr(response, "code", None))
        final_url = response.geturl()
        content_lengths = _header_values(response.headers, "Content-Length")
        content_encodings = _header_values(response.headers, "Content-Encoding")
        content_ranges = _header_values(response.headers, "Content-Range")
        transfer_encodings = _header_values(response.headers, "Transfer-Encoding")
        normalized: dict[str, str] = {}
        for key, value in response.headers.items():
            lowered = str(key).lower()
            rendered = str(value)
            if (
                not lowered
                or lowered in normalized
                or len(lowered) > 256
                or len(rendered) > 4_096
                or "\r" in lowered
                or "\n" in lowered
                or "\r" in rendered
                or "\n" in rendered
            ):
                _fail("http_response_invalid")
            normalized[lowered] = rendered
    except SafeSubmitError:
        raise
    except Exception:
        raise SafeSubmitError("http_response_invalid") from None
    if (
        type(status) is not int
        or not 100 <= status <= 599
        or type(final_url) is not str
        or final_url != expected_url
        or len(content_lengths) != 1
        or len(content_encodings) > 1
        or content_ranges
        or transfer_encodings
        or content_encodings
        and content_encodings[0].strip().lower() != "identity"
    ):
        _fail("http_response_invalid")
    raw_length = content_lengths[0]
    if (
        not raw_length.isascii()
        or not raw_length.isdigit()
        or raw_length != str(int(raw_length))
    ):
        _fail("http_response_invalid")
    declared_length = int(raw_length)
    if declared_length > maximum_bytes:
        _fail("http_response_too_large")
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            chunk = response.read(maximum_bytes + 1 - total)
            if type(chunk) is not bytes:
                _fail("http_response_invalid")
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum_bytes:
                _fail("http_response_too_large")
    except SafeSubmitError:
        raise
    except Exception:
        raise SafeSubmitError("http_response_unavailable") from None
    raw = b"".join(chunks)
    if declared_length != len(raw):
        _fail("http_response_invalid")
    return _WireResponse(status=status, body=raw, headers=normalized)


def _json_mapping(raw: bytes, code: str) -> Mapping[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                _fail(code)
            value[key] = item
        return value

    def reject_nonfinite(_value: str) -> NoReturn:
        _fail(code)

    try:
        value = json.loads(
            raw,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonfinite,
        )
    except SafeSubmitError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise SafeSubmitError(code) from None
    if not isinstance(value, Mapping):
        _fail(code)
    return value


class ContainerAppsManagedIdentityTokenSource:
    """Explicit tokens for one selected job UAMI and two fixed audiences."""

    __slots__ = (
        "_client_id",
        "_clock",
        "_endpoint",
        "_expiry_skew_seconds",
        "_header",
        "_last_now",
        "_lock",
        "_maximum_lifetime_seconds",
        "_maximum_response_bytes",
        "_maximum_timeout_seconds",
        "_opener",
        "_service_api_version",
        "_tokens",
    )

    def __init__(
        self,
        *,
        client_id: str,
        identity_endpoint: str,
        identity_header: str,
        service_api_version: str,
        maximum_response_bytes: int,
        expiry_skew_seconds: int,
        maximum_lifetime_seconds: int,
        maximum_timeout_seconds: int,
        opener: _HttpOpener | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        endpoint = _validate_identity_endpoint(identity_endpoint)
        if (
            type(client_id) is not str
            or UUID_RE.fullmatch(client_id) is None
            or type(identity_header) is not str
            or SAFE_TOKEN_RE.fullmatch(identity_header) is None
            or service_api_version != IDENTITY_API_VERSION
            or type(maximum_response_bytes) is not int
            or not 1 <= maximum_response_bytes <= MAXIMUM_TOKEN_BYTES
            or type(expiry_skew_seconds) is not int
            or not 1 <= expiry_skew_seconds <= 300
            or type(maximum_lifetime_seconds) is not int
            or not expiry_skew_seconds < maximum_lifetime_seconds <= 86_400
            or type(maximum_timeout_seconds) is not int
            or not 1 <= maximum_timeout_seconds <= 60
            or (opener is not None and not callable(getattr(opener, "open", None)))
            or not callable(clock)
        ):
            _fail("managed_identity_configuration_invalid")
        self._client_id = client_id
        self._endpoint = endpoint
        self._header = identity_header
        self._service_api_version = service_api_version
        self._maximum_response_bytes = maximum_response_bytes
        self._expiry_skew_seconds = expiry_skew_seconds
        self._maximum_lifetime_seconds = maximum_lifetime_seconds
        self._maximum_timeout_seconds = maximum_timeout_seconds
        self._opener = opener or _hardened_opener()
        self._clock = clock
        self._lock = threading.Lock()
        self._tokens: dict[str, tuple[str, int]] = {}
        self._last_now: float | None = None

    def _is_bound(self, *, client_id: str, resource: str) -> bool:
        return (
            type(self) is ContainerAppsManagedIdentityTokenSource
            and client_id == self._client_id
            and resource in {ARM_OAUTH_RESOURCE, BLOB_OAUTH_RESOURCE}
        )

    def _bounded_timeout(self, timeout_seconds: int) -> int:
        if type(timeout_seconds) is not int or timeout_seconds < 1:
            _fail("managed_identity_request_invalid")
        return min(timeout_seconds, self._maximum_timeout_seconds)

    def get_token(self, *, client_id: str, resource: str, timeout_seconds: int) -> str:
        if (
            not self._is_bound(client_id=client_id, resource=resource)
            or type(timeout_seconds) is not int
            or not 1 <= timeout_seconds <= self._maximum_timeout_seconds
        ):
            _fail("managed_identity_request_invalid")
        with self._lock:
            now = _clock_value(self._clock, "managed_identity_clock_invalid")
            if self._last_now is not None and now < self._last_now:
                _fail("managed_identity_clock_invalid")
            self._last_now = now
            cached = self._tokens.get(resource)
            if cached is not None and cached[1] > now + self._expiry_skew_seconds:
                return cached[0]
            query = urllib.parse.urlencode(
                {
                    "api-version": self._service_api_version,
                    "resource": resource,
                    "client_id": client_id,
                }
            )
            url = f"{self._endpoint}?{query}"
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "Metadata": "true",
                    "X-IDENTITY-HEADER": self._header,
                },
                method="GET",
            )
            try:
                try:
                    response = self._opener.open(request, timeout=timeout_seconds)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    try:
                        identity_status = getattr(
                            response, "status", getattr(response, "code", None)
                        )
                        identity_final_url = response.geturl()
                    except Exception:
                        _fail("managed_identity_response_invalid")
                    if (
                        identity_final_url != url
                        or type(identity_status) is int
                        and 300 <= identity_status < 400
                    ):
                        _fail("managed_identity_redirect_refused")
                    wire = _read_wire_response(
                        response,
                        expected_url=url,
                        maximum_bytes=self._maximum_response_bytes,
                    )
            except SafeSubmitError:
                raise
            except Exception:
                raise SafeSubmitError("managed_identity_unavailable") from None
            if 300 <= wire.status < 400:
                _fail("managed_identity_redirect_refused")
            if wire.status != 200:
                _fail("managed_identity_refused")
            content_type = wire.headers.get("content-type", "")
            if tuple(part.strip().lower() for part in content_type.split(";")) not in {
                ("application/json",),
                ("application/json", "charset=utf-8"),
            }:
                _fail("managed_identity_response_invalid")
            document = _json_mapping(wire.body, "managed_identity_response_invalid")
            token = document.get("access_token")
            expires_on = document.get("expires_on")
            if (
                not {"access_token", "expires_on"} <= set(document)
                or type(token) is not str
                or SAFE_TOKEN_RE.fullmatch(token) is None
                or document.get("token_type", "Bearer") != "Bearer"
                or document.get("resource", resource) != resource
                or document.get("client_id", client_id) != client_id
            ):
                _fail("managed_identity_response_invalid")
            if type(expires_on) is int:
                expiration = expires_on
            elif (
                type(expires_on) is str
                and UNSIGNED_DECIMAL_RE.fullmatch(expires_on) is not None
                and expires_on == str(int(expires_on))
            ):
                expiration = int(expires_on)
            else:
                _fail("managed_identity_response_invalid")
            if (
                expiration <= now + self._expiry_skew_seconds
                or expiration > now + self._maximum_lifetime_seconds
            ):
                _fail("managed_identity_response_invalid")
            self._tokens[resource] = (token, expiration)
            return token


@dataclass(frozen=True)
class SafeSubmitConfig:
    policy_id: str
    envelope_policy_id: str
    envelope_policy_sha256: str
    evidence_binding_schema_version: int
    evidence_binding_policy_id: str
    evidence_manifest_policy_id: str
    evidence_manifest_policy_sha256: str
    evidence_repository: str
    evidence_protected_ref: str
    evidence_workflow_path: str
    evidence_event_name: str
    evidence_signer_issuer: str
    evidence_signer_subject: str
    evidence_source_database_name: str
    evidence_maximum_lifetime_seconds: int
    subscription_id: str
    target_group_id: str
    source_server_id: str
    provision_client_id: str
    provision_principal_id: str
    location: str
    name_prefix: str
    maximum_ttl_hours: int
    required_tags: dict[str, str]
    postgres_api_version: str
    request_timeout_seconds: int
    poll_interval_seconds: int
    restore_poll_attempts: int
    cleanup_poll_attempts: int
    binding_clock_skew_seconds: int
    late_materialization_watch_hours: int
    marker_account_resource_id: str
    marker_account_host: str
    marker_oauth_resource: str
    marker_service_version: str
    marker_containers: dict[str, str]
    marker_blob_name_suffix: str
    marker_request_timeout_seconds: int
    marker_read_poll_attempts: int
    marker_read_poll_interval_seconds: int
    marker_max_record_bytes: int
    marker_list_page_size: int
    marker_list_max_pages: int
    marker_list_max_records: int
    marker_max_list_response_bytes: int
    marker_minimum_retention_days: int
    ready_state: str
    failed_states: frozenset[str]
    execution_authorized: bool
    blob_marker_store_implemented: bool
    blob_marker_provider_fidelity_proven: bool
    private_runner_binding_implemented: bool
    trusted_evidence_binding_implemented: bool


def _remaining_timeout(
    clock: Callable[[], float], *, started: float, limit: int, code: str
) -> int:
    current = _clock_value(clock, code)
    if current < started:
        _fail(code)
    remaining = math.floor(limit - (current - started))
    if remaining < 1:
        _fail("transport_deadline")
    return remaining


class AzureArmTransport:
    """Exact-target ARM adapter using one selected Container Apps job UAMI."""

    __slots__ = (
        "_action",
        "_client_id",
        "_clock",
        "_deadline",
        "_lock",
        "_opener",
        "_token_source",
        "config",
    )

    def __init__(
        self,
        config: SafeSubmitConfig,
        token_source: ContainerAppsManagedIdentityTokenSource,
        *,
        action: str,
        client_id: str,
        opener: _HttpOpener | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        deadline: _DeadlineFactory = _wall_clock_deadline,
    ) -> None:
        if (
            type(config) is not SafeSubmitConfig
            or not config.execution_authorized
            or action not in RUNTIME_ACTIONS
            or type(token_source) is not ContainerAppsManagedIdentityTokenSource
            or not token_source._is_bound(
                client_id=client_id, resource=ARM_OAUTH_RESOURCE
            )
            or (opener is not None and not callable(getattr(opener, "open", None)))
            or not callable(monotonic)
            or not callable(deadline)
        ):
            _fail("arm_transport_configuration_invalid")
        self.config = config
        self._action = action
        self._client_id = client_id
        self._token_source = token_source
        self._opener = opener or _hardened_opener()
        self._clock = monotonic
        self._deadline = deadline
        self._lock = threading.Lock()

    def _validate_request(
        self,
        method: str,
        resource_id: str,
        *,
        api_version: str,
        body: dict[str, Any] | None,
        headers: Mapping[str, str] | None,
        timeout_seconds: int,
    ) -> tuple[bytes | None, dict[str, str]]:
        allowed_methods = (
            {"GET", "PATCH", "PUT"}
            if self._action == "provision"
            else {"DELETE", "GET", "PATCH"}
        )
        if type(resource_id) is not str:
            _fail("arm_request_invalid")
        target_prefix = (
            f"{self.config.target_group_id}/providers/"
            "Microsoft.DBforPostgreSQL/flexibleServers/"
        )
        target_name = resource_id.removeprefix(target_prefix)
        if (
            type(method) is not str
            or method not in allowed_methods
            or not resource_id.startswith(target_prefix)
            or not target_name
            or not target_name.startswith(f"{self.config.name_prefix}-")
            or "/" in target_name
            or len(target_name) > 63
            or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", target_name) is None
            or any(char in resource_id for char in "%?#")
            or "//" in resource_id
            or api_version != self.config.postgres_api_version
            or type(timeout_seconds) is not int
            or timeout_seconds != self.config.request_timeout_seconds
            or body is not None
            and type(body) is not dict
            or headers is not None
            and not isinstance(headers, Mapping)
        ):
            _fail("arm_request_invalid")
        try:
            supplied = dict(headers or {})
        except Exception:
            raise SafeSubmitError("arm_request_invalid") from None
        if any(
            type(key) is not str or type(value) is not str
            for key, value in supplied.items()
        ):
            _fail("arm_request_invalid")
        if method == "GET":
            valid = body is None and not supplied
        elif method == "PUT":
            valid = body is not None and supplied == {"If-None-Match": "*"}
        elif method == "PATCH":
            valid = body is not None and (
                not supplied
                or set(supplied) == {"If-Match"}
                and ARM_ETAG_RE.fullmatch(supplied["If-Match"]) is not None
            )
        else:
            valid = body is None and (
                not supplied
                or set(supplied) == {"If-Match"}
                and ARM_ETAG_RE.fullmatch(supplied["If-Match"]) is not None
            )
        if not valid:
            _fail("arm_request_invalid")
        try:
            payload = (
                json.dumps(
                    body, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode()
                if body is not None
                else None
            )
        except (TypeError, ValueError, RecursionError):
            raise SafeSubmitError("arm_request_invalid") from None
        if payload is not None and len(payload) > MAXIMUM_ARM_RESPONSE_BYTES:
            _fail("arm_request_invalid")
        return payload, supplied

    def request(
        self,
        method: str,
        resource_id: str,
        *,
        api_version: str,
        body: dict[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: int,
    ) -> ArmResponse:
        payload, supplied = self._validate_request(
            method,
            resource_id,
            api_version=api_version,
            body=body,
            headers=headers,
            timeout_seconds=timeout_seconds,
        )
        mutating = method in {"DELETE", "PATCH", "PUT"}
        query = urllib.parse.urlencode({"api-version": api_version})
        url = f"{MANAGEMENT_ROOT}{resource_id}?{query}"
        dispatched = False
        try:
            with self._deadline(timeout_seconds), self._lock:
                started = _clock_value(self._clock, "arm_transport_clock_invalid")
                token_timeout = self._token_source._bounded_timeout(
                    _remaining_timeout(
                        self._clock,
                        started=started,
                        limit=timeout_seconds,
                        code="arm_transport_clock_invalid",
                    )
                )
                token = self._token_source.get_token(
                    client_id=self._client_id,
                    resource=ARM_OAUTH_RESOURCE,
                    timeout_seconds=token_timeout,
                )
                remaining = _remaining_timeout(
                    self._clock,
                    started=started,
                    limit=timeout_seconds,
                    code="arm_transport_clock_invalid",
                )
                request_headers = {
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "Authorization": f"Bearer {token}",
                }
                if payload is not None:
                    request_headers.update(
                        {
                            "Content-Length": str(len(payload)),
                            "Content-Type": "application/json",
                        }
                    )
                request_headers.update(supplied)
                request = urllib.request.Request(
                    url, data=payload, headers=request_headers, method=method
                )
                dispatched = True
                try:
                    response = self._opener.open(request, timeout=remaining)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    try:
                        arm_status = getattr(
                            response, "status", getattr(response, "code", None)
                        )
                        arm_final_url = response.geturl()
                    except Exception:
                        _fail("arm_response_invalid")
                    if (
                        arm_final_url != url
                        or type(arm_status) is int
                        and 300 <= arm_status < 400
                    ):
                        _fail("arm_redirect_refused")
                    wire = _read_wire_response(
                        response,
                        expected_url=url,
                        maximum_bytes=MAXIMUM_ARM_RESPONSE_BYTES,
                    )
                if 300 <= wire.status < 400:
                    _fail("arm_redirect_refused")
                if not 200 <= wire.status < 300:
                    return ArmResponse(status=wire.status, headers=wire.headers)
                if not wire.body:
                    parsed: Any = None
                else:
                    content_type = wire.headers.get("content-type", "")
                    if (
                        content_type.split(";", 1)[0].strip().lower()
                        != "application/json"
                    ):
                        _fail("arm_response_invalid")
                    parsed = dict(_json_mapping(wire.body, "arm_response_invalid"))
                return ArmResponse(
                    status=wire.status, body=parsed, headers=wire.headers
                )
        except ArmOutcomeUnknown:
            raise
        except SafeSubmitError as exc:
            if mutating and dispatched:
                raise ArmOutcomeUnknown from exc
            if exc.code == "http_response_too_large":
                _fail("arm_response_too_large")
            if exc.code.startswith("http_response_"):
                _fail("arm_response_invalid")
            raise
        except Exception as exc:
            raise ArmOutcomeUnknown from exc


@dataclass(frozen=True)
class BlobResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]


class AzureBlobMarkerStore:
    """Exact-endpoint marker store using one selected Container Apps job UAMI."""

    def __init__(
        self,
        config: SafeSubmitConfig,
        token_source: ContainerAppsManagedIdentityTokenSource,
        *,
        action: str,
        client_id: str,
        sleeper: Callable[[float], None] = sleep,
        clock: Callable[[], datetime] = _now,
        opener: _HttpOpener | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        deadline: _DeadlineFactory = _wall_clock_deadline,
        container_listing_permitted: bool = True,
    ) -> None:
        if type(config) is not SafeSubmitConfig:
            _fail("blob_store_configuration_invalid")
        account_parts = config.marker_account_resource_id.strip("/").split("/")
        if (
            not config.execution_authorized
            or not config.blob_marker_store_implemented
            or not config.blob_marker_provider_fidelity_proven
            or not config.private_runner_binding_implemented
            or action not in RUNTIME_ACTIONS
            or type(token_source) is not ContainerAppsManagedIdentityTokenSource
            or not token_source._is_bound(
                client_id=client_id, resource=BLOB_OAUTH_RESOURCE
            )
            or (opener is not None and not callable(getattr(opener, "open", None)))
            or not callable(monotonic)
            or not callable(deadline)
            or type(container_listing_permitted) is not bool
            or len(account_parts) != 8
            or any(not part for part in account_parts)
            or [part.lower() for part in account_parts[::2]]
            != ["subscriptions", "resourcegroups", "providers", "storageaccounts"]
            or account_parts[5].lower() != "microsoft.storage"
            or account_parts[7] != config.marker_account_host.split(".", 1)[0]
            or not BLOB_HOST_RE.fullmatch(config.marker_account_host)
            or config.marker_oauth_resource != BLOB_OAUTH_RESOURCE
            or config.marker_service_version != BLOB_SERVICE_VERSION
            or set(config.marker_containers) != MARKER_KINDS
            or len(set(config.marker_containers.values())) != len(MARKER_KINDS)
            or not all(
                CONTAINER_RE.fullmatch(value)
                for value in config.marker_containers.values()
            )
            or config.marker_blob_name_suffix != ".json"
            or not 1 <= config.marker_request_timeout_seconds <= 60
            or not 1 <= config.marker_read_poll_attempts <= 100
            or not 0 <= config.marker_read_poll_interval_seconds <= 60
            or not 1 <= config.marker_max_record_bytes <= 1_048_576
            or not 1 <= config.marker_list_page_size <= 5_000
            or not 1 <= config.marker_list_max_pages <= 100
            or not 1 <= config.marker_list_max_records <= 50_000
            or not config.marker_max_record_bytes
            <= config.marker_max_list_response_bytes
            <= 4_194_304
            or config.marker_minimum_retention_days < 90
        ):
            _fail("blob_store_configuration_invalid")
        self.config = config
        self.action = action
        self.client_id = client_id
        self.token_source = token_source
        self.sleeper = sleeper
        self.clock = clock
        self.monotonic = monotonic
        self.deadline = deadline
        self.lock = threading.Lock()
        self.opener = opener or _hardened_opener()
        self.container_listing_permitted = container_listing_permitted

    def _trusted_now(self) -> datetime:
        try:
            value = self.clock()
        except Exception as exc:
            raise SafeSubmitError("trusted_clock_unavailable") from exc
        if not isinstance(value, datetime) or value.tzinfo is None:
            _fail("trusted_clock_invalid")
        return value.astimezone(UTC).replace(microsecond=0)

    def _access_token(self, timeout_seconds: int) -> str:
        return self.token_source.get_token(
            client_id=self.client_id,
            resource=BLOB_OAUTH_RESOURCE,
            timeout_seconds=self.token_source._bounded_timeout(timeout_seconds),
        )

    def _url(self, kind: str, rehearsal_id: str) -> str:
        if kind not in MARKER_KINDS or not REHEARSAL_RE.fullmatch(rehearsal_id):
            _fail("marker_path_invalid")
        container = self.config.marker_containers[kind]
        name = f"{rehearsal_id}{self.config.marker_blob_name_suffix}"
        return f"https://{self.config.marker_account_host}/{container}/{name}"

    def _list_url(self, marker: str | None) -> str:
        query: dict[str, str | int] = {
            "restype": "container",
            "comp": "list",
            "maxresults": self.config.marker_list_page_size,
        }
        if marker:
            query["marker"] = marker
        return (
            f"https://{self.config.marker_account_host}/"
            f"{self.config.marker_containers['intent']}?"
            f"{urllib.parse.urlencode(query)}"
        )

    def _validate_url(self, url: str) -> None:
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != self.config.marker_account_host
            or parsed.netloc != self.config.marker_account_host
            or parsed.fragment
            or not parsed.path.startswith("/")
            or "//" in parsed.path
        ):
            _fail("blob_request_target_invalid")

    def _client_request_id(self, method: str, url: str, body: bytes | None) -> str:
        material = b"\0".join(
            [method.encode(), url.encode(), hashlib.sha256(body or b"").digest()]
        )
        digest = hashlib.sha256(material).hexdigest()
        return "-".join(
            [digest[:8], digest[8:12], digest[12:16], digest[16:20], digest[20:32]]
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        max_response_bytes: int,
        exact_marker_get: bool = False,
    ) -> BlobResponse:
        method = method.upper()
        mutating = method == "PUT"
        self._validate_url(url)
        status: int | None = None
        dispatched = False
        try:
            with self.deadline(self.config.marker_request_timeout_seconds), self.lock:
                started = _clock_value(self.monotonic, "blob_transport_clock_invalid")
                token = self._access_token(
                    _remaining_timeout(
                        self.monotonic,
                        started=started,
                        limit=self.config.marker_request_timeout_seconds,
                        code="blob_transport_clock_invalid",
                    )
                )
                remaining = _remaining_timeout(
                    self.monotonic,
                    started=started,
                    limit=self.config.marker_request_timeout_seconds,
                    code="blob_transport_clock_invalid",
                )
                headers = {
                    "Accept": (
                        "application/json" if exact_marker_get else "application/xml"
                    ),
                    "Accept-Encoding": "identity",
                    "Authorization": f"Bearer {token}",
                    "x-ms-client-request-id": self._client_request_id(
                        method, url, body
                    ),
                    "x-ms-date": format_datetime(self._trusted_now(), usegmt=True),
                    "x-ms-version": self.config.marker_service_version,
                }
                if mutating:
                    assert body is not None
                    content_md5 = base64.b64encode(
                        hashlib.md5(body, usedforsecurity=False).digest()
                    ).decode()
                    headers.update(
                        {
                            "Accept": "application/json",
                            "Content-Length": str(len(body)),
                            "Content-MD5": content_md5,
                            "Content-Type": "application/json",
                            "If-None-Match": "*",
                            "x-ms-blob-content-md5": content_md5,
                            "x-ms-blob-type": "BlockBlob",
                        }
                    )
                request = urllib.request.Request(
                    url, data=body, headers=headers, method=method
                )
                dispatched = True
                try:
                    response = self.opener.open(request, timeout=remaining)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    try:
                        observed_status = getattr(
                            response, "status", getattr(response, "code", None)
                        )
                        status = (
                            observed_status if type(observed_status) is int else None
                        )
                    except Exception:
                        status = None
                    try:
                        final_url = response.geturl()
                    except Exception:
                        final_url = None
                    if final_url != url or status is not None and 300 <= status < 400:
                        _fail("blob_redirect_refused")
                    wire = _read_wire_response(
                        response,
                        expected_url=url,
                        maximum_bytes=max_response_bytes,
                    )
                status = wire.status
                if 300 <= status < 400:
                    _fail("blob_redirect_refused")
                exposed_body = wire.body if 200 <= status < 300 else b""
                return BlobResponse(status, exposed_body, wire.headers)
        except (MarkerOutcomeUnknown, MarkerPresentInvalid):
            raise
        except SafeSubmitError as error:
            if mutating and dispatched:
                raise MarkerOutcomeUnknown from error
            if exact_marker_get and status == 200:
                code = (
                    "marker_body_too_large"
                    if error.code == "http_response_too_large"
                    else "marker_response_invalid"
                )
                raise MarkerPresentInvalid(code) from error
            if error.code == "http_response_too_large":
                _fail("blob_response_too_large")
            if error.code.startswith("http_response_"):
                raise MarkerOutcomeUnknown from error
            raise
        except Exception as exc:
            if mutating:
                raise MarkerOutcomeUnknown from exc
            if exact_marker_get and status == 200:
                raise MarkerPresentInvalid("marker_response_invalid") from exc
            raise MarkerOutcomeUnknown from exc

    def _verify_marker(self, response: BlobResponse) -> bytes:
        headers = response.headers
        try:
            content_length = int(headers["content-length"])
            last_modified = parsedate_to_datetime(headers["last-modified"])
            retained_until = parsedate_to_datetime(
                headers["x-ms-immutability-policy-until-date"]
            )
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise MarkerPresentInvalid("marker_properties_unproven") from exc
        expected_md5 = base64.b64encode(
            hashlib.md5(response.body, usedforsecurity=False).digest()
        ).decode()
        if (
            content_length != len(response.body)
            or headers.get("content-md5") != expected_md5
            or headers.get("content-type", "").split(";", 1)[0].lower()
            != "application/json"
            or not ETAG_RE.fullmatch(headers.get("etag", ""))
            or headers.get("x-ms-blob-type") != "BlockBlob"
            or headers.get("x-ms-version") != self.config.marker_service_version
            or headers.get("x-ms-server-encrypted", "").lower() != "true"
            or headers.get("x-ms-immutability-policy-mode", "").lower() != "locked"
            or headers.get("x-ms-legal-hold", "").lower() != "true"
            or last_modified.tzinfo is None
            or retained_until.tzinfo is None
            or retained_until.astimezone(UTC) - last_modified.astimezone(UTC)
            < timedelta(days=self.config.marker_minimum_retention_days)
        ):
            raise MarkerPresentInvalid("marker_properties_unproven")
        return response.body

    def _read_once(self, kind: str, rehearsal_id: str) -> bytes | None:
        response = self._request(
            "GET",
            self._url(kind, rehearsal_id),
            max_response_bytes=self.config.marker_max_record_bytes,
            exact_marker_get=True,
        )
        if response.status == 404:
            return None
        if response.status == 200:
            return self._verify_marker(response)
        if response.status in {408, 425, 429} or response.status >= 500:
            raise MarkerOutcomeUnknown
        _fail("marker_read_refused")

    def read(self, kind: str, rehearsal_id: str) -> bytes | None:
        indeterminate = False
        for attempt in range(self.config.marker_read_poll_attempts):
            try:
                raw = self._read_once(kind, rehearsal_id)
            except MarkerOutcomeUnknown:
                indeterminate = True
            else:
                if raw is not None:
                    return raw
                if not indeterminate:
                    return None
            if attempt + 1 < self.config.marker_read_poll_attempts:
                self.sleeper(self.config.marker_read_poll_interval_seconds)
        raise MarkerOutcomeUnknown

    def _verify_create_response(self, response: BlobResponse) -> None:
        if (
            response.status != 201
            or response.headers.get("x-ms-request-server-encrypted", "").lower()
            != "true"
            or response.headers.get("x-ms-version")
            != self.config.marker_service_version
            or not ETAG_RE.fullmatch(response.headers.get("etag", ""))
        ):
            raise MarkerOutcomeUnknown

    def create_if_absent(self, kind: str, rehearsal_id: str, raw: bytes) -> bytes:
        if self.action == "cleanup" and kind != "cleanup":
            _fail("marker_write_refused")
        if not raw or len(raw) > self.config.marker_max_record_bytes:
            _fail("marker_record_size_invalid")
        try:
            existing = self.read(kind, rehearsal_id)
        except MarkerPresentInvalid:
            raise
        if existing is not None:
            if existing != raw:
                raise MarkerConflict("marker_record_conflict")
            return existing
        try:
            response = self._request(
                "PUT",
                self._url(kind, rehearsal_id),
                body=raw,
                max_response_bytes=self.config.marker_max_record_bytes,
            )
            if response.status == 201:
                self._verify_create_response(response)
            elif 200 <= response.status < 300:
                raise MarkerOutcomeUnknown
            elif (
                response.status not in {408, 409, 412, 425, 429}
                and response.status < 500
            ):
                _fail("marker_write_refused")
        except MarkerOutcomeUnknown:
            pass
        for attempt in range(self.config.marker_read_poll_attempts):
            try:
                observed = self._read_once(kind, rehearsal_id)
            except MarkerOutcomeUnknown:
                observed = None
            if observed is not None:
                if observed != raw:
                    raise MarkerConflict("marker_record_conflict")
                return observed
            if attempt + 1 < self.config.marker_read_poll_attempts:
                self.sleeper(self.config.marker_read_poll_interval_seconds)
        raise MarkerOutcomeUnknown

    def list_intent_ids(self) -> tuple[str, ...]:
        if not self.container_listing_permitted:
            _fail("marker_list_refused")
        found: list[str] = []
        seen: set[str] = set()
        marker: str | None = None
        for page in range(self.config.marker_list_max_pages):
            response = self._request(
                "GET",
                self._list_url(marker),
                max_response_bytes=self.config.marker_max_list_response_bytes,
            )
            if response.status in {408, 425, 429} or response.status >= 500:
                raise MarkerOutcomeUnknown
            if response.status != 200:
                _fail("marker_list_refused")
            if (
                response.headers.get("x-ms-version")
                != self.config.marker_service_version
                or response.headers.get("content-type", "").split(";", 1)[0].lower()
                != "application/xml"
                or b"<!DOCTYPE" in response.body.upper()
                or b"<!ENTITY" in response.body.upper()
            ):
                _fail("marker_list_invalid")
            try:
                root = ET.fromstring(response.body)
            except ET.ParseError as exc:
                raise SafeSubmitError("marker_list_invalid") from exc
            if root.tag != "EnumerationResults":
                _fail("marker_list_invalid")
            for entry in root.findall("./Blobs/Blob/Name"):
                name = entry.text or ""
                suffix = self.config.marker_blob_name_suffix
                rehearsal_id = name.removesuffix(suffix)
                if (
                    not name.endswith(suffix)
                    or not REHEARSAL_RE.fullmatch(rehearsal_id)
                    or rehearsal_id in seen
                ):
                    _fail("marker_list_invalid")
                seen.add(rehearsal_id)
                found.append(rehearsal_id)
                if len(found) > self.config.marker_list_max_records:
                    _fail("marker_list_bound_exceeded")
            next_marker = root.findtext("./NextMarker") or ""
            if not next_marker:
                return tuple(sorted(found))
            if len(next_marker) > 1_024 or any(ord(char) < 32 for char in next_marker):
                _fail("marker_list_invalid")
            marker = next_marker
            if page + 1 < self.config.marker_list_max_pages:
                self.sleeper(self.config.marker_read_poll_interval_seconds)
        _fail("marker_list_bound_exceeded")


_RESTORE_EVIDENCE_BINDING_SEAL = object()


@dataclass(frozen=True, slots=True, init=False, repr=False)
class RestoreEvidenceBinding:
    """Composite-verifier output, not proof against hostile in-process code."""

    schema_version: int
    binding_policy_id: str
    evidence_manifest_policy_id: str
    evidence_manifest_policy_sha256: str
    manifest_id: str
    manifest_file_sha256: str
    bundle_index_sha256: str
    evidence_set_sha256: str
    source_sha: str
    candidate_sha: str
    source_server_resource_id: str
    source_database_name: str
    approved_restore_time: datetime
    observed_at: datetime
    expires_at: datetime
    candidate_approval_pr_number: int
    candidate_protected_master_sha: str
    candidate_schema_revision: str
    candidate_receipt_sha256: str
    inventory_receipt_sha256: str
    quiescence_receipt_sha256: str
    server_scope_receipt_sha256: str
    repository: str
    protected_ref: str
    workflow_path: str
    workflow_sha: str
    run_id: str
    run_attempt: str
    event_name: str
    signer_issuer: str
    signer_subject: str
    attestation_receipt_sha256: str
    provider_receipt_sha256: str
    server_scope_authentication_receipt_sha256: str
    _binding_seal: object = dataclass_field(repr=False, compare=False)

    def _is_module_minted_binding(self) -> bool:
        return (
            type(self) is RestoreEvidenceBinding
            and getattr(self, "_binding_seal", None) is _RESTORE_EVIDENCE_BINDING_SEAL
        )


@dataclass(frozen=True, slots=True)
class RestoreRequest:
    rehearsal_id: str
    evidence_binding: RestoreEvidenceBinding
    cleanup_not_after: datetime
    repository: str
    workflow_sha: str
    run_id: str
    run_attempt: str


@dataclass(frozen=True, slots=True)
class SubmitOutcome:
    status: str
    target_resource_id: str
    intent_sha256: str
    target_observed: bool
    cleanup_required: bool


def _validate_safe_policy(policy: dict[str, Any], *, executable: bool) -> None:
    if set(policy) != {
        "schema_version",
        "policy_id",
        "envelope_policy_id",
        "envelope_policy_sha256",
        "evidence_binding",
        "activation",
        "arm",
        "marker_store",
        "operation_authority",
        "outcomes",
        "retention",
    } or (
        policy.get("schema_version") != 4
        or policy.get("policy_id") != "cosmaneura-staging-restore-safe-submit-v4"
        or policy.get("envelope_policy_id") != "cosmaneura-staging-restore-envelope-v1"
        or policy.get("envelope_policy_sha256") != f"sha256:{ENVELOPE_POLICY_SHA256}"
    ):
        _fail("safe_submit_policy_identity_mismatch")
    if policy.get("evidence_binding") != {
        "schema_version": 1,
        "policy_id": "cosmaneura-staging-restore-evidence-binding-v1",
        "manifest_policy_id": "cosmaneura-staging-evidence-manifest-v2",
        "manifest_policy_sha256": f"sha256:{EVIDENCE_MANIFEST_POLICY_SHA256}",
        "repository": "CosmaNeura/cosmaneura",
        "protected_ref": "refs/heads/master",
        "workflow_path": ".github/workflows/staging_restore_rehearsal.yml",
        "event_name": "workflow_dispatch",
        "signer_issuer": "https://token.actions.githubusercontent.com",
        "signer_subject": (
            "repo:CosmaNeura/cosmaneura:environment:staging-restore-provision"
        ),
        "source_database_name": "staging",
        "maximum_lifetime_seconds": 900,
    }:
        _fail("safe_submit_evidence_policy_mismatch")
    activation = policy.get("activation")
    if (
        not isinstance(activation, dict)
        or set(activation)
        != {
            "blob_marker_store_implemented",
            "blob_marker_provider_fidelity_proven",
            "private_runner_binding_implemented",
            "workflow_integrated",
            "trusted_evidence_binding_implemented",
        }
        or not all(isinstance(value, bool) for value in activation.values())
    ):
        _fail("safe_submit_activation_invalid")
    if executable and activation != {
        "blob_marker_store_implemented": True,
        "blob_marker_provider_fidelity_proven": True,
        "private_runner_binding_implemented": True,
        "workflow_integrated": True,
        "trusted_evidence_binding_implemented": True,
    }:
        _fail("safe_submit_not_activated")
    arm = policy.get("arm")
    if arm != {
        "management_host": "management.azure.com",
        "postgres_api_version": "2024-08-01",
        "request_timeout_seconds": 30,
        "poll_interval_seconds": 15,
        "restore_poll_attempts": 480,
        "cleanup_poll_attempts": 120,
        "binding_clock_skew_seconds": 300,
        "late_materialization_watch_hours": 48,
    }:
        _fail("safe_submit_arm_policy_mismatch")
    marker_store = policy.get("marker_store")
    if marker_store != {
        "kind": "azure-blob-immutable-create-only-v1",
        "account_resource_id": "CONFIGURE_EXACT_RESTORE_MARKER_STORAGE_ACCOUNT_RESOURCE_ID",
        "account_host": "CONFIGURE_EXACT_RESTORE_MARKER_STORAGE_ACCOUNT_HOST",
        "oauth_resource": BLOB_OAUTH_RESOURCE,
        "blob_service_version": BLOB_SERVICE_VERSION,
        "containers": {
            "intent": "restore-intents-v4",
            "submission": "restore-submissions-v4",
            "cleanup": "restore-cleanups-v4",
        },
        "blob_name_suffix": ".json",
        "request_timeout_seconds": 30,
        "read_poll_attempts": 20,
        "read_poll_interval_seconds": 15,
        "max_record_bytes": 65_536,
        "list_page_size": 500,
        "list_max_pages": 20,
        "list_max_records": 10_000,
        "max_list_response_bytes": 1_048_576,
        "account_posture": {
            "resource_group_id": "CONFIGURE_EXACT_RESTORE_MARKER_CONTROL_RESOURCE_GROUP_ID",
            "sku": "Standard_ZRS",
            "kind": "StorageV2",
            "public_network_access": "Disabled",
            "shared_key_access": False,
            "allow_blob_public_access": False,
            "default_to_oauth_authentication": True,
            "https_only": True,
            "minimum_tls_version": "TLS1_2",
            "hierarchical_namespace": False,
            "blob_versioning": False,
            "nfs_v3": False,
            "sftp": False,
            "local_users": False,
            "point_in_time_restore": False,
            "last_access_time_tracking": False,
            "management_lock_id": "CONFIGURE_EXACT_RESTORE_MARKER_MANAGEMENT_LOCK_ID",
            "management_lock_level": "ReadOnly",
        },
        "network": {
            "private_endpoint_resource_id": "CONFIGURE_EXACT_RESTORE_MARKER_PRIVATE_ENDPOINT_RESOURCE_ID",
            "private_dns_zone_resource_id": "CONFIGURE_EXACT_RESTORE_MARKER_PRIVATE_DNS_ZONE_RESOURCE_ID",
            "private_dns_link_resource_id": "CONFIGURE_EXACT_RESTORE_MARKER_PRIVATE_DNS_LINK_RESOURCE_ID",
            "runner_subnet_resource_id": "CONFIGURE_EXACT_RESTORE_MARKER_RUNNER_SUBNET_RESOURCE_ID",
            "runner_environment": "CONFIGURE_EXACT_RESTORE_MARKER_RUNNER_ENVIRONMENT",
        },
        "rbac": {
            "actions": [],
            "create_read_data_actions": "CONFIGURE_EXACT_RESTORE_MARKER_CREATE_READ_DATA_ACTIONS",
            "read_data_actions": [
                "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"
            ],
            "create_read_role_definition_id": "CONFIGURE_EXACT_RESTORE_MARKER_CREATE_READ_ROLE_DEFINITION_ID",
            "create_read_role_permissions_sha256": "CONFIGURE_EXACT_RESTORE_MARKER_CREATE_READ_ROLE_PERMISSIONS_SHA256",
            "read_role_definition_id": "CONFIGURE_EXACT_RESTORE_MARKER_READ_ROLE_DEFINITION_ID",
            "read_role_permissions_sha256": "CONFIGURE_EXACT_RESTORE_MARKER_READ_ROLE_PERMISSIONS_SHA256",
            "provision_intent_assignment_id": "CONFIGURE_EXACT_RESTORE_MARKER_PROVISION_INTENT_ROLE_ASSIGNMENT_ID",
            "provision_submission_assignment_id": "CONFIGURE_EXACT_RESTORE_MARKER_PROVISION_SUBMISSION_ROLE_ASSIGNMENT_ID",
            "provision_cleanup_tombstone_assignment_id": "CONFIGURE_EXACT_RESTORE_PROVISION_CLEANUP_TOMBSTONE_ROLE_ASSIGNMENT_ID",
            "cleanup_intent_read_assignment_id": "CONFIGURE_EXACT_RESTORE_MARKER_CLEANUP_INTENT_READ_ROLE_ASSIGNMENT_ID",
            "cleanup_submission_read_assignment_id": "CONFIGURE_EXACT_RESTORE_MARKER_CLEANUP_SUBMISSION_READ_ROLE_ASSIGNMENT_ID",
            "cleanup_marker_assignment_id": "CONFIGURE_EXACT_RESTORE_MARKER_CLEANUP_MARKER_ROLE_ASSIGNMENT_ID",
        },
        "immutability": {
            "scope": "container",
            "mode": "Locked",
            "minimum_retention_days": 90,
            "legal_hold_tag": "restorecontrolactive",
            "allow_protected_append_writes": False,
            "allow_protected_append_writes_all": False,
            "intent_policy_resource_id": "CONFIGURE_EXACT_RESTORE_MARKER_INTENT_IMMUTABILITY_POLICY_RESOURCE_ID",
            "submission_policy_resource_id": "CONFIGURE_EXACT_RESTORE_MARKER_SUBMISSION_IMMUTABILITY_POLICY_RESOURCE_ID",
            "cleanup_policy_resource_id": "CONFIGURE_EXACT_RESTORE_MARKER_CLEANUP_IMMUTABILITY_POLICY_RESOURCE_ID",
            "configuration_evidence_sha256": "CONFIGURE_EXACT_RESTORE_MARKER_IMMUTABILITY_CONFIGURATION_EVIDENCE_SHA256",
        },
        "provider_fidelity": {
            "openapi_commit_sha": "CONFIGURE_EXACT_RESTORE_MARKER_OPENAPI_COMMIT_SHA",
            "conditional_create_concurrency_evidence_sha256": "CONFIGURE_EXACT_RESTORE_MARKER_CONDITIONAL_CREATE_CONCURRENCY_EVIDENCE_SHA256",
            "strong_consistency_evidence_sha256": "CONFIGURE_EXACT_RESTORE_MARKER_STRONG_CONSISTENCY_EVIDENCE_SHA256",
            "timeout_reconciliation_evidence_sha256": "CONFIGURE_EXACT_RESTORE_MARKER_TIMEOUT_RECONCILIATION_EVIDENCE_SHA256",
            "identity_authorization_evidence_sha256": "CONFIGURE_EXACT_RESTORE_MARKER_IDENTITY_AUTHORIZATION_EVIDENCE_SHA256",
            "block_blob_create_authorization_evidence_sha256": "CONFIGURE_EXACT_RESTORE_MARKER_BLOCK_BLOB_CREATE_AUTHORIZATION_EVIDENCE_SHA256",
            "overwrite_delete_denial_evidence_sha256": "CONFIGURE_EXACT_RESTORE_MARKER_OVERWRITE_DELETE_DENIAL_EVIDENCE_SHA256",
            "private_reachability_evidence_sha256": "CONFIGURE_EXACT_RESTORE_MARKER_PRIVATE_REACHABILITY_EVIDENCE_SHA256",
            "review_evidence_sha256": "CONFIGURE_EXACT_RESTORE_MARKER_PROVIDER_FIDELITY_REVIEW_SHA256",
        },
    }:
        _fail("safe_submit_marker_store_policy_mismatch")
    if policy.get("operation_authority") != {
        "provision": {
            "entrypoint": "staging_restore_safe_submit.SafeRestoreSubmitter.submit",
            "arm_resource_delete_permitted": False,
            "cleanup_handoff": "exact_dominant_tombstone_create_and_read_back",
            "cleanup_tombstone_required_after_partial_or_unknown_outcome": True,
        },
        "cleanup": {
            "entrypoint": (
                "staging_restore_safe_submit.SafeRestoreSubmitter.cleanup_intent"
            ),
            "arm_resource_delete_permitted": True,
            "exact_dominant_tombstone_required_before_delete": True,
        },
    }:
        _fail("safe_submit_operation_authority_mismatch")
    if policy.get("outcomes") != {
        "ready_state": "Ready",
        "failed_states": ["Disabled", "Failed"],
    }:
        _fail("safe_submit_outcome_policy_mismatch")
    if policy.get("retention") != {
        "delete_markers_automatically": False,
        "protected_kinds": ["intent", "submission", "cleanup"],
        "reason": "Preserve every authorization and cleanup tombstone; deleting a current cleanup marker could permit a later retry to submit the deterministic target.",
    }:
        _fail("safe_submit_retention_policy_mismatch")


def validate_canonical_contracts() -> tuple[dict[str, Any], dict[str, Any]]:
    envelope = _read_object(ENVELOPE_POLICY_PATH, ENVELOPE_POLICY_SHA256)
    safe = _read_object(SAFE_SUBMIT_POLICY_PATH, SAFE_SUBMIT_POLICY_SHA256)
    evidence = _read_object(
        EVIDENCE_MANIFEST_POLICY_PATH, EVIDENCE_MANIFEST_POLICY_SHA256
    )
    if (
        evidence.get("schema_version") != 2
        or evidence.get("policy_id") != "cosmaneura-staging-evidence-manifest-v2"
    ):
        _fail("canonical_evidence_policy_identity_mismatch")
    if envelope.get("trusted_evidence_binding_implemented") is not False:
        _fail("canonical_envelope_activation_changed")
    if tuple(sorted(_placeholder_values(envelope))) != EXPECTED_PLACEHOLDERS:
        _fail("canonical_envelope_blockers_changed")
    _validate_safe_policy(safe, executable=False)
    if safe["activation"] != {
        "blob_marker_store_implemented": False,
        "blob_marker_provider_fidelity_proven": False,
        "private_runner_binding_implemented": False,
        "workflow_integrated": False,
        "trusted_evidence_binding_implemented": False,
    }:
        _fail("canonical_safe_submit_activation_changed")
    return envelope, safe


def config_from_policies(
    envelope: dict[str, Any], safe: dict[str, Any], *, executable: bool
) -> SafeSubmitConfig:
    canonical_envelope, canonical_safe = validate_canonical_contracts()
    if executable and (
        _sha256(_canonical(envelope)) != _sha256(_canonical(canonical_envelope))
        or _sha256(_canonical(safe)) != _sha256(_canonical(canonical_safe))
    ):
        _fail("executable_policy_not_canonical")
    if executable:
        envelope, safe = canonical_envelope, canonical_safe
    _validate_safe_policy(safe, executable=executable)
    if (
        envelope.get("policy_id") != safe["envelope_policy_id"]
        or envelope.get("repository") != "CosmaNeura/cosmaneura"
        or envelope.get("protected_ref") != "refs/heads/master"
    ):
        _fail("envelope_policy_identity_mismatch")
    if executable and (
        envelope.get("trusted_evidence_binding_implemented") is not True
        or _placeholder_values(envelope)
    ):
        _fail("envelope_policy_not_activated")
    source = envelope.get("source")
    target = envelope.get("target")
    identities = envelope.get("identities")
    provision = identities.get("provision") if isinstance(identities, dict) else None
    if (
        not isinstance(source, dict)
        or not isinstance(target, dict)
        or not isinstance(provision, dict)
    ):
        _fail("envelope_policy_shape_invalid")
    required_tags = target.get("required_tags")
    if (
        not isinstance(required_tags, dict)
        or not required_tags
        or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in required_tags.items()
        )
        or not isinstance(target.get("maximum_ttl_hours"), int)
        or not isinstance(provision.get("client_id"), str)
        or not provision["client_id"]
        or not isinstance(provision.get("principal_id"), str)
        or not provision["principal_id"]
    ):
        _fail("envelope_policy_shape_invalid")
    subscription = str(envelope.get("subscription_id", ""))
    group_id = _id(
        "subscriptions", subscription, "resourceGroups", str(target["resource_group"])
    )
    source_id = _id(
        "subscriptions",
        subscription,
        "resourceGroups",
        str(source["resource_group"]),
        "providers/Microsoft.DBforPostgreSQL/flexibleServers",
        str(source["server_name"]),
    )
    activation = safe["activation"]
    arm = safe["arm"]
    marker = safe["marker_store"]
    evidence_binding = safe["evidence_binding"]
    return SafeSubmitConfig(
        policy_id=safe["policy_id"],
        envelope_policy_id=envelope["policy_id"],
        envelope_policy_sha256=safe["envelope_policy_sha256"],
        evidence_binding_schema_version=evidence_binding["schema_version"],
        evidence_binding_policy_id=evidence_binding["policy_id"],
        evidence_manifest_policy_id=evidence_binding["manifest_policy_id"],
        evidence_manifest_policy_sha256=evidence_binding["manifest_policy_sha256"],
        evidence_repository=evidence_binding["repository"],
        evidence_protected_ref=evidence_binding["protected_ref"],
        evidence_workflow_path=evidence_binding["workflow_path"],
        evidence_event_name=evidence_binding["event_name"],
        evidence_signer_issuer=evidence_binding["signer_issuer"],
        evidence_signer_subject=evidence_binding["signer_subject"],
        evidence_source_database_name=evidence_binding["source_database_name"],
        evidence_maximum_lifetime_seconds=evidence_binding["maximum_lifetime_seconds"],
        subscription_id=subscription,
        target_group_id=group_id,
        source_server_id=source_id,
        provision_client_id=str(provision.get("client_id", "")),
        provision_principal_id=str(provision.get("principal_id", "")),
        location=str(envelope["location"]),
        name_prefix=str(target["name_prefix"]),
        maximum_ttl_hours=int(target["maximum_ttl_hours"]),
        required_tags=dict(required_tags),
        postgres_api_version=arm["postgres_api_version"],
        request_timeout_seconds=arm["request_timeout_seconds"],
        poll_interval_seconds=arm["poll_interval_seconds"],
        restore_poll_attempts=arm["restore_poll_attempts"],
        cleanup_poll_attempts=arm["cleanup_poll_attempts"],
        binding_clock_skew_seconds=arm["binding_clock_skew_seconds"],
        late_materialization_watch_hours=arm["late_materialization_watch_hours"],
        marker_account_resource_id=marker["account_resource_id"],
        marker_account_host=marker["account_host"],
        marker_oauth_resource=marker["oauth_resource"],
        marker_service_version=marker["blob_service_version"],
        marker_containers=dict(marker["containers"]),
        marker_blob_name_suffix=marker["blob_name_suffix"],
        marker_request_timeout_seconds=marker["request_timeout_seconds"],
        marker_read_poll_attempts=marker["read_poll_attempts"],
        marker_read_poll_interval_seconds=marker["read_poll_interval_seconds"],
        marker_max_record_bytes=marker["max_record_bytes"],
        marker_list_page_size=marker["list_page_size"],
        marker_list_max_pages=marker["list_max_pages"],
        marker_list_max_records=marker["list_max_records"],
        marker_max_list_response_bytes=marker["max_list_response_bytes"],
        marker_minimum_retention_days=marker["immutability"]["minimum_retention_days"],
        ready_state=safe["outcomes"]["ready_state"],
        failed_states=frozenset(safe["outcomes"]["failed_states"]),
        execution_authorized=executable,
        blob_marker_store_implemented=activation["blob_marker_store_implemented"],
        blob_marker_provider_fidelity_proven=activation[
            "blob_marker_provider_fidelity_proven"
        ],
        private_runner_binding_implemented=activation[
            "private_runner_binding_implemented"
        ],
        trusted_evidence_binding_implemented=activation[
            "trusted_evidence_binding_implemented"
        ],
    )


@dataclass(frozen=True, slots=True)
class PolicyBoundSafeSubmitRuntime:
    """Dormant construction result for one exact provision or cleanup job."""

    action: str
    config: SafeSubmitConfig
    arm_transport: AzureArmTransport
    marker_store: AzureBlobMarkerStore


def build_policy_bound_runtime(
    *,
    config: SafeSubmitConfig,
    runtime_policy: Any,
    action: str,
    environment: Mapping[str, str] | None = None,
    identity_opener: _HttpOpener | None = None,
    arm_opener: _HttpOpener | None = None,
    blob_opener: _HttpOpener | None = None,
    epoch_clock: Callable[[], float] = time.time,
    monotonic: Callable[[], float] = time.monotonic,
    deadline: _DeadlineFactory = _wall_clock_deadline,
    sleeper: Callable[[float], None] = sleep,
    trusted_clock: Callable[[], datetime] = _now,
) -> PolicyBoundSafeSubmitRuntime:
    """Bind both transports to the runtime policy's selected job UAMI."""

    if (
        type(config) is not SafeSubmitConfig
        or type(action) is not str
        or action not in RUNTIME_ACTIONS
        or not config.execution_authorized
        or not config.blob_marker_store_implemented
        or not config.blob_marker_provider_fidelity_proven
        or not config.private_runner_binding_implemented
        or not config.trusted_evidence_binding_implemented
        or environment is not None
        and not isinstance(environment, Mapping)
    ):
        _fail("safe_submit_runtime_configuration_invalid")
    try:
        document = runtime_binding.validate_runtime_binding_policy(
            runtime_policy, executable=True
        )
        jobs = document["identities"]["jobs"]
        selected_identity = jobs[action]
        provision_identity = jobs["provision"]
        marker = document["private_control_marker_store"]
        egress = document["egress"]
        allowed = egress["allowed_destinations"]
        arm_egress = allowed["azure_resource_manager"]
        identity_egress = allowed["managed_identity"]
        marker_egress = allowed["private_control_marker_blob"]
        retrieval = document["runtime_record_ingress"]["retrieval"]
    except (KeyError, TypeError, runtime_binding.RuntimeBindingFailure):
        raise SafeSubmitError("safe_submit_runtime_policy_invalid") from None
    if (
        config.provision_client_id != provision_identity["client_id"]
        or config.provision_principal_id != provision_identity["principal_id"]
        or config.policy_id != marker["policy_id"]
        or config.marker_account_resource_id != marker["account_resource_id"]
        or config.marker_account_host != marker["account_host"]
        or config.marker_oauth_resource != marker["oauth_resource"]
        or config.marker_containers != marker["container_names"]
        or config.marker_service_version != retrieval["service_api_version"]
        or marker["allowed_job_identity_resource_ids"]
        != [jobs[name]["resource_id"] for name in ("provision", "cleanup")]
        or marker["container_listing_permitted"] is not False
        or marker["caller_path_or_uri_override_permitted"] is not False
        or egress["default_action"] != "deny"
        or egress["implicit_destinations_permitted"] is not False
        or egress["default_credential_chain_permitted"] is not False
        or egress["ambient_proxy_configuration_permitted"] is not False
        or arm_egress
        != {
            "scheme": "https",
            "host": "management.azure.com",
            "port": 443,
            "oauth_resource": ARM_OAUTH_RESOURCE,
            "redirects_permitted": False,
        }
        or identity_egress
        != {
            "scheme": "http",
            "endpoint_source": "IDENTITY_ENDPOINT",
            "secret_header_source": "IDENTITY_HEADER",
            "path": "/msi/token",
            "address_classes": ["loopback", "link-local"],
            "dns_names_permitted": ["localhost"],
            "redirects_permitted": False,
        }
        or marker_egress
        != {
            "scheme": "https",
            "host_source": "private_control_marker_store.account_host",
            "port": 443,
            "private_endpoint_required": True,
        }
        or retrieval["managed_identity_endpoint_source"]
        != "system-environment.IDENTITY_ENDPOINT"
        or retrieval["managed_identity_secret_header_source"]
        != "system-environment.IDENTITY_HEADER"
        or retrieval["redirects_permitted"] is not False
        or retrieval["ambient_credential_permitted"] is not False
        or retrieval["ambient_proxy_permitted"] is not False
        or retrieval["minimum_tls_version"] != "1.2"
        or retrieval["runtime_ca_bundle_path"] != str(RUNTIME_CA_BUNDLE_PATH)
        or retrieval["content_encoding"] != "identity"
        or retrieval["content_length_required"] is not True
        or type(selected_identity["client_id"]) is not str
        or UUID_RE.fullmatch(selected_identity["client_id"]) is None
        or type(selected_identity["resource_id"]) is not str
        or not selected_identity["resource_id"].startswith("/subscriptions/")
        or action == "provision"
        and selected_identity["delete_actions"] != []
        or action == "cleanup"
        and "Microsoft.DBforPostgreSQL/flexibleServers/delete"
        not in selected_identity["delete_actions"]
    ):
        _fail("safe_submit_runtime_policy_mismatch")
    source_environment = os.environ if environment is None else environment
    try:
        identity_endpoint = source_environment.get("IDENTITY_ENDPOINT", "")
        identity_header = source_environment.get("IDENTITY_HEADER", "")
    except Exception:
        raise SafeSubmitError("safe_submit_runtime_environment_invalid") from None
    if type(identity_endpoint) is not str or type(identity_header) is not str:
        _fail("safe_submit_runtime_environment_invalid")
    tls_context: ssl.SSLContext | None = None
    if identity_opener is None or arm_opener is None or blob_opener is None:
        tls_context = _runtime_tls_context(Path(retrieval["runtime_ca_bundle_path"]))
    source = ContainerAppsManagedIdentityTokenSource(
        client_id=selected_identity["client_id"],
        identity_endpoint=identity_endpoint,
        identity_header=identity_header,
        service_api_version=retrieval["managed_identity_api_version"],
        maximum_response_bytes=retrieval["managed_identity_response_maximum_bytes"],
        expiry_skew_seconds=retrieval["managed_identity_expiry_skew_seconds"],
        maximum_lifetime_seconds=retrieval[
            "managed_identity_maximum_token_lifetime_seconds"
        ],
        maximum_timeout_seconds=retrieval["maximum_request_timeout_seconds"],
        opener=identity_opener or _hardened_opener(tls_context=tls_context),
        clock=epoch_clock,
    )
    arm = AzureArmTransport(
        config,
        source,
        action=action,
        client_id=selected_identity["client_id"],
        opener=arm_opener or _hardened_opener(tls_context=tls_context),
        monotonic=monotonic,
        deadline=deadline,
    )
    store = AzureBlobMarkerStore(
        config,
        source,
        action=action,
        client_id=selected_identity["client_id"],
        opener=blob_opener or _hardened_opener(tls_context=tls_context),
        sleeper=sleeper,
        clock=trusted_clock,
        monotonic=monotonic,
        deadline=deadline,
        container_listing_permitted=marker["container_listing_permitted"],
    )
    return PolicyBoundSafeSubmitRuntime(
        action=action,
        config=config,
        arm_transport=arm,
        marker_store=store,
    )


class SafeRestoreSubmitter:
    """Model marker persistence, one exact PUT, and identity reconciliation."""

    def __init__(
        self,
        transport: ArmTransport,
        marker_store: MarkerStore,
        config: SafeSubmitConfig,
        *,
        sleeper: Callable[[float], None] = sleep,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self.transport = transport
        self.marker_store = marker_store
        self.config = config
        self.sleeper = sleeper
        self.clock = clock

    def _trusted_now(self) -> datetime:
        try:
            value = self.clock()
        except Exception as exc:
            raise SafeSubmitError("trusted_clock_unavailable") from exc
        if not isinstance(value, datetime) or value.tzinfo is None:
            _fail("trusted_clock_invalid")
        return value.astimezone(UTC).replace(microsecond=0)

    def _require_marker_fidelity(self) -> None:
        if not self.config.execution_authorized:
            _fail("safe_submit_not_activated")
        if not self.config.blob_marker_store_implemented:
            _fail("blob_marker_store_not_implemented")
        if not self.config.blob_marker_provider_fidelity_proven:
            _fail("blob_marker_provider_fidelity_unproven")
        if not self.config.private_runner_binding_implemented:
            _fail("blob_marker_private_runner_unproven")
        if not self.config.trusted_evidence_binding_implemented:
            _fail("trusted_evidence_binding_unproven")

    def _request(
        self,
        method: str,
        resource_id: str,
        *,
        api_version: str,
        body: dict[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> ArmResponse | object:
        try:
            return self.transport.request(
                method,
                resource_id,
                api_version=api_version,
                body=body,
                headers=headers,
                timeout_seconds=self.config.request_timeout_seconds,
            )
        except ArmOutcomeUnknown:
            return UNKNOWN

    @staticmethod
    def _binding_record(binding: RestoreEvidenceBinding) -> dict[str, Any]:
        if (
            type(binding) is not RestoreEvidenceBinding
            or not binding._is_module_minted_binding()
        ):
            _fail("evidence_binding_type_invalid")
        return {
            "schema_version": binding.schema_version,
            "binding_policy_id": binding.binding_policy_id,
            "evidence_manifest_policy_id": binding.evidence_manifest_policy_id,
            "evidence_manifest_policy_sha256": binding.evidence_manifest_policy_sha256,
            "manifest_id": binding.manifest_id,
            "manifest_file_sha256": binding.manifest_file_sha256,
            "bundle_index_sha256": binding.bundle_index_sha256,
            "evidence_set_sha256": binding.evidence_set_sha256,
            "source_sha": binding.source_sha,
            "candidate_sha": binding.candidate_sha,
            "source_server_resource_id": binding.source_server_resource_id,
            "source_database_name": binding.source_database_name,
            "approved_restore_time": _iso(binding.approved_restore_time),
            "observed_at": _iso(binding.observed_at),
            "expires_at": _iso(binding.expires_at),
            "candidate_approval_pr_number": binding.candidate_approval_pr_number,
            "candidate_protected_master_sha": binding.candidate_protected_master_sha,
            "candidate_schema_revision": binding.candidate_schema_revision,
            "candidate_receipt_sha256": binding.candidate_receipt_sha256,
            "inventory_receipt_sha256": binding.inventory_receipt_sha256,
            "quiescence_receipt_sha256": binding.quiescence_receipt_sha256,
            "server_scope_receipt_sha256": binding.server_scope_receipt_sha256,
            "repository": binding.repository,
            "protected_ref": binding.protected_ref,
            "workflow_path": binding.workflow_path,
            "workflow_sha": binding.workflow_sha,
            "run_id": binding.run_id,
            "run_attempt": binding.run_attempt,
            "event_name": binding.event_name,
            "signer_issuer": binding.signer_issuer,
            "signer_subject": binding.signer_subject,
            "attestation_receipt_sha256": binding.attestation_receipt_sha256,
            "provider_receipt_sha256": binding.provider_receipt_sha256,
            "server_scope_authentication_receipt_sha256": (
                binding.server_scope_authentication_receipt_sha256
            ),
        }

    def _validate_evidence_record(
        self, evidence: object, *, require_fresh: bool
    ) -> tuple[datetime, datetime, datetime]:
        required = {
            "schema_version",
            "binding_policy_id",
            "evidence_manifest_policy_id",
            "evidence_manifest_policy_sha256",
            "manifest_id",
            "manifest_file_sha256",
            "bundle_index_sha256",
            "evidence_set_sha256",
            "source_sha",
            "candidate_sha",
            "source_server_resource_id",
            "source_database_name",
            "approved_restore_time",
            "observed_at",
            "expires_at",
            "candidate_approval_pr_number",
            "candidate_protected_master_sha",
            "candidate_schema_revision",
            "candidate_receipt_sha256",
            "inventory_receipt_sha256",
            "quiescence_receipt_sha256",
            "server_scope_receipt_sha256",
            "repository",
            "protected_ref",
            "workflow_path",
            "workflow_sha",
            "run_id",
            "run_attempt",
            "event_name",
            "signer_issuer",
            "signer_subject",
            "attestation_receipt_sha256",
            "provider_receipt_sha256",
            "server_scope_authentication_receipt_sha256",
        }
        if not isinstance(evidence, dict) or set(evidence) != required:
            _fail("evidence_binding_invalid")
        digests = (
            "evidence_manifest_policy_sha256",
            "manifest_file_sha256",
            "bundle_index_sha256",
            "evidence_set_sha256",
            "candidate_receipt_sha256",
            "inventory_receipt_sha256",
            "quiescence_receipt_sha256",
            "server_scope_receipt_sha256",
            "attestation_receipt_sha256",
            "provider_receipt_sha256",
            "server_scope_authentication_receipt_sha256",
        )
        approval_pr = evidence["candidate_approval_pr_number"]
        if (
            isinstance(evidence["schema_version"], bool)
            or evidence["schema_version"] != self.config.evidence_binding_schema_version
            or evidence["binding_policy_id"] != self.config.evidence_binding_policy_id
            or evidence["evidence_manifest_policy_id"]
            != self.config.evidence_manifest_policy_id
            or evidence["evidence_manifest_policy_sha256"]
            != self.config.evidence_manifest_policy_sha256
            or not isinstance(evidence["manifest_id"], str)
            or MANIFEST_ID_RE.fullmatch(evidence["manifest_id"]) is None
            or any(
                not isinstance(evidence[field], str)
                or DIGEST_RE.fullmatch(evidence[field]) is None
                for field in digests
            )
            or any(
                not isinstance(evidence[field], str)
                or SHA_RE.fullmatch(evidence[field]) is None
                for field in (
                    "source_sha",
                    "candidate_sha",
                    "candidate_protected_master_sha",
                    "workflow_sha",
                )
            )
            or _lower_id(evidence["source_server_resource_id"])
            != _lower_id(self.config.source_server_id)
            or evidence["source_database_name"]
            != self.config.evidence_source_database_name
            or isinstance(approval_pr, bool)
            or not isinstance(approval_pr, int)
            or approval_pr <= 0
            or not isinstance(evidence["candidate_schema_revision"], str)
            or REVISION_RE.fullmatch(evidence["candidate_schema_revision"]) is None
            or evidence["candidate_protected_master_sha"] != evidence["workflow_sha"]
            or evidence["repository"] != self.config.evidence_repository
            or evidence["protected_ref"] != self.config.evidence_protected_ref
            or evidence["workflow_path"] != self.config.evidence_workflow_path
            or evidence["event_name"] != self.config.evidence_event_name
            or evidence["signer_issuer"] != self.config.evidence_signer_issuer
            or evidence["signer_subject"] != self.config.evidence_signer_subject
            or not isinstance(evidence["run_id"], str)
            or RUN_RE.fullmatch(evidence["run_id"]) is None
            or not isinstance(evidence["run_attempt"], str)
            or RUN_RE.fullmatch(evidence["run_attempt"]) is None
        ):
            _fail("evidence_binding_invalid")
        restore = _parse_utc(
            evidence["approved_restore_time"], "evidence_binding_invalid"
        )
        observed = _parse_utc(evidence["observed_at"], "evidence_binding_invalid")
        expires = _parse_utc(evidence["expires_at"], "evidence_binding_invalid")
        if (
            restore != observed
            or expires <= observed
            or (expires - observed).total_seconds()
            > self.config.evidence_maximum_lifetime_seconds
        ):
            _fail("evidence_binding_invalid")
        if require_fresh:
            now = self._trusted_now()
            if observed > now:
                _fail("evidence_observed_in_future")
            if now >= expires:
                _fail("evidence_expired")
        return restore, observed, expires

    def _authorization_id(self, evidence: dict[str, Any]) -> str:
        payload = {
            "schema_version": 1,
            "safe_submit_policy_id": self.config.policy_id,
            "evidence_binding": evidence,
        }
        return f"sha256:{_sha256(_canonical(payload))}"

    def _validate_request(
        self, request: RestoreRequest, *, require_fresh: bool = True
    ) -> dict[str, Any]:
        if not REHEARSAL_RE.fullmatch(request.rehearsal_id):
            _fail("rehearsal_id_invalid")
        evidence = self._binding_record(request.evidence_binding)
        _restore, created, _expires = self._validate_evidence_record(
            evidence, require_fresh=require_fresh
        )
        if (
            request.rehearsal_id
            != self._authorization_id(evidence).removeprefix("sha256:")[:12]
            or request.repository != evidence["repository"]
            or request.workflow_sha != evidence["workflow_sha"]
            or request.run_id != evidence["run_id"]
            or request.run_attempt != evidence["run_attempt"]
            or not SHA_RE.fullmatch(request.workflow_sha)
            or not RUN_RE.fullmatch(request.run_id)
            or not RUN_RE.fullmatch(request.run_attempt)
        ):
            _fail("workflow_identity_invalid")
        cleanup = _parse_utc(
            _iso(request.cleanup_not_after), "cleanup_not_after_invalid"
        )
        if cleanup <= created:
            _fail("restore_timeline_invalid")
        if (cleanup - created).total_seconds() > self.config.maximum_ttl_hours * 3600:
            _fail("cleanup_deadline_out_of_range")
        return evidence

    def _build_intent(
        self, request: RestoreRequest, *, require_fresh: bool = True
    ) -> dict[str, Any]:
        evidence = self._validate_request(request, require_fresh=require_fresh)
        cleanup_epoch = int(request.cleanup_not_after.timestamp())
        base = f"{self.config.name_prefix}-{request.rehearsal_id}-e{cleanup_epoch}"
        target_name = f"{base}-pg"
        target_id = _id(
            self.config.target_group_id,
            "providers/Microsoft.DBforPostgreSQL/flexibleServers",
            target_name,
        )
        tags = {
            **self.config.required_tags,
            "expires-epoch": str(cleanup_epoch),
            "rehearsal-id": request.rehearsal_id,
        }
        return {
            "schema_version": 4,
            "policy_id": self.config.policy_id,
            "envelope_policy_id": self.config.envelope_policy_id,
            "envelope_policy_sha256": self.config.envelope_policy_sha256,
            "authorization_id": self._authorization_id(evidence),
            "evidence_binding": evidence,
            "rehearsal_id": request.rehearsal_id,
            "source_server_resource_id": self.config.source_server_id,
            "provision_client_id": self.config.provision_client_id,
            "provision_principal_id": self.config.provision_principal_id,
            "target_server_resource_id": target_id,
            "target_server_name": target_name,
            "location": self.config.location,
            "restore_time": evidence["approved_restore_time"],
            "created_at": evidence["observed_at"],
            "cleanup_not_after": _iso(request.cleanup_not_after),
            "repository": request.repository,
            "workflow_sha": request.workflow_sha,
            "run_id": request.run_id,
            "run_attempt": request.run_attempt,
            "required_tags": tags,
        }

    def _parse_marker(self, raw: bytes, code: str) -> dict[str, Any]:
        try:
            record = json.loads(raw)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise SafeSubmitError(code) from exc
        if not isinstance(record, dict) or _canonical(record) != raw:
            _fail(code)
        return record

    def _read_marker(
        self, kind: str, rehearsal_id: str
    ) -> dict[str, Any] | object | None:
        try:
            raw = self.marker_store.read(kind, rehearsal_id)
        except MarkerOutcomeUnknown:
            return UNKNOWN
        if raw is None:
            return None
        try:
            return self._parse_marker(raw, f"{kind}_record_invalid")
        except SafeSubmitError as exc:
            raise MarkerPresentInvalid(f"{kind}_record_invalid") from exc

    def _ensure_marker(
        self, kind: str, rehearsal_id: str, record: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_marker_fidelity()
        expected = _canonical(record)
        try:
            observed = self.marker_store.create_if_absent(kind, rehearsal_id, expected)
        except MarkerOutcomeUnknown:
            _fail(f"{kind}_persistence_unproven")
        actual = self._parse_marker(observed, f"{kind}_record_invalid")
        if actual != record:
            raise MarkerConflict(f"{kind}_record_conflict")
        return actual

    def _load_intent(self, rehearsal_id: str) -> tuple[dict[str, Any], str]:
        if not REHEARSAL_RE.fullmatch(rehearsal_id):
            _fail("rehearsal_id_invalid")
        intent = self._read_marker("intent", rehearsal_id)
        if intent is UNKNOWN:
            _fail("intent_record_read_unknown")
        if intent is None:
            _fail("intent_record_missing")
        assert isinstance(intent, dict)
        self._validate_intent(intent, rehearsal_id)
        return intent, f"sha256:{_sha256(_canonical(intent))}"

    def _validate_intent(self, intent: dict[str, Any], rehearsal_id: str) -> None:
        required = {
            "schema_version",
            "policy_id",
            "envelope_policy_id",
            "envelope_policy_sha256",
            "authorization_id",
            "evidence_binding",
            "rehearsal_id",
            "source_server_resource_id",
            "provision_client_id",
            "provision_principal_id",
            "target_server_resource_id",
            "target_server_name",
            "location",
            "restore_time",
            "created_at",
            "cleanup_not_after",
            "repository",
            "workflow_sha",
            "run_id",
            "run_attempt",
            "required_tags",
        }
        if set(intent) != required or (
            intent.get("schema_version") != 4
            or intent.get("policy_id") != self.config.policy_id
            or intent.get("envelope_policy_id") != self.config.envelope_policy_id
            or intent.get("envelope_policy_sha256")
            != self.config.envelope_policy_sha256
            or intent.get("rehearsal_id") != rehearsal_id
            or _lower_id(intent.get("source_server_resource_id"))
            != _lower_id(self.config.source_server_id)
            or intent.get("provision_client_id") != self.config.provision_client_id
            or intent.get("provision_principal_id")
            != self.config.provision_principal_id
            or _location(intent.get("location")) != _location(self.config.location)
        ):
            _fail("intent_record_invalid")
        evidence = intent.get("evidence_binding")
        self._validate_evidence_record(evidence, require_fresh=False)
        assert isinstance(evidence, dict)
        created = _parse_utc(intent.get("created_at"), "intent_record_invalid")
        cleanup = _parse_utc(intent.get("cleanup_not_after"), "intent_record_invalid")
        restore = _parse_utc(intent.get("restore_time"), "intent_record_invalid")
        if (
            restore
            != _parse_utc(evidence["approved_restore_time"], "intent_record_invalid")
            or created != _parse_utc(evidence["observed_at"], "intent_record_invalid")
            or intent.get("authorization_id") != self._authorization_id(evidence)
            or _lower_id(intent.get("source_server_resource_id"))
            != _lower_id(evidence["source_server_resource_id"])
            or intent.get("repository") != evidence["repository"]
            or intent.get("workflow_sha") != evidence["workflow_sha"]
            or intent.get("run_id") != evidence["run_id"]
            or intent.get("run_attempt") != evidence["run_attempt"]
            or rehearsal_id
            != self._authorization_id(evidence).removeprefix("sha256:")[:12]
            or restore > created
            or cleanup <= created
            or (cleanup - created).total_seconds()
            > self.config.maximum_ttl_hours * 3600
        ):
            _fail("intent_record_invalid")

        expected_name = (
            f"{self.config.name_prefix}-{rehearsal_id}-e{int(cleanup.timestamp())}-pg"
        )
        expected_id = _id(
            self.config.target_group_id,
            "providers/Microsoft.DBforPostgreSQL/flexibleServers",
            expected_name,
        )
        expected_tags = {
            **self.config.required_tags,
            "expires-epoch": str(int(cleanup.timestamp())),
            "rehearsal-id": rehearsal_id,
        }
        if (
            intent.get("target_server_name") != expected_name
            or _lower_id(intent.get("target_server_resource_id"))
            != _lower_id(expected_id)
            or intent.get("required_tags") != expected_tags
            or intent.get("repository") != "CosmaNeura/cosmaneura"
            or not SHA_RE.fullmatch(str(intent.get("workflow_sha", "")))
            or not RUN_RE.fullmatch(str(intent.get("run_id", "")))
            or not RUN_RE.fullmatch(str(intent.get("run_attempt", "")))
        ):
            _fail("intent_record_invalid")

    def _intent_expired(self, intent: dict[str, Any]) -> bool:
        created = _parse_utc(intent.get("created_at"), "intent_record_invalid")
        now = self._trusted_now()
        if created > now + timedelta(seconds=self.config.binding_clock_skew_seconds):
            _fail("intent_created_in_future")
        cleanup = _parse_utc(intent.get("cleanup_not_after"), "intent_record_invalid")
        return cleanup <= now

    def _evidence_expired(self, intent: dict[str, Any]) -> bool:
        evidence = intent.get("evidence_binding")
        self._validate_evidence_record(evidence, require_fresh=False)
        assert isinstance(evidence, dict)
        return _parse_utc(evidence["expires_at"], "intent_record_invalid") <= (
            self._trusted_now()
        )

    def _submission_record(
        self, intent: dict[str, Any], intent_digest: str
    ) -> dict[str, Any]:
        body = self._restore_body(intent, intent_digest)
        return {
            "schema_version": 4,
            "kind": "restore-submission-authorized",
            "policy_id": self.config.policy_id,
            "rehearsal_id": intent["rehearsal_id"],
            "intent_sha256": intent_digest,
            "target_server_resource_id": intent["target_server_resource_id"],
            "restore_request_sha256": f"sha256:{_sha256(_canonical(body))}",
            "authorized_at": intent["created_at"],
        }

    def _cleanup_record(
        self, intent: dict[str, Any], intent_digest: str
    ) -> dict[str, Any]:
        return {
            "schema_version": 4,
            "kind": "restore-cleanup-authorized",
            "policy_id": self.config.policy_id,
            "rehearsal_id": intent["rehearsal_id"],
            "intent_sha256": intent_digest,
            "target_server_resource_id": intent["target_server_resource_id"],
            "authorized_at": intent["created_at"],
        }

    def _cleanup_requested(
        self, intent: dict[str, Any], intent_digest: str
    ) -> bool | object:
        try:
            raw = self.marker_store.read("cleanup", intent["rehearsal_id"])
        except MarkerOutcomeUnknown:
            return UNKNOWN
        except Exception:
            return CLEANUP_PRESENT_UNPROVEN
        if raw is None:
            return False
        try:
            actual = self._parse_marker(raw, "cleanup_record_invalid")
        except SafeSubmitError:
            return CLEANUP_PRESENT_UNPROVEN
        if actual != self._cleanup_record(intent, intent_digest):
            return CLEANUP_PRESENT_UNPROVEN
        return True

    def _mark_cleanup(self, intent: dict[str, Any], intent_digest: str) -> bool:
        requested = self._cleanup_requested(intent, intent_digest)
        if requested is True:
            return True
        if requested is CLEANUP_PRESENT_UNPROVEN:
            return False
        record = self._cleanup_record(intent, intent_digest)
        try:
            raw = self.marker_store.create_if_absent(
                "cleanup", intent["rehearsal_id"], _canonical(record)
            )
        except MarkerConflict:
            return False
        except MarkerPresentInvalid:
            return False
        except MarkerOutcomeUnknown:
            return False
        try:
            actual = self._parse_marker(raw, "cleanup_record_invalid")
        except SafeSubmitError:
            return False
        return actual == record

    def _restore_body(
        self, intent: dict[str, Any], intent_digest: str
    ) -> dict[str, Any]:
        tags = {
            **intent["required_tags"],
            "restore-intent-sha256": intent_digest.removeprefix("sha256:"),
        }
        return {
            "location": intent["location"],
            "tags": tags,
            "properties": {
                "createMode": "PointInTimeRestore",
                "sourceServerResourceId": intent["source_server_resource_id"],
                "pointInTimeUTC": intent["restore_time"],
                "network": {"publicNetworkAccess": "Disabled"},
            },
        }

    def _get_target(self, target_id: str) -> ArmResponse | object | None:
        response = self._request(
            "GET", target_id, api_version=self.config.postgres_api_version
        )
        if response is UNKNOWN:
            return UNKNOWN
        assert isinstance(response, ArmResponse)
        if response.status == 404:
            return None
        if response.status == 200:
            return response
        if response.status == 429 or response.status >= 500:
            return UNKNOWN
        _fail("target_read_refused")

    def _verify_target(
        self, response: ArmResponse, intent: dict[str, Any], intent_digest: str
    ) -> dict[str, Any]:
        body = response.body
        if not isinstance(body, dict):
            _fail("target_response_invalid")
        if (
            _lower_id(body.get("id")) != _lower_id(intent["target_server_resource_id"])
            or body.get("name") != intent["target_server_name"]
            or str(body.get("type", "")).lower() != POSTGRES_TYPE
            or _location(body.get("location")) != _location(intent["location"])
        ):
            _fail("target_identity_mismatch")
        tags = body.get("tags")
        if tags is not None and not isinstance(tags, dict):
            _fail("target_binding_mismatch")
        expected_tags = {
            **intent["required_tags"],
            "restore-intent-sha256": intent_digest.removeprefix("sha256:"),
        }
        if isinstance(tags, dict) and any(
            key in tags and tags[key] != value for key, value in expected_tags.items()
        ):
            _fail("target_binding_mismatch")
        properties = body.get("properties")
        if not isinstance(properties, dict):
            _fail("target_response_invalid")
        source = properties.get("sourceServerResourceId")
        restore_time = properties.get("pointInTimeUTC")
        create_mode = properties.get("createMode")
        if source is not None and _lower_id(source) != _lower_id(
            intent["source_server_resource_id"]
        ):
            _fail("target_binding_mismatch")
        if restore_time is not None and restore_time != intent["restore_time"]:
            _fail("target_binding_mismatch")
        if create_mode is not None and create_mode != "PointInTimeRestore":
            _fail("target_binding_mismatch")
        system_data = body.get("systemData")
        system_bound = False
        if system_data is not None:
            if not isinstance(system_data, dict):
                _fail("target_binding_mismatch")
            created_at = _parse_arm_utc(system_data.get("createdAt"))
            lower_bound = _parse_utc(
                intent["created_at"], "intent_record_invalid"
            ) - timedelta(seconds=self.config.binding_clock_skew_seconds)
            upper_bound = _parse_utc(
                intent["cleanup_not_after"], "intent_record_invalid"
            ) + timedelta(hours=self.config.late_materialization_watch_hours)
            if (
                system_data.get("createdByType") != "Application"
                or system_data.get("createdBy")
                not in {
                    intent["provision_client_id"],
                    intent["provision_principal_id"],
                }
                or created_at is None
                or not lower_bound <= created_at <= upper_bound
            ):
                _fail("target_binding_mismatch")
            system_bound = True
        tag_bound = isinstance(tags, dict) and tags.get("restore-intent-sha256") == (
            intent_digest.removeprefix("sha256:")
        )
        restore_bound = (
            _lower_id(source) == _lower_id(intent["source_server_resource_id"])
            and restore_time == intent["restore_time"]
            and create_mode == "PointInTimeRestore"
        )
        if not tag_bound and not restore_bound and not system_bound:
            _fail("target_binding_unproven")
        return body

    def _is_disabled(self, target: dict[str, Any]) -> bool:
        properties = target.get("properties")
        return (
            isinstance(properties, dict)
            and isinstance(properties.get("network"), dict)
            and properties["network"].get("publicNetworkAccess") == "Disabled"
        )

    def _request_isolation(self, target: dict[str, Any], target_id: str) -> str:
        if self._is_disabled(target):
            return ISOLATED
        headers: dict[str, str] = {}
        if isinstance(target.get("etag"), str):
            headers["If-Match"] = target["etag"]
        response = self._request(
            "PATCH",
            target_id,
            api_version=self.config.postgres_api_version,
            body={"properties": {"network": {"publicNetworkAccess": "Disabled"}}},
            headers=headers,
        )
        if response is UNKNOWN:
            return ISOLATION_TRANSIENT
        assert isinstance(response, ArmResponse)
        if response.status in {200, 202, 204}:
            return ISOLATION_TRANSIENT
        if response.status in {404, 409, 412, 429} or response.status >= 500:
            return ISOLATION_TRANSIENT
        return ISOLATION_REFUSED

    def _target_state(self, target: dict[str, Any]) -> str:
        properties = target.get("properties")
        if not isinstance(properties, dict):
            _fail("target_response_invalid")
        state = properties.get("state")
        return state if isinstance(state, str) else "Unknown"

    def _outcome(
        self,
        status: str,
        intent: dict[str, Any],
        intent_digest: str,
        *,
        observed: bool,
        cleanup_required: bool,
    ) -> SubmitOutcome:
        return SubmitOutcome(
            status=status,
            target_resource_id=intent["target_server_resource_id"],
            intent_sha256=intent_digest,
            target_observed=observed,
            cleanup_required=cleanup_required,
        )

    def _defer_cleanup(
        self,
        intent: dict[str, Any],
        intent_digest: str,
        *,
        observed: bool,
    ) -> SubmitOutcome:
        """Persist the dominant tombstone without exercising delete authority."""

        try:
            cleanup_proven = self._mark_cleanup(intent, intent_digest)
        except Exception:
            cleanup_proven = False
        if not cleanup_proven:
            return self._outcome(
                "cleanup_marker_unproven",
                intent,
                intent_digest,
                observed=observed,
                cleanup_required=True,
            )
        return self._outcome(
            "cleanup_pending" if observed else "pending_late_materialization",
            intent,
            intent_digest,
            observed=observed,
            cleanup_required=True,
        )

    def _delete_target_for_cleanup(
        self, intent: dict[str, Any], intent_digest: str
    ) -> SubmitOutcome:
        if not self._mark_cleanup(intent, intent_digest):
            return self._outcome(
                "cleanup_marker_unproven",
                intent,
                intent_digest,
                observed=False,
                cleanup_required=True,
            )
        target_id = intent["target_server_resource_id"]
        observed = False
        delete_attempted = False
        for attempt in range(self.config.cleanup_poll_attempts):
            response = self._get_target(target_id)
            if response is UNKNOWN:
                pass
            elif response is None:
                if delete_attempted:
                    return self._outcome(
                        "cleaned",
                        intent,
                        intent_digest,
                        observed=observed,
                        cleanup_required=False,
                    )
            else:
                assert isinstance(response, ArmResponse)
                observed = True
                target = self._verify_target(response, intent, intent_digest)
                self._request_isolation(target, target_id)
                headers: dict[str, str] = {}
                if isinstance(target.get("etag"), str):
                    headers["If-Match"] = target["etag"]
                deleted = self._request(
                    "DELETE",
                    target_id,
                    api_version=self.config.postgres_api_version,
                    headers=headers,
                )
                if deleted is UNKNOWN:
                    delete_attempted = True
                else:
                    assert isinstance(deleted, ArmResponse)
                    if (
                        deleted.status in {200, 202, 204, 404, 409, 412, 429}
                        or deleted.status >= 500
                    ):
                        delete_attempted = True
                    else:
                        _fail("target_cleanup_refused")
            if attempt + 1 < self.config.cleanup_poll_attempts:
                self.sleeper(self.config.poll_interval_seconds)
        status = "cleanup_pending" if observed else "pending_late_materialization"
        return self._outcome(
            status,
            intent,
            intent_digest,
            observed=observed,
            cleanup_required=True,
        )

    def _poll_restore(
        self,
        intent: dict[str, Any],
        intent_digest: str,
        *,
        observed: bool = False,
    ) -> SubmitOutcome:
        target_id = intent["target_server_resource_id"]
        for attempt in range(self.config.restore_poll_attempts):
            try:
                cleanup_requested = self._cleanup_requested(intent, intent_digest)
                if cleanup_requested is not False:
                    return self._defer_cleanup(intent, intent_digest, observed=observed)
                if self._intent_expired(intent):
                    return self._defer_cleanup(intent, intent_digest, observed=observed)
                response = self._get_target(target_id)
                if isinstance(response, ArmResponse):
                    observed = True
                    target = self._verify_target(response, intent, intent_digest)
                    state = self._target_state(target)
                    if state in self.config.failed_states:
                        return self._defer_cleanup(
                            intent, intent_digest, observed=observed
                        )
                    isolation = self._request_isolation(target, target_id)
                    if isolation == ISOLATION_REFUSED:
                        return self._defer_cleanup(
                            intent, intent_digest, observed=observed
                        )
                    cleanup_requested = self._cleanup_requested(intent, intent_digest)
                    if cleanup_requested is not False:
                        return self._defer_cleanup(
                            intent, intent_digest, observed=observed
                        )
                    if self._intent_expired(intent):
                        return self._defer_cleanup(
                            intent, intent_digest, observed=observed
                        )
                    if state == self.config.ready_state and self._is_disabled(target):
                        return self._outcome(
                            "ready",
                            intent,
                            intent_digest,
                            observed=True,
                            cleanup_required=False,
                        )
                if attempt + 1 < self.config.restore_poll_attempts:
                    self.sleeper(self.config.poll_interval_seconds)
            except Exception:
                return self._defer_cleanup(intent, intent_digest, observed=observed)
        return self._defer_cleanup(intent, intent_digest, observed=observed)

    def submit(self, request: RestoreRequest) -> SubmitOutcome:
        self._require_marker_fidelity()
        expected = self._build_intent(request, require_fresh=False)
        existing_intent = self._read_marker("intent", request.rehearsal_id)
        if existing_intent is UNKNOWN:
            _fail("intent_record_read_unknown")
        if existing_intent is None:
            self._validate_evidence_record(
                expected["evidence_binding"], require_fresh=True
            )
            if self._intent_expired(expected):
                _fail("intent_expired")
            if self._evidence_expired(expected):
                _fail("evidence_expired")
            intent = self._ensure_marker("intent", request.rehearsal_id, expected)
        else:
            assert isinstance(existing_intent, dict)
            intent = existing_intent
        self._validate_intent(intent, request.rehearsal_id)
        if intent != expected:
            _fail("intent_record_conflict")
        intent_digest = f"sha256:{_sha256(_canonical(intent))}"
        cleanup_requested = self._cleanup_requested(intent, intent_digest)
        if cleanup_requested is UNKNOWN:
            return self._defer_cleanup(intent, intent_digest, observed=False)
        if cleanup_requested is not False:
            return self._defer_cleanup(intent, intent_digest, observed=False)
        try:
            existing_submission = self._read_marker("submission", request.rehearsal_id)
        except Exception:
            return self._defer_cleanup(intent, intent_digest, observed=False)
        submission = self._submission_record(intent, intent_digest)
        try:
            intent_expired = self._intent_expired(intent)
        except Exception:
            return self._defer_cleanup(intent, intent_digest, observed=False)
        if intent_expired:
            if existing_submission is UNKNOWN:
                return self._defer_cleanup(intent, intent_digest, observed=False)
            if existing_submission is None:
                _fail("intent_expired")
            if existing_submission != submission:
                return self._defer_cleanup(intent, intent_digest, observed=False)
            return self._defer_cleanup(intent, intent_digest, observed=False)
        try:
            target = self._get_target(intent["target_server_resource_id"])
        except Exception:
            return self._defer_cleanup(intent, intent_digest, observed=False)
        if target is UNKNOWN:
            return self._defer_cleanup(intent, intent_digest, observed=False)
        if isinstance(target, ArmResponse) and existing_submission != submission:
            try:
                self._verify_target(target, intent, intent_digest)
            except Exception:
                return self._defer_cleanup(intent, intent_digest, observed=True)
            return self._defer_cleanup(intent, intent_digest, observed=True)
        if target is None:
            try:
                if self._evidence_expired(intent):
                    return self._defer_cleanup(intent, intent_digest, observed=False)
            except Exception:
                return self._defer_cleanup(intent, intent_digest, observed=False)
        try:
            self._ensure_marker("submission", request.rehearsal_id, submission)
        except Exception:
            return self._defer_cleanup(
                intent,
                intent_digest,
                observed=isinstance(target, ArmResponse),
            )
        if isinstance(target, ArmResponse):
            try:
                self._verify_target(target, intent, intent_digest)
            except Exception:
                return self._defer_cleanup(intent, intent_digest, observed=True)
            return self._poll_restore(intent, intent_digest, observed=True)
        try:
            if self._intent_expired(intent):
                return self._defer_cleanup(intent, intent_digest, observed=False)
        except Exception:
            return self._defer_cleanup(intent, intent_digest, observed=False)
        cleanup_requested = self._cleanup_requested(intent, intent_digest)
        if cleanup_requested is not False:
            return self._defer_cleanup(intent, intent_digest, observed=False)
        try:
            if self._evidence_expired(intent) or self._intent_expired(intent):
                return self._defer_cleanup(intent, intent_digest, observed=False)
        except Exception:
            return self._defer_cleanup(intent, intent_digest, observed=False)
        try:
            response = self._request(
                "PUT",
                intent["target_server_resource_id"],
                api_version=self.config.postgres_api_version,
                body=self._restore_body(intent, intent_digest),
                headers={"If-None-Match": "*"},
            )
        except Exception:
            return self._defer_cleanup(intent, intent_digest, observed=False)
        try:
            if not isinstance(response, ArmResponse) or response.status not in {
                200,
                201,
                202,
            }:
                return self._defer_cleanup(intent, intent_digest, observed=False)
            cleanup_requested = self._cleanup_requested(intent, intent_digest)
            if cleanup_requested is not False:
                return self._defer_cleanup(intent, intent_digest, observed=False)
        except Exception:
            return self._defer_cleanup(intent, intent_digest, observed=False)
        return self._poll_restore(intent, intent_digest)

    def cleanup_intent(self, rehearsal_id: str) -> SubmitOutcome:
        self._require_marker_fidelity()
        intent, intent_digest = self._load_intent(rehearsal_id)
        if not self._mark_cleanup(intent, intent_digest):
            return self._outcome(
                "cleanup_marker_unproven",
                intent,
                intent_digest,
                observed=False,
                cleanup_required=True,
            )
        try:
            actual = self._read_marker("submission", rehearsal_id)
        except MarkerPresentInvalid:
            return self._delete_target_for_cleanup(intent, intent_digest)
        if actual is UNKNOWN:
            return self._delete_target_for_cleanup(intent, intent_digest)
        if actual is None:
            target = self._get_target(intent["target_server_resource_id"])
            if target is None:
                return self._outcome(
                    "prepared_only",
                    intent,
                    intent_digest,
                    observed=False,
                    cleanup_required=False,
                )
            if target is UNKNOWN:
                return self._outcome(
                    "submission_record_unknown",
                    intent,
                    intent_digest,
                    observed=False,
                    cleanup_required=True,
                )
            return self._delete_target_for_cleanup(intent, intent_digest)
        assert isinstance(actual, dict)
        return self._delete_target_for_cleanup(intent, intent_digest)


def request_from_binding(
    *,
    binding: RestoreEvidenceBinding,
    config: SafeSubmitConfig,
    ttl_hours: int,
) -> RestoreRequest:
    """Derive the only valid request identity and deadline from a sealed binding."""

    if type(config) is not SafeSubmitConfig:
        _fail("safe_submit_config_invalid")
    if (
        type(ttl_hours) is not int
        or ttl_hours < 1
        or ttl_hours > config.maximum_ttl_hours
    ):
        _fail("cleanup_deadline_out_of_range")
    evidence = SafeRestoreSubmitter._binding_record(binding)
    observed_at = _parse_utc(evidence["observed_at"], "evidence_binding_invalid")
    authorization = {
        "schema_version": 1,
        "safe_submit_policy_id": config.policy_id,
        "evidence_binding": evidence,
    }
    rehearsal_id = _sha256(_canonical(authorization))[:12]
    return RestoreRequest(
        rehearsal_id=rehearsal_id,
        evidence_binding=binding,
        cleanup_not_after=observed_at + timedelta(hours=ttl_hours),
        repository=binding.repository,
        workflow_sha=binding.workflow_sha,
        run_id=binding.run_id,
        run_attempt=binding.run_attempt,
    )
