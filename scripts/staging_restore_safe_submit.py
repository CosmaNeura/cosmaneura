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
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from time import sleep
from typing import Any, Callable, Mapping, NoReturn, Protocol

ROOT = Path(__file__).resolve().parents[1]
POLICY_ROOT = ROOT / "contracts/staging-rehearsal/v1"
ENVELOPE_POLICY_PATH = POLICY_ROOT / "restore-envelope-policy.json"
SAFE_SUBMIT_POLICY_PATH = POLICY_ROOT / "restore-safe-submit-policy.json"
EVIDENCE_MANIFEST_POLICY_PATH = POLICY_ROOT / "evidence-manifest-policy.json"
ENVELOPE_POLICY_SHA256 = (
    "6a80a9681bf2527b7c69f48c896baa01a7b5face3ddb415c1300632dab020d92"
)
SAFE_SUBMIT_POLICY_SHA256 = (
    "3e3758a1f3756b5e922b22d4b740d1de0fc2aa8140c07589d7d39369d244a02d"
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
BLOB_OAUTH_RESOURCE = "https://storage.azure.com/"
BLOB_SERVICE_VERSION = "2021-12-02"
BLOB_HOST_RE = re.compile(r"^[a-z0-9]{3,24}\.blob\.core\.windows\.net$")
CONTAINER_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$")
ETAG_RE = re.compile(r'^"[^"\r\n]+"$')
MARKER_KINDS = frozenset({"intent", "submission", "cleanup"})
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


class AzureArmTransport:
    """Small ARM HTTP adapter with per-request deadlines and bounded errors."""

    def _access_token(self, timeout_seconds: int) -> str:
        command = [
            "az",
            "account",
            "get-access-token",
            "--resource",
            MANAGEMENT_ROOT,
            "--query",
            "accessToken",
            "--output",
            "tsv",
            "--only-show-errors",
        ]
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ArmOutcomeUnknown from exc
        token = result.stdout.strip()
        if result.returncode != 0 or not token or any(char.isspace() for char in token):
            _fail("arm_authentication_failed")
        return token

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
        if not resource_id.startswith("/") or not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}", api_version
        ):
            _fail("arm_request_target_invalid")
        mutating = method.upper() in {"DELETE", "PATCH", "POST", "PUT"}
        query = urllib.parse.urlencode({"api-version": api_version})
        url = f"{MANAGEMENT_ROOT}{resource_id}?{query}"
        payload = _canonical(body) if body is not None else None
        request_headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._access_token(timeout_seconds)}",
            "Content-Type": "application/json",
        }
        request_headers.update(headers or {})
        request = urllib.request.Request(
            url, data=payload, headers=request_headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read(1_048_577)
                if len(raw) > 1_048_576:
                    if mutating:
                        raise ArmOutcomeUnknown
                    _fail("arm_response_too_large")
                try:
                    parsed = json.loads(raw) if raw.strip() else None
                except (UnicodeError, json.JSONDecodeError) as exc:
                    if mutating:
                        raise ArmOutcomeUnknown from exc
                    _fail("arm_response_invalid")
                try:
                    status = int(response.status)
                    response_headers = {
                        key.lower(): value for key, value in response.headers.items()
                    }
                except (AttributeError, TypeError, ValueError) as exc:
                    if mutating:
                        raise ArmOutcomeUnknown from exc
                    _fail("arm_response_invalid")
                return ArmResponse(status=status, body=parsed, headers=response_headers)
        except urllib.error.HTTPError as exc:
            try:
                return ArmResponse(
                    status=int(exc.code),
                    headers={key.lower(): value for key, value in exc.headers.items()},
                )
            except Exception as response_error:
                if mutating:
                    raise ArmOutcomeUnknown from response_error
                _fail("arm_response_invalid")
        except (ArmOutcomeUnknown, SafeSubmitError):
            raise
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            raise ArmOutcomeUnknown from exc
        except Exception as exc:
            if mutating:
                raise ArmOutcomeUnknown from exc
            raise SafeSubmitError("arm_response_invalid") from exc


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


@dataclass(frozen=True)
class BlobResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class AzureBlobMarkerStore:
    """Raw-byte, create-only marker store pinned to one Blob endpoint."""

    def __init__(
        self,
        config: SafeSubmitConfig,
        *,
        sleeper: Callable[[float], None] = sleep,
        clock: Callable[[], datetime] = _now,
        opener: Any | None = None,
    ) -> None:
        self.config = config
        self.sleeper = sleeper
        self.clock = clock
        self.opener = opener or urllib.request.build_opener(_RefuseRedirects())
        account_parts = config.marker_account_resource_id.strip("/").split("/")
        if (
            not config.execution_authorized
            or not config.blob_marker_store_implemented
            or not config.blob_marker_provider_fidelity_proven
            or not config.private_runner_binding_implemented
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

    def _trusted_now(self) -> datetime:
        try:
            value = self.clock()
        except Exception as exc:
            raise SafeSubmitError("trusted_clock_unavailable") from exc
        if not isinstance(value, datetime) or value.tzinfo is None:
            _fail("trusted_clock_invalid")
        return value.astimezone(UTC).replace(microsecond=0)

    def _access_token(self) -> str:
        command = [
            "az",
            "account",
            "get-access-token",
            "--resource",
            BLOB_OAUTH_RESOURCE,
            "--query",
            "accessToken",
            "--output",
            "tsv",
            "--only-show-errors",
        ]
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.config.marker_request_timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise MarkerOutcomeUnknown from exc
        token = result.stdout.strip()
        if result.returncode != 0 or not token or any(char.isspace() for char in token):
            _fail("blob_authentication_failed")
        return token

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

    def _response_headers(self, response: Any) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, value in response.headers.items():
            lowered = str(key).lower()
            if lowered in normalized or "\r" in str(value) or "\n" in str(value):
                raise ValueError("invalid response headers")
            normalized[lowered] = str(value)
        return normalized

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
        headers = {
            "Accept": "application/json" if exact_marker_get else "application/xml",
            "Authorization": f"Bearer {self._access_token()}",
            "x-ms-client-request-id": self._client_request_id(method, url, body),
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
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        status: int | None = None
        try:
            with self.opener.open(
                request, timeout=self.config.marker_request_timeout_seconds
            ) as response:
                status = int(response.status)
                if 300 <= status < 400 or response.geturl() != url:
                    _fail("blob_redirect_refused")
                response_headers = self._response_headers(response)
                raw = response.read(max_response_bytes + 1)
                if len(raw) > max_response_bytes:
                    if mutating:
                        raise MarkerOutcomeUnknown
                    if exact_marker_get and status == 200:
                        raise MarkerPresentInvalid("marker_body_too_large")
                    _fail("blob_response_too_large")
                return BlobResponse(status, raw, response_headers)
        except urllib.error.HTTPError as exc:
            if 300 <= int(exc.code) < 400:
                _fail("blob_redirect_refused")
            return BlobResponse(int(exc.code), b"", {})
        except (MarkerOutcomeUnknown, MarkerPresentInvalid, SafeSubmitError):
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


@dataclass(frozen=True)
class RestoreRequest:
    rehearsal_id: str
    evidence_binding: RestoreEvidenceBinding
    cleanup_not_after: datetime
    repository: str
    workflow_sha: str
    run_id: str
    run_attempt: str


@dataclass(frozen=True)
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
            "provision_cleanup_read_assignment_id": "CONFIGURE_EXACT_RESTORE_MARKER_PROVISION_CLEANUP_READ_ROLE_ASSIGNMENT_ID",
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
        except MarkerPresentInvalid:
            return CLEANUP_PRESENT_UNPROVEN
        if raw is None:
            return False
        try:
            actual = self._parse_marker(raw, "cleanup_record_invalid")
            _ = actual == self._cleanup_record(intent, intent_digest)
        except SafeSubmitError:
            pass
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
            return True
        except MarkerPresentInvalid:
            return False
        except MarkerOutcomeUnknown:
            return False
        try:
            _ = self._parse_marker(raw, "cleanup_record_invalid") == record
        except SafeSubmitError:
            pass
        return True

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

    def _cleanup_target(
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
        self, intent: dict[str, Any], intent_digest: str
    ) -> SubmitOutcome:
        target_id = intent["target_server_resource_id"]
        for attempt in range(self.config.restore_poll_attempts):
            cleanup_requested = self._cleanup_requested(intent, intent_digest)
            if cleanup_requested is not False:
                return self._cleanup_target(intent, intent_digest)
            if self._intent_expired(intent):
                return self._cleanup_target(intent, intent_digest)
            response = self._get_target(target_id)
            if isinstance(response, ArmResponse):
                target = self._verify_target(response, intent, intent_digest)
                state = self._target_state(target)
                if state in self.config.failed_states:
                    return self._cleanup_target(intent, intent_digest)
                isolation = self._request_isolation(target, target_id)
                if isolation == ISOLATION_REFUSED:
                    return self._cleanup_target(intent, intent_digest)
                cleanup_requested = self._cleanup_requested(intent, intent_digest)
                if cleanup_requested is not False:
                    return self._cleanup_target(intent, intent_digest)
                if self._intent_expired(intent):
                    return self._cleanup_target(intent, intent_digest)
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
        return self._cleanup_target(intent, intent_digest)

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
            _fail("cleanup_record_preflight_unknown")
        if cleanup_requested is not False:
            return self._cleanup_target(intent, intent_digest)
        try:
            existing_submission = self._read_marker("submission", request.rehearsal_id)
        except MarkerPresentInvalid:
            return self._cleanup_target(intent, intent_digest)
        submission = self._submission_record(intent, intent_digest)
        if self._intent_expired(intent):
            if existing_submission is UNKNOWN:
                return self._cleanup_target(intent, intent_digest)
            if existing_submission is None:
                _fail("intent_expired")
            if existing_submission != submission:
                return self._cleanup_target(intent, intent_digest)
            return self._cleanup_target(intent, intent_digest)
        target = self._get_target(intent["target_server_resource_id"])
        if target is UNKNOWN:
            _fail("target_preflight_unknown")
        if isinstance(target, ArmResponse) and existing_submission != submission:
            return self._cleanup_target(intent, intent_digest)
        if target is None and self._evidence_expired(intent):
            return self._cleanup_target(intent, intent_digest)
        try:
            self._ensure_marker("submission", request.rehearsal_id, submission)
        except (MarkerConflict, MarkerPresentInvalid):
            return self._cleanup_target(intent, intent_digest)
        if isinstance(target, ArmResponse):
            self._verify_target(target, intent, intent_digest)
            return self._poll_restore(intent, intent_digest)
        if self._intent_expired(intent):
            return self._cleanup_target(intent, intent_digest)
        cleanup_requested = self._cleanup_requested(intent, intent_digest)
        if cleanup_requested is not False:
            return self._cleanup_target(intent, intent_digest)
        if self._evidence_expired(intent):
            return self._cleanup_target(intent, intent_digest)
        if self._intent_expired(intent):
            return self._cleanup_target(intent, intent_digest)
        response = self._request(
            "PUT",
            intent["target_server_resource_id"],
            api_version=self.config.postgres_api_version,
            body=self._restore_body(intent, intent_digest),
            headers={"If-None-Match": "*"},
        )
        if (
            isinstance(response, ArmResponse)
            and response.status
            not in {
                200,
                201,
                202,
                409,
                412,
                429,
            }
            and response.status < 500
        ):
            _fail("restore_submission_refused")
        cleanup_requested = self._cleanup_requested(intent, intent_digest)
        if cleanup_requested is not False:
            return self._cleanup_target(intent, intent_digest)
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
            return self._cleanup_target(intent, intent_digest)
        if actual is UNKNOWN:
            return self._cleanup_target(intent, intent_digest)
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
            return self._cleanup_target(intent, intent_digest)
        assert isinstance(actual, dict)
        return self._cleanup_target(intent, intent_digest)
