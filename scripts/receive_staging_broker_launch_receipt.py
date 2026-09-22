#!/usr/bin/env python3
"""Dormant exact-object receiver; seals prevent accidental in-process misuse."""

from __future__ import annotations

import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from typing import Any, NoReturn, Protocol

try:
    from scripts import authenticate_staging_broker_launch_receipt as receipt_auth
    from scripts import staging_restore_runtime_adapters as runtime_adapters
    from scripts import validate_staging_restore_launch_binding as launch_binding
    from scripts import validate_staging_restore_runtime_binding as runtime_binding
except ModuleNotFoundError:  # pragma: no cover - direct script import fallback
    import authenticate_staging_broker_launch_receipt as receipt_auth
    import staging_restore_runtime_adapters as runtime_adapters
    import validate_staging_restore_launch_binding as launch_binding
    import validate_staging_restore_runtime_binding as runtime_binding


OBJECT_KINDS = ("receipt", "signature_envelope")
STORAGE_OAUTH_RESOURCE = "https://storage.azure.com/"
_REQUEST_SEAL = object()
_RESPONSE_SEAL = object()


class LaunchReceiptMailboxFailure(RuntimeError):
    """A bounded receiver failure safe to report without response contents."""


def _fail(code: str) -> NoReturn:
    raise LaunchReceiptMailboxFailure(code)


@dataclass(frozen=True, slots=True, init=False)
class AuthenticatedMailboxGetRequest:
    """Exact request contract for the managed-identity private-Blob adapter."""

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
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("mailbox_receiver.request_unsealed")

    def _is_sealed(self) -> bool:
        return (
            type(self) is AuthenticatedMailboxGetRequest
            and getattr(self, "_seal", None) is _REQUEST_SEAL
        )


@dataclass(frozen=True, slots=True, init=False)
class AuthenticatedMailboxGetResponse:
    """Response sealed by a reviewed managed-identity private-Blob adapter."""

    request: AuthenticatedMailboxGetRequest = dataclass_field(repr=False)
    status_code: int
    body: bytes = dataclass_field(repr=False)
    _seal: object = dataclass_field(repr=False, compare=False)

    def __init__(self) -> None:
        _fail("mailbox_receiver.response_unsealed")

    @classmethod
    def _from_reviewed_managed_identity_private_blob_adapter(
        cls,
        *,
        request: AuthenticatedMailboxGetRequest,
        final_uri: str,
        redirect_count: int,
        status_code: int,
        content_type_values: tuple[str, ...],
        content_encoding_values: tuple[str, ...],
        content_length_values: tuple[str, ...],
        body: bytes,
        content_range_values: tuple[str, ...] = (),
        transfer_encoding_values: tuple[str, ...] = (),
    ) -> AuthenticatedMailboxGetResponse:
        if (
            cls is not AuthenticatedMailboxGetResponse
            or type(request) is not AuthenticatedMailboxGetRequest
            or not request._is_sealed()
            or type(final_uri) is not str
            or final_uri != request.uri
            or type(redirect_count) is not int
            or redirect_count != 0
            or type(status_code) is not int
            or not 100 <= status_code <= 599
            or type(body) is not bytes
            or len(body) > request.maximum_bytes
        ):
            _fail("mailbox_receiver.response_invalid")
        header_groups = (
            content_type_values,
            content_encoding_values,
            content_length_values,
        )
        if any(
            type(values) is not tuple
            or len(values) > 1
            or any(
                type(value) is not str
                or not value
                or len(value) > 256
                or "\r" in value
                or "\n" in value
                for value in values
            )
            for values in header_groups
        ):
            _fail("mailbox_receiver.response_invalid")
        content_type = content_type_values[0] if content_type_values else None
        if (
            request.content_length_required
            and len(content_length_values) != 1
            or not request.content_range_permitted
            and content_range_values != ()
            or not request.transfer_encoding_permitted
            and transfer_encoding_values != ()
            or request.content_length_required
            and bool(transfer_encoding_values)
            or request.content_length_required
            and len(content_length_values) == 1
            and content_length_values[0] != str(len(body))
            or request.content_length_required
            and not content_length_values[0].isascii()
            or request.content_length_required
            and not content_length_values[0].isdigit()
            or request.content_length_required
            and content_length_values[0] != str(int(content_length_values[0]))
            or content_encoding_values
            and content_encoding_values[0].strip().lower() != "identity"
        ):
            _fail("mailbox_receiver.response_invalid")
        if content_length_values:
            raw_length = content_length_values[0]
            if (
                not raw_length.isascii()
                or not raw_length.isdigit()
                or raw_length != str(len(body))
            ):
                _fail("mailbox_receiver.response_invalid")
        if status_code == 200:
            if not body or not _content_type_matches(request, content_type):
                _fail("mailbox_receiver.response_invalid")
            exposed_body = bytes(body)
        else:
            exposed_body = b""
        result = object.__new__(cls)
        for name, value in {
            "request": request,
            "status_code": status_code,
            "body": exposed_body,
        }.items():
            object.__setattr__(result, name, value)
        object.__setattr__(result, "_seal", _RESPONSE_SEAL)
        return result

    def _is_authenticated_for(self, request: AuthenticatedMailboxGetRequest) -> bool:
        return (
            type(self) is AuthenticatedMailboxGetResponse
            and getattr(self, "_seal", None) is _RESPONSE_SEAL
            and self.request is request
        )


class LaunchReceiptMailboxTransport(Protocol):
    """Private-Blob adapter boundary; implementations must not redirect."""

    def get_exact_private_blob(
        self, request: AuthenticatedMailboxGetRequest
    ) -> AuthenticatedMailboxGetResponse: ...


class _HttpOpener(Protocol):
    def open(self, request: Any, *, timeout: int) -> Any: ...


class _DeadlineFactory(Protocol):
    def __call__(self, timeout_seconds: int) -> AbstractContextManager[None]: ...


def _header_values(headers: Any, name: str) -> tuple[str, ...]:
    try:
        if hasattr(headers, "get_all"):
            raw = headers.get_all(name) or []
        else:
            value = headers.get(name)
            raw = [] if value is None else [value]
        values = tuple(raw)
    except Exception:
        _fail("mailbox_receiver.http_response_invalid")
    if any(
        type(value) is not str
        or not value
        or len(value) > 256
        or "\r" in value
        or "\n" in value
        for value in values
    ):
        _fail("mailbox_receiver.http_response_invalid")
    return values


def _read_wire_response(
    response: Any, *, expected_uri: str, maximum_bytes: int
) -> tuple[
    int,
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    bytes,
]:
    try:
        status = getattr(response, "status", getattr(response, "code", None))
        final_uri = response.geturl()
        content_types = _header_values(response.headers, "Content-Type")
        content_encodings = _header_values(response.headers, "Content-Encoding")
        content_lengths = _header_values(response.headers, "Content-Length")
        content_ranges = _header_values(response.headers, "Content-Range")
        transfer_encodings = _header_values(response.headers, "Transfer-Encoding")
    except LaunchReceiptMailboxFailure:
        raise
    except Exception:
        raise LaunchReceiptMailboxFailure(
            "mailbox_receiver.http_response_invalid"
        ) from None
    if (
        type(status) is not int
        or not 100 <= status <= 599
        or type(final_uri) is not str
        or final_uri != expected_uri
        or len(content_types) > 1
        or len(content_encodings) > 1
        or len(content_lengths) > 1
    ):
        _fail("mailbox_receiver.http_response_invalid")
    declared_length: int | None = None
    if content_lengths:
        raw_length = content_lengths[0]
        if (
            not raw_length.isascii()
            or not raw_length.isdigit()
            or raw_length != str(int(raw_length))
        ):
            _fail("mailbox_receiver.http_response_invalid")
        declared_length = int(raw_length)
        if declared_length > maximum_bytes:
            _fail("mailbox_receiver.http_response_too_large")
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            chunk = response.read(maximum_bytes + 1 - total)
            if type(chunk) is not bytes:
                _fail("mailbox_receiver.http_response_invalid")
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum_bytes:
                _fail("mailbox_receiver.http_response_too_large")
    except LaunchReceiptMailboxFailure:
        raise
    except Exception:
        raise LaunchReceiptMailboxFailure(
            "mailbox_receiver.http_response_unavailable"
        ) from None
    raw = b"".join(chunks)
    if declared_length is not None and declared_length != len(raw):
        _fail("mailbox_receiver.http_response_invalid")
    return (
        status,
        content_types,
        content_encodings,
        content_lengths,
        content_ranges,
        transfer_encodings,
        raw,
    )


def _clock_value(clock: Callable[[], float], code: str) -> float:
    try:
        value = clock()
    except Exception:
        raise LaunchReceiptMailboxFailure(code) from None
    if (
        type(value) not in {int, float}
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < 0
    ):
        _fail(code)
    return float(value)


class ContainerAppsStorageTokenSource:
    """Cached storage token from one exact Container Apps job UAMI endpoint."""

    __slots__ = (
        "_access_token",
        "_client_id",
        "_clock",
        "_expires_on",
        "_expiry_skew_seconds",
        "_identity_endpoint",
        "_identity_header",
        "_last_now",
        "_maximum_lifetime_seconds",
        "_maximum_response_bytes",
        "_maximum_timeout_seconds",
        "_opener",
        "_service_api_version",
        "_lock",
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
        try:
            validated_endpoint = runtime_adapters._validate_identity_endpoint(
                identity_endpoint
            )
        except runtime_adapters.RuntimeAdapterFailure:
            _fail("mailbox_receiver.identity_configuration_invalid")
        if (
            type(client_id) is not str
            or runtime_adapters.UUID_RE.fullmatch(client_id) is None
            or type(identity_header) is not str
            or len(identity_header) > 8_192
            or runtime_adapters.SAFE_TOKEN_RE.fullmatch(identity_header) is None
            or type(service_api_version) is not str
            or service_api_version != runtime_adapters.IDENTITY_API_VERSION
            or type(maximum_response_bytes) is not int
            or not 1 <= maximum_response_bytes <= runtime_adapters.MAXIMUM_TOKEN_BYTES
            or type(expiry_skew_seconds) is not int
            or not 1 <= expiry_skew_seconds <= 300
            or type(maximum_lifetime_seconds) is not int
            or not expiry_skew_seconds < maximum_lifetime_seconds <= 86_400
            or type(maximum_timeout_seconds) is not int
            or not 1 <= maximum_timeout_seconds <= 60
            or not callable(clock)
            or (opener is not None and not callable(getattr(opener, "open", None)))
        ):
            _fail("mailbox_receiver.identity_configuration_invalid")
        self._client_id = client_id
        self._identity_endpoint = validated_endpoint
        self._identity_header = identity_header
        self._service_api_version = service_api_version
        self._maximum_response_bytes = maximum_response_bytes
        self._expiry_skew_seconds = expiry_skew_seconds
        self._maximum_lifetime_seconds = maximum_lifetime_seconds
        self._maximum_timeout_seconds = maximum_timeout_seconds
        self._opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}), runtime_adapters._NoRedirects()
        )
        self._clock = clock
        self._lock = threading.Lock()
        self._access_token: str | None = None
        self._expires_on: int | None = None
        self._last_now: float | None = None

    def _is_bound(self, *, client_id: str, resource: str) -> bool:
        return (
            type(self) is ContainerAppsStorageTokenSource
            and client_id == self._client_id
            and resource == STORAGE_OAUTH_RESOURCE
        )

    def get_token(self, *, client_id: str, resource: str, timeout_seconds: int) -> str:
        if (
            not self._is_bound(client_id=client_id, resource=resource)
            or type(timeout_seconds) is not int
            or not 1 <= timeout_seconds <= self._maximum_timeout_seconds
        ):
            _fail("mailbox_receiver.identity_request_invalid")
        with self._lock:
            now = _clock_value(self._clock, "mailbox_receiver.identity_clock_invalid")
            if self._last_now is not None and now < self._last_now:
                _fail("mailbox_receiver.identity_clock_invalid")
            self._last_now = now
            if (
                self._access_token is not None
                and self._expires_on is not None
                and self._expires_on > now + self._expiry_skew_seconds
            ):
                return self._access_token
            query = urllib.parse.urlencode(
                {
                    "api-version": self._service_api_version,
                    "resource": resource,
                    "client_id": client_id,
                }
            )
            uri = f"{self._identity_endpoint}?{query}"
            request = urllib.request.Request(
                uri,
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "Metadata": "true",
                    "X-IDENTITY-HEADER": self._identity_header,
                },
                method="GET",
            )
            try:
                try:
                    response = self._opener.open(request, timeout=timeout_seconds)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    (
                        status,
                        types,
                        encodings,
                        _lengths,
                        _ranges,
                        _transfers,
                        raw,
                    ) = _read_wire_response(
                        response,
                        expected_uri=uri,
                        maximum_bytes=self._maximum_response_bytes,
                    )
            except LaunchReceiptMailboxFailure:
                raise
            except Exception:
                raise LaunchReceiptMailboxFailure(
                    "mailbox_receiver.identity_unavailable"
                ) from None
            if 300 <= status < 400:
                _fail("mailbox_receiver.identity_redirect_refused")
            if status != 200:
                _fail("mailbox_receiver.identity_refused")
            if (
                len(types) != 1
                or tuple(part.strip().lower() for part in types[0].split(";"))
                not in {
                    ("application/json",),
                    ("application/json", "charset=utf-8"),
                }
                or encodings
                and encodings[0].strip().lower() != "identity"
            ):
                _fail("mailbox_receiver.identity_response_invalid")
            try:
                document = runtime_adapters._load_mapping(
                    raw, "managed_identity.response_invalid"
                )
            except runtime_adapters.RuntimeAdapterFailure:
                _fail("mailbox_receiver.identity_response_invalid")
            token = document.get("access_token")
            expires_on = document.get("expires_on")
            if (
                not {"access_token", "expires_on"} <= set(document)
                or type(token) is not str
                or runtime_adapters.SAFE_TOKEN_RE.fullmatch(token) is None
                or document.get("token_type", "Bearer") != "Bearer"
                or document.get("resource", resource) != resource
                or document.get("client_id", client_id) != client_id
            ):
                _fail("mailbox_receiver.identity_response_invalid")
            if type(expires_on) is int:
                expiration = expires_on
            elif (
                type(expires_on) is str
                and runtime_adapters.UNSIGNED_DECIMAL_RE.fullmatch(expires_on)
                is not None
                and expires_on == str(int(expires_on))
            ):
                expiration = int(expires_on)
            else:
                _fail("mailbox_receiver.identity_response_invalid")
            if (
                expiration <= now + self._expiry_skew_seconds
                or expiration > now + self._maximum_lifetime_seconds
            ):
                _fail("mailbox_receiver.identity_response_invalid")
            self._access_token = token
            self._expires_on = expiration
            return token


class ManagedIdentityPrivateBlobTransport:
    """Concrete no-proxy/no-redirect Blob GET using one exact job UAMI."""

    __slots__ = ("_clock", "_deadline", "_lock", "_opener", "_token_source")

    def __init__(
        self,
        token_source: ContainerAppsStorageTokenSource,
        *,
        opener: _HttpOpener | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        deadline: _DeadlineFactory = runtime_adapters._wall_clock_deadline,
    ) -> None:
        if (
            type(token_source) is not ContainerAppsStorageTokenSource
            or (opener is not None and not callable(getattr(opener, "open", None)))
            or not callable(monotonic)
            or not callable(deadline)
        ):
            _fail("mailbox_receiver.transport_configuration_invalid")
        self._token_source = token_source
        try:
            self._opener = opener or runtime_adapters._default_opener()
        except runtime_adapters.RuntimeAdapterFailure:
            _fail("mailbox_receiver.transport_configuration_invalid")
        self._clock = monotonic
        self._deadline = deadline
        self._lock = threading.Lock()

    @classmethod
    def from_environment(
        cls,
        policy: Any,
        *,
        action: str,
        identity_opener: _HttpOpener | None = None,
        blob_opener: _HttpOpener | None = None,
        epoch_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        deadline: _DeadlineFactory = runtime_adapters._wall_clock_deadline,
    ) -> ManagedIdentityPrivateBlobTransport:
        if type(action) is not str or action not in launch_binding.JOB_KINDS:
            _fail("mailbox_receiver.transport_configuration_invalid")
        try:
            document = runtime_binding.validate_runtime_binding_policy(
                policy, executable=True
            )
            retrieval = document["launch_binding"]["mailbox"][
                "execution_receipt_handoff"
            ]["retrieval"]
            identity = document["identities"]["jobs"][action]
        except (KeyError, TypeError, IndexError, runtime_binding.RuntimeBindingFailure):
            raise LaunchReceiptMailboxFailure(
                "mailbox_receiver.transport_configuration_invalid"
            ) from None
        source = ContainerAppsStorageTokenSource(
            client_id=identity["client_id"],
            identity_endpoint=os.environ.get("IDENTITY_ENDPOINT", ""),
            identity_header=os.environ.get("IDENTITY_HEADER", ""),
            service_api_version=retrieval["managed_identity_api_version"],
            maximum_response_bytes=retrieval["managed_identity_response_maximum_bytes"],
            expiry_skew_seconds=retrieval["managed_identity_expiry_skew_seconds"],
            maximum_lifetime_seconds=retrieval[
                "managed_identity_maximum_token_lifetime_seconds"
            ],
            maximum_timeout_seconds=retrieval["maximum_request_timeout_seconds"],
            opener=identity_opener,
            clock=epoch_clock,
        )
        return cls(
            source,
            opener=blob_opener,
            monotonic=monotonic,
            deadline=deadline,
        )

    @classmethod
    def from_provision_launch_record_environment(
        cls,
        policy: Any,
        *,
        identity_opener: _HttpOpener | None = None,
        blob_opener: _HttpOpener | None = None,
        epoch_clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        deadline: _DeadlineFactory = runtime_adapters._wall_clock_deadline,
    ) -> ManagedIdentityPrivateBlobTransport:
        """Build the exact provision-job transport for one launch-record GET."""

        try:
            document = runtime_binding.validate_runtime_binding_policy(
                policy, executable=True
            )
            retrieval = document["launch_binding"]["mailbox"]["launch_record_retrieval"]
            identity = document["identities"]["jobs"]["provision"]
        except (KeyError, TypeError, IndexError, runtime_binding.RuntimeBindingFailure):
            raise LaunchReceiptMailboxFailure(
                "mailbox_receiver.transport_configuration_invalid"
            ) from None
        source = ContainerAppsStorageTokenSource(
            client_id=identity["client_id"],
            identity_endpoint=os.environ.get("IDENTITY_ENDPOINT", ""),
            identity_header=os.environ.get("IDENTITY_HEADER", ""),
            service_api_version=retrieval["managed_identity_api_version"],
            maximum_response_bytes=retrieval["managed_identity_response_maximum_bytes"],
            expiry_skew_seconds=retrieval["managed_identity_expiry_skew_seconds"],
            maximum_lifetime_seconds=retrieval[
                "managed_identity_maximum_token_lifetime_seconds"
            ],
            maximum_timeout_seconds=retrieval["maximum_request_timeout_seconds"],
            opener=identity_opener,
            clock=epoch_clock,
        )
        return cls(
            source,
            opener=blob_opener,
            monotonic=monotonic,
            deadline=deadline,
        )

    def get_exact_private_blob(
        self, request: AuthenticatedMailboxGetRequest
    ) -> AuthenticatedMailboxGetResponse:
        if (
            type(request) is not AuthenticatedMailboxGetRequest
            or not request._is_sealed()
            or request.method != "GET"
            or request.oauth_resource != STORAGE_OAUTH_RESOURCE
            or request.redirects_permitted is not False
            or request.accept_encoding != "identity"
            or type(request.timeout_seconds) is not int
            or not 1 <= request.timeout_seconds <= 60
        ):
            _fail("mailbox_receiver.transport_request_invalid")
        with self._lock:
            started = _clock_value(
                self._clock, "mailbox_receiver.transport_clock_invalid"
            )
            try:
                with self._deadline(request.timeout_seconds):
                    token = self._token_source.get_token(
                        client_id=request.managed_identity_client_id,
                        resource=request.oauth_resource,
                        timeout_seconds=request.timeout_seconds,
                    )
                    if (
                        type(token) is not str
                        or runtime_adapters.SAFE_TOKEN_RE.fullmatch(token) is None
                    ):
                        _fail("mailbox_receiver.identity_response_invalid")
                    after_token = _clock_value(
                        self._clock, "mailbox_receiver.transport_clock_invalid"
                    )
                    if after_token < started:
                        _fail("mailbox_receiver.transport_clock_invalid")
                    remaining = math.floor(
                        request.timeout_seconds - (after_token - started)
                    )
                    if remaining < 1:
                        _fail("mailbox_receiver.deadline")
                    http_request = urllib.request.Request(
                        request.uri,
                        headers={
                            "Accept": request.accept,
                            "Accept-Encoding": request.accept_encoding,
                            "Authorization": f"Bearer {token}",
                            "x-ms-version": request.service_api_version,
                        },
                        method=request.method,
                    )
                    try:
                        response = self._opener.open(http_request, timeout=remaining)
                    except urllib.error.HTTPError as error:
                        response = error
                    with response:
                        (
                            status,
                            types,
                            encodings,
                            lengths,
                            ranges,
                            transfers,
                            body,
                        ) = _read_wire_response(
                            response,
                            expected_uri=request.uri,
                            maximum_bytes=request.maximum_bytes,
                        )
            except LaunchReceiptMailboxFailure:
                raise
            except runtime_adapters.RuntimeAdapterFailure as error:
                code = (
                    "mailbox_receiver.deadline"
                    if str(error) == "http_request.timeout"
                    else "mailbox_receiver.transport_unavailable"
                )
                raise LaunchReceiptMailboxFailure(code) from None
            except Exception:
                raise LaunchReceiptMailboxFailure(
                    "mailbox_receiver.transport_unavailable"
                ) from None
            if 300 <= status < 400:
                _fail("mailbox_receiver.redirect_refused")
            return AuthenticatedMailboxGetResponse._from_reviewed_managed_identity_private_blob_adapter(
                request=request,
                final_uri=request.uri,
                redirect_count=0,
                status_code=status,
                content_type_values=types,
                content_encoding_values=encodings,
                content_length_values=lengths,
                body=body,
                content_range_values=ranges,
                transfer_encoding_values=transfers,
            )


def _content_type_matches(
    request: AuthenticatedMailboxGetRequest, value: str | None
) -> bool:
    if type(value) is not str:
        return False
    parts = tuple(part.strip().lower() for part in value.split(";"))
    return parts == (request.accept,) or parts == (
        request.accept,
        f"charset={request.optional_charset}",
    )


def _sealed_request(
    *,
    document: dict[str, Any],
    action: str,
    object_kind: str,
    execution_name: str,
    uri: str,
    maximum_bytes: int,
    attempt_number: int,
    timeout_seconds: int,
) -> AuthenticatedMailboxGetRequest:
    retrieval = document["launch_binding"]["mailbox"]["execution_receipt_handoff"][
        "retrieval"
    ]
    identity = document["identities"]["jobs"][action]
    mailbox = document["launch_binding"]["mailbox"]
    expected_uris = dict(
        zip(
            OBJECT_KINDS,
            launch_binding._execution_receipt_uris(document, action, execution_name),
            strict=True,
        )
    )
    parsed = urllib.parse.urlsplit(uri)
    expected_maximum = {
        "receipt": document["launch_binding"]["launch_receipt"]["maximum_bytes"],
        "signature_envelope": document["launch_binding"]["receipt_signing"][
            "signature_envelope"
        ]["maximum_bytes"],
    }
    if (
        object_kind not in OBJECT_KINDS
        or uri != expected_uris[object_kind]
        or maximum_bytes != expected_maximum[object_kind]
        or parsed.scheme != "https"
        or parsed.hostname != mailbox["account_host"]
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or type(maximum_bytes) is not int
        or maximum_bytes <= 0
        or type(attempt_number) is not int
        or not 1 <= attempt_number <= retrieval["maximum_attempts"]
        or type(timeout_seconds) is not int
        or not 0 < timeout_seconds <= retrieval["maximum_request_timeout_seconds"]
        or retrieval["method"] != "GET"
        or retrieval["managed_identity_required"] is not True
        or retrieval["exact_object_only"] is not True
        or retrieval["redirects_permitted"] is not False
        or retrieval["request_query_parameters"] != []
        or retrieval["attempt_scope"] != "per-exact-object"
        or retrieval["objects"] != list(OBJECT_KINDS)
        or retrieval["private_endpoint_required"] is not True
        or retrieval["response_authentication"]
        != "reviewed-managed-identity-private-blob-adapter-seal"
        or retrieval["non_success_body_exposed_to_receiver"] is not False
    ):
        _fail("mailbox_receiver.request_invalid")
    result = object.__new__(AuthenticatedMailboxGetRequest)
    values = {
        "action": action,
        "object_kind": object_kind,
        "uri": uri,
        "method": retrieval["method"],
        "oauth_resource": mailbox["oauth_resource"],
        "account_resource_id": mailbox["account_resource_id"],
        "container_resource_id": mailbox["container_resource_ids"][action],
        "managed_identity_resource_id": identity["resource_id"],
        "managed_identity_client_id": identity["client_id"],
        "private_endpoint_resource_id": mailbox["private_endpoint_resource_id"],
        "service_api_version": retrieval["service_api_version"],
        "accept": retrieval["successful_media_type"],
        "optional_charset": retrieval["optional_charset"],
        "accept_encoding": retrieval["content_encoding"],
        "content_length_required": False,
        "content_range_permitted": True,
        "transfer_encoding_permitted": True,
        "redirects_permitted": retrieval["redirects_permitted"],
        "maximum_bytes": maximum_bytes,
        "attempt_number": attempt_number,
        "timeout_seconds": timeout_seconds,
    }
    for name, value in values.items():
        object.__setattr__(result, name, value)
    object.__setattr__(result, "_seal", _REQUEST_SEAL)
    return result


def _sealed_provision_launch_record_request(
    *,
    document: dict[str, Any],
    authenticated_receipt: launch_binding.AuthenticatedBrokerLaunchReceipt,
    authority_receipt_sha256: str,
    authority_execution_name: str,
    authority_launch_record_uri: str,
    authority_launch_record_sha256: str,
) -> AuthenticatedMailboxGetRequest:
    """Seal the one URI derived from an authenticated provision receipt."""

    mailbox = document["launch_binding"]["mailbox"]
    retrieval = mailbox["launch_record_retrieval"]
    identity = document["identities"]["jobs"]["provision"]
    maximum_bytes = document["launch_binding"]["launch_record"]["maximum_bytes"]
    if (
        type(authenticated_receipt)
        is not launch_binding.AuthenticatedBrokerLaunchReceipt
        or not authenticated_receipt._is_authenticated()
        or authenticated_receipt.action != "provision"
        or authenticated_receipt.receipt_sha256 != authority_receipt_sha256
        or authenticated_receipt.execution_name != authority_execution_name
        or authenticated_receipt.launch_record_uri != authority_launch_record_uri
        or authenticated_receipt.launch_record_sha256 != authority_launch_record_sha256
        or os.environ.get(launch_binding.EXECUTION_NAME_ENV) != authority_execution_name
        or type(authenticated_receipt.receipt_bytes) is not bytes
        or launch_binding._digest(authenticated_receipt.receipt_bytes)
        != authority_receipt_sha256
        or type(authority_launch_record_sha256) is not str
        or launch_binding.DIGEST_RE.fullmatch(authority_launch_record_sha256) is None
    ):
        _fail("mailbox_receiver.launch_record_request_invalid")
    object_path = mailbox["launch_record_path_template"].format(
        launch_record_sha256_without_prefix=authority_launch_record_sha256[7:]
    )
    expected_uri = (
        f"https://{mailbox['account_host']}/"
        f"{mailbox['container_names']['provision']}/{object_path}"
    )
    try:
        parsed = urllib.parse.urlsplit(authenticated_receipt.launch_record_uri)
        port = parsed.port
    except (TypeError, ValueError):
        _fail("mailbox_receiver.launch_record_request_invalid")
    if (
        authority_launch_record_uri != expected_uri
        or parsed.scheme != "https"
        or parsed.hostname != mailbox["account_host"]
        or parsed.netloc != mailbox["account_host"]
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.path != f"/{mailbox['container_names']['provision']}/{object_path}"
        or parsed.query
        or parsed.fragment
        or retrieval["action"] != "provision"
        or retrieval["object"] != "launch_record"
        or retrieval["method"] != "GET"
        or retrieval["managed_identity_required"] is not True
        or retrieval["exact_object_only"] is not True
        or retrieval["listing_permitted"] is not False
        or retrieval["polling_permitted"] is not False
        or retrieval["retry_permitted"] is not False
        or retrieval["redirects_permitted"] is not False
        or retrieval["private_endpoint_required"] is not True
        or retrieval["maximum_object_get_requests"] != 1
        or retrieval["maximum_managed_identity_token_requests"] != 1
        or retrieval["maximum_total_http_requests"] != 2
        or retrieval["request_query_parameters"] != []
        or retrieval["content_length_required"] is not True
        or retrieval["content_range_permitted"] is not False
        or retrieval["transfer_encoding_permitted"] is not False
        or retrieval["response_authentication"]
        != "reviewed-managed-identity-private-blob-adapter-seal"
        or retrieval["non_success_body_exposed_to_reader"] is not False
        or retrieval["one_shot_reader_required"] is not True
        or retrieval["authority_and_execution_recheck"] != "before-and-after-object-get"
    ):
        _fail("mailbox_receiver.launch_record_request_invalid")
    result = object.__new__(AuthenticatedMailboxGetRequest)
    values = {
        "action": "provision",
        "object_kind": "launch_record",
        "uri": authority_launch_record_uri,
        "method": retrieval["method"],
        "oauth_resource": mailbox["oauth_resource"],
        "account_resource_id": mailbox["account_resource_id"],
        "container_resource_id": mailbox["container_resource_ids"]["provision"],
        "managed_identity_resource_id": identity["resource_id"],
        "managed_identity_client_id": identity["client_id"],
        "private_endpoint_resource_id": mailbox["private_endpoint_resource_id"],
        "service_api_version": retrieval["service_api_version"],
        "accept": retrieval["successful_media_type"],
        "optional_charset": retrieval["optional_charset"],
        "accept_encoding": retrieval["content_encoding"],
        "content_length_required": retrieval["content_length_required"],
        "content_range_permitted": retrieval["content_range_permitted"],
        "transfer_encoding_permitted": retrieval["transfer_encoding_permitted"],
        "redirects_permitted": retrieval["redirects_permitted"],
        "maximum_bytes": maximum_bytes,
        "attempt_number": 1,
        "timeout_seconds": retrieval["maximum_request_timeout_seconds"],
    }
    for name, value in values.items():
        object.__setattr__(result, name, value)
    object.__setattr__(result, "_seal", _REQUEST_SEAL)
    return result


class _MonotonicTimeline:
    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._last: float | None = None

    def read(self) -> float:
        try:
            value = self._clock()
        except Exception:
            raise LaunchReceiptMailboxFailure(
                "mailbox_receiver.clock_invalid"
            ) from None
        if (
            type(value) not in {int, float}
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value < 0
            or (self._last is not None and value < self._last)
        ):
            _fail("mailbox_receiver.clock_invalid")
        self._last = float(value)
        return self._last


def _retrieve_exact_object(
    *,
    document: dict[str, Any],
    action: str,
    object_kind: str,
    execution_name: str,
    uri: str,
    maximum_bytes: int,
    transport: LaunchReceiptMailboxTransport,
    timeline: _MonotonicTimeline,
    deadline: float,
    sleeper: Callable[[float], None],
    request_count: list[int],
) -> tuple[bytes, tuple[int, ...]]:
    retrieval = document["launch_binding"]["mailbox"]["execution_receipt_handoff"][
        "retrieval"
    ]
    statuses: list[int] = []
    for attempt_number in range(1, retrieval["maximum_attempts"] + 1):
        before = timeline.read()
        remaining = deadline - before
        if remaining <= 0 or request_count[0] >= retrieval["maximum_total_requests"]:
            _fail("mailbox_receiver.deadline")
        request_timeout = min(
            retrieval["maximum_request_timeout_seconds"], math.floor(remaining)
        )
        if request_timeout < 1:
            _fail("mailbox_receiver.deadline")
        request_count[0] += 1
        request = _sealed_request(
            document=document,
            action=action,
            object_kind=object_kind,
            execution_name=execution_name,
            uri=uri,
            maximum_bytes=maximum_bytes,
            attempt_number=attempt_number,
            timeout_seconds=request_timeout,
        )
        try:
            response = transport.get_exact_private_blob(request)
        except LaunchReceiptMailboxFailure:
            raise
        except Exception:
            raise LaunchReceiptMailboxFailure(
                "mailbox_receiver.transport_unavailable"
            ) from None
        after = timeline.read()
        if after > deadline:
            _fail("mailbox_receiver.deadline")
        if type(
            response
        ) is not AuthenticatedMailboxGetResponse or not response._is_authenticated_for(
            request
        ):
            _fail("mailbox_receiver.response_invalid")
        statuses.append(response.status_code)
        if response.status_code == retrieval["successful_status"]:
            return response.body, tuple(statuses)
        if (
            response.status_code not in retrieval["retryable_statuses_before_success"]
            or attempt_number == retrieval["maximum_attempts"]
        ):
            _fail("mailbox_receiver.refused")
        interval = retrieval["poll_interval_seconds"]
        if deadline - after <= interval:
            _fail("mailbox_receiver.deadline")
        try:
            sleeper(interval)
        except Exception:
            raise LaunchReceiptMailboxFailure(
                "mailbox_receiver.sleep_invalid"
            ) from None
        if timeline.read() > deadline:
            _fail("mailbox_receiver.deadline")
    _fail("mailbox_receiver.refused")


def receive_broker_launch_receipt(
    policy: Any,
    *,
    action: str,
    transport: LaunchReceiptMailboxTransport,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC).replace(
        microsecond=0
    ),
) -> launch_binding.AuthenticatedBrokerLaunchReceipt:
    """Retrieve and authenticate one execution-addressed receipt/envelope pair."""

    try:
        validated = runtime_binding.validate_runtime_binding_policy(
            policy, executable=True
        )
        snapshot = runtime_binding._parse_policy(runtime_binding._canonical(validated))
        document = launch_binding.validate_launch_binding_policy(snapshot)
    except (runtime_binding.RuntimeBindingFailure, launch_binding.LaunchBindingFailure):
        raise LaunchReceiptMailboxFailure("mailbox_receiver.policy_invalid") from None
    if (
        type(action) is not str
        or action not in launch_binding.JOB_KINDS
        or not callable(getattr(transport, "get_exact_private_blob", None))
        or not callable(monotonic)
        or not callable(sleeper)
        or not callable(wall_clock)
    ):
        _fail("mailbox_receiver.configuration_invalid")
    execution_name = os.environ.get(launch_binding.EXECUTION_NAME_ENV)
    if (
        type(execution_name) is not str
        or launch_binding.EXECUTION_NAME_RE.fullmatch(execution_name) is None
    ):
        _fail("mailbox_receiver.execution_identity_invalid")
    receipt_uri, signature_envelope_uri = launch_binding._execution_receipt_uris(
        document, action, execution_name
    )
    retrieval = document["launch_binding"]["mailbox"]["execution_receipt_handoff"][
        "retrieval"
    ]
    timeline = _MonotonicTimeline(monotonic)
    started = timeline.read()
    deadline = started + retrieval["maximum_elapsed_seconds"]
    request_count = [0]
    receipt_bytes, receipt_statuses = _retrieve_exact_object(
        document=document,
        action=action,
        object_kind="receipt",
        execution_name=execution_name,
        uri=receipt_uri,
        maximum_bytes=document["launch_binding"]["launch_receipt"]["maximum_bytes"],
        transport=transport,
        timeline=timeline,
        deadline=deadline,
        sleeper=sleeper,
        request_count=request_count,
    )
    signature_envelope_bytes, signature_envelope_statuses = _retrieve_exact_object(
        document=document,
        action=action,
        object_kind="signature_envelope",
        execution_name=execution_name,
        uri=signature_envelope_uri,
        maximum_bytes=document["launch_binding"]["receipt_signing"][
            "signature_envelope"
        ]["maximum_bytes"],
        transport=transport,
        timeline=timeline,
        deadline=deadline,
        sleeper=sleeper,
        request_count=request_count,
    )
    finished = timeline.read()
    try:
        signature_verification = (
            receipt_auth.authenticate_broker_launch_receipt_signature(
                document,
                receipt_bytes=receipt_bytes,
                signature_envelope_bytes=signature_envelope_bytes,
            )
        )
        try:
            observed_at = wall_clock()
        except Exception:
            raise LaunchReceiptMailboxFailure(
                "mailbox_receiver.clock_invalid"
            ) from None
        if os.environ.get(launch_binding.EXECUTION_NAME_ENV) != execution_name:
            _fail("mailbox_receiver.execution_identity_invalid")
        return launch_binding.AuthenticatedBrokerLaunchReceipt._from_reviewed_platform_metadata_mailbox_signature_adapter(
            policy=document,
            action=action,
            receipt_uri=receipt_uri,
            signature_envelope_uri=signature_envelope_uri,
            receipt_retrieval_statuses=receipt_statuses,
            signature_envelope_retrieval_statuses=signature_envelope_statuses,
            retrieval_elapsed_seconds=math.ceil(finished - started),
            receipt_bytes=receipt_bytes,
            signature_envelope_bytes=signature_envelope_bytes,
            signature_verification=signature_verification,
            observed_at=observed_at,
        )
    except LaunchReceiptMailboxFailure:
        raise
    except (
        receipt_auth.BrokerLaunchReceiptAuthenticationFailure,
        launch_binding.LaunchBindingFailure,
    ):
        raise LaunchReceiptMailboxFailure("mailbox_receiver.authentication") from None
