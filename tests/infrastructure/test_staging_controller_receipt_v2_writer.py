from __future__ import annotations

import base64
import hashlib
import inspect
import json
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace as dataclass_replace
from datetime import timedelta
from email.message import Message
from typing import Any, Callable, Iterator

import pytest

from scripts import write_staging_controller_receipt_v2 as writer

from tests.infrastructure import (
    test_staging_controller_receipt_v2_builder as builder_helpers,
)
from tests.infrastructure import test_staging_runtime_record_private_readers as wire

NOW = builder_helpers.NOW
EPOCH_NOW = 1_799_150_000
TOKEN = "controller-receipt-writer-token"
SECRET = "Bearer secret://writer-provider"
Failure = writer.ControllerReceiptV2WriteFailure


class Response:
    def __init__(
        self,
        body: bytes,
        *,
        status: int,
        headers: dict[str, str],
        url: str | None = None,
        remove: str | None = None,
        replace: tuple[str, str] | None = None,
        duplicate: tuple[str, str] | None = None,
    ) -> None:
        self.body = body
        self.status = status
        self.url = url
        values = dict(headers)
        if remove is not None:
            values.pop(remove)
        if replace is not None:
            values[replace[0]] = replace[1]
        self.headers = Message()
        for name, value in values.items():
            self.headers.add_header(name, value)
        if duplicate is not None:
            self.headers.add_header(*duplicate)

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def geturl(self) -> str:
        assert self.url is not None
        return self.url

    def read(self, maximum: int) -> bytes:
        chunk, self.body = self.body[:maximum], self.body[maximum:]
        return chunk


class Opener:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[Any, int]] = []
        self.effects: list[Callable[[], None]] = []

    def open(self, request: Any, *, timeout: int) -> Any:
        self.calls.append((request, timeout))
        if self.effects:
            self.effects.pop(0)()
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        response.url = response.url or request.full_url
        return response


@contextmanager
def _no_deadline(_seconds: int) -> Iterator[None]:
    yield


def _http_date(value: Any) -> str:
    return value.strftime("%a, %d %b %Y %H:%M:%S GMT")


def _identity_response(built: Any, **changes: Any) -> Response:
    client_id = json.loads(built.receipt_bytes)["identity"][
        "provision_job_uami_client_id"
    ]
    body = {
        "access_token": TOKEN,
        "client_id": client_id,
        "expires_on": str(EPOCH_NOW + 3600),
        "resource": "https://storage.azure.com/",
        "token_type": "Bearer",
        **changes,
    }
    raw = json.dumps(body, separators=(",", ":")).encode()
    return Response(
        raw,
        status=200,
        headers={"Content-Type": "application/json", "Content-Length": str(len(raw))},
    )


def _put_response(
    *, status: int = 201, remove: str | None = None, url: str | None = None
) -> Response:
    return Response(
        b"",
        status=status,
        url=url,
        remove=remove,
        headers={
            "Content-Length": "0",
            "ETag": '"receipt-etag"',
            "Last-Modified": _http_date(NOW),
            "x-ms-request-server-encrypted": "true",
            "x-ms-version": "2021-12-02",
        },
    )


def _get_response(
    raw: bytes,
    *,
    status: int = 200,
    remove: str | None = None,
    replace: tuple[str, str] | None = None,
    duplicate: tuple[str, str] | None = None,
    url: str | None = None,
) -> Response:
    return Response(
        raw,
        status=status,
        url=url,
        remove=remove,
        replace=replace,
        duplicate=duplicate,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(raw)),
            "Content-MD5": base64.b64encode(
                hashlib.md5(raw, usedforsecurity=False).digest()
            ).decode("ascii"),
            "ETag": '"receipt-etag"',
            "Last-Modified": _http_date(NOW),
            "x-ms-immutability-policy-until-date": _http_date(NOW + timedelta(days=91)),
            "x-ms-immutability-policy-mode": "Locked",
            "x-ms-legal-hold": "true",
            "x-ms-server-encrypted": "true",
            "x-ms-blob-type": "BlockBlob",
            "x-ms-version": "2021-12-02",
        },
    )


@pytest.fixture(scope="module")
def built_chain() -> Any:
    monkeypatch = pytest.MonkeyPatch()
    try:
        yield builder_helpers._build(monkeypatch)
    finally:
        monkeypatch.undo()


def _instance(
    monkeypatch: pytest.MonkeyPatch,
    built: Any,
    *,
    put: Any | None = None,
    get: Any | None = None,
    clock: Callable[[], Any] = lambda: NOW,
    monotonic: Callable[[], float] = lambda: 10.0,
    identity: Opener | None = None,
    blob: Opener | None = None,
    deadline: Callable[[int], Any] = _no_deadline,
) -> tuple[writer.ControllerReceiptV2Writer, Opener, Opener]:
    monkeypatch.setenv("IDENTITY_ENDPOINT", wire.IDENTITY_ENDPOINT)
    monkeypatch.setenv("IDENTITY_HEADER", wire.IDENTITY_HEADER)
    identity = identity or Opener([_identity_response(built)])
    blob = blob or Opener(
        [put or _put_response(), get or _get_response(built.receipt_bytes)]
    )
    instance = writer.ControllerReceiptV2Writer._from_reviewed_configuration(
        clock=clock,
        identity_opener=identity,
        blob_opener=blob,
        epoch_clock=lambda: EPOCH_NOW,
        monotonic=monotonic,
        deadline_factory=deadline,
    )
    return instance, identity, blob


def _headers(request: Any) -> dict[str, str]:
    return {name.casefold(): value for name, value in request.header_items()}


def _reuse_builder_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep transport matrices focused; dedicated tests below use real reauth."""

    monkeypatch.setattr(
        writer.builder.BuiltControllerReceiptV2,
        "_is_built",
        lambda _self: True,
    )


def test_exact_201_put_and_get_mint_narrow_authenticated_result(
    monkeypatch: pytest.MonkeyPatch, built_chain: Any
) -> None:
    _authority, built = built_chain
    deadline_calls: list[int] = []

    @contextmanager
    def deadline(seconds: int) -> Iterator[None]:
        deadline_calls.append(seconds)
        yield

    instance, identity, blob = _instance(monkeypatch, built, deadline=deadline)
    result = instance.write(built_receipt=built)

    assert result._is_authenticated()
    assert result.outcome == "created"
    assert result.receipt_sha256 == built.receipt_sha256
    assert result.record_chain_sha256 == built.record_chain_sha256
    assert result.authority_chain_sha256 == built.authority_chain_sha256
    assert result.controller_receipt_exists is True
    assert result.full_three_record_source_authenticated is True
    for name in (
        "create_only_behavior_proven",
        "writer_identity_use_authenticated",
        "writer_private_route_use_authenticated",
        "provider_facts_authenticated",
        "execution_history_authenticated",
        "blob_or_redis_copy_absence_proven",
        "database_mutation_authority",
        "sanitizer_authority",
        "runtime_authority",
        "executable",
        "workflow_integrated",
    ):
        assert getattr(result, name) is False

    assert len(identity.calls) == 1
    assert len(blob.calls) == 2
    assert deadline_calls == [15, 15, 15]
    put, get = (call[0] for call in blob.calls)
    assert (put.method, get.method) == ("PUT", "GET")
    assert put.full_url == get.full_url
    assert urllib.parse.urlsplit(put.full_url).query == ""
    assert put.data == built.receipt_bytes
    put_headers = _headers(put)
    get_headers = _headers(get)
    md5 = base64.b64encode(
        hashlib.md5(built.receipt_bytes, usedforsecurity=False).digest()
    ).decode("ascii")
    assert put_headers == {
        "accept": "application/json",
        "accept-encoding": "identity",
        "authorization": f"Bearer {TOKEN}",
        "content-length": str(len(built.receipt_bytes)),
        "content-md5": md5,
        "content-type": "application/json",
        "if-none-match": "*",
        "x-ms-blob-content-md5": md5,
        "x-ms-blob-type": "BlockBlob",
        "x-ms-date": _http_date(NOW),
        "x-ms-version": "2021-12-02",
    }
    assert get_headers == {
        "accept": "application/json",
        "accept-encoding": "identity",
        "authorization": f"Bearer {TOKEN}",
        "x-ms-date": _http_date(NOW),
        "x-ms-version": "2021-12-02",
    }
    assert SECRET not in repr(result)
    assert TOKEN not in repr(result)


@pytest.mark.parametrize(
    "put",
    (
        _put_response(status=412),
        _put_response(status=409),
        _put_response(remove="ETag"),
        _put_response(url="https://redirect.invalid/object"),
        RuntimeError(SECRET),
    ),
)
def test_unknown_put_outcomes_require_one_exact_get_and_reconcile_only_as_indeterminate(
    monkeypatch: pytest.MonkeyPatch, built_chain: Any, put: Any
) -> None:
    _authority, built = built_chain
    _reuse_builder_contract(monkeypatch)
    instance, _identity, blob = _instance(monkeypatch, built, put=put)
    result = instance.write(built_receipt=built)
    assert result.outcome == "reconciled_indeterminate"
    assert [call[0].method for call in blob.calls] == ["PUT", "GET"]
    assert result.create_only_behavior_proven is False


@pytest.mark.parametrize(
    "case",
    (
        "body_mismatch",
        "non_200",
        "missing_content_type",
        "missing_content_md5",
        "missing_etag",
        "missing_last_modified",
        "missing_retained_until",
        "missing_mode",
        "missing_hold",
        "missing_encryption",
        "missing_blob_type",
        "missing_version",
        "unlocked",
        "false_hold",
        "false_encryption",
        "weak_etag",
        "duplicate_md5",
        "short_future_retention",
    ),
)
def test_mismatched_or_partial_worm_readback_refuses(
    monkeypatch: pytest.MonkeyPatch, built_chain: Any, case: str
) -> None:
    _authority, built = built_chain
    _reuse_builder_contract(monkeypatch)
    missing = {
        "missing_content_type": "Content-Type",
        "missing_content_md5": "Content-MD5",
        "missing_etag": "ETag",
        "missing_last_modified": "Last-Modified",
        "missing_retained_until": "x-ms-immutability-policy-until-date",
        "missing_mode": "x-ms-immutability-policy-mode",
        "missing_hold": "x-ms-legal-hold",
        "missing_encryption": "x-ms-server-encrypted",
        "missing_blob_type": "x-ms-blob-type",
        "missing_version": "x-ms-version",
    }
    replacements = {
        "unlocked": ("x-ms-immutability-policy-mode", "Unlocked"),
        "false_hold": ("x-ms-legal-hold", "false"),
        "false_encryption": ("x-ms-server-encrypted", "false"),
        "weak_etag": ("ETag", 'W/"weak"'),
        "short_future_retention": (
            "x-ms-immutability-policy-until-date",
            _http_date(NOW + timedelta(days=89)),
        ),
    }
    raw = b"{}" if case == "body_mismatch" else built.receipt_bytes
    response = _get_response(
        raw,
        status=404 if case == "non_200" else 200,
        remove=missing.get(case),
        replace=replacements.get(case),
        duplicate=("Content-MD5", "duplicate") if case == "duplicate_md5" else None,
    )
    instance, _identity, blob = _instance(monkeypatch, built, get=response)
    with pytest.raises(Failure, match="readback_invalid") as error:
        instance.write(built_receipt=built)
    assert len(blob.calls) == 2
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


@pytest.mark.parametrize(
    ("modified", "retained"),
    (
        (NOW - timedelta(seconds=61), NOW + timedelta(days=91)),
        (NOW + timedelta(seconds=61), NOW + timedelta(days=91)),
    ),
    ids=("older-than-created", "beyond-future-skew"),
)
def test_readback_time_gates_are_independently_regressed(
    monkeypatch: pytest.MonkeyPatch,
    built_chain: Any,
    modified: Any,
    retained: Any,
) -> None:
    _authority, built = built_chain
    _reuse_builder_contract(monkeypatch)
    response = _get_response(built.receipt_bytes)
    response.headers.replace_header("Last-Modified", _http_date(modified))
    response.headers.replace_header(
        "x-ms-immutability-policy-until-date", _http_date(retained)
    )
    instance, _identity, _blob = _instance(monkeypatch, built, get=response)
    with pytest.raises(Failure, match="readback_invalid"):
        instance.write(built_receipt=built)


def test_expired_retention_is_rejected_independently(built_chain: Any) -> None:
    _authority, built = built_chain
    policy = writer.load_controller_receipt_v2_writer_policy()
    snapshot = dataclass_replace(
        writer._authority_snapshot(built, policy),
        created_at=NOW - timedelta(days=200),
    )
    response = _get_response(built.receipt_bytes)
    response.headers.replace_header(
        "Last-Modified", _http_date(NOW - timedelta(days=100))
    )
    response.headers.replace_header(
        "x-ms-immutability-policy-until-date", _http_date(NOW - timedelta(days=1))
    )
    wire_response = writer._WireResponse(200, built.receipt_bytes, response.headers)
    with pytest.raises(Failure, match="readback_invalid"):
        writer._verify_readback(
            wire_response, snapshot=snapshot, policy=policy, now=NOW
        )


@pytest.mark.parametrize(
    "get_change",
    (
        ("ETag", '"different-etag"'),
        ("Last-Modified", _http_date(NOW + timedelta(seconds=1))),
    ),
    ids=("etag", "last-modified"),
)
def test_created_response_must_match_exact_get_causally(
    monkeypatch: pytest.MonkeyPatch, built_chain: Any, get_change: tuple[str, str]
) -> None:
    _authority, built = built_chain
    _reuse_builder_contract(monkeypatch)
    response = _get_response(built.receipt_bytes, replace=get_change)
    instance, _identity, blob = _instance(monkeypatch, built, get=response)
    with pytest.raises(Failure, match="readback_invalid"):
        instance.write(built_receipt=built)
    assert [call[0].method for call in blob.calls] == ["PUT", "GET"]


@pytest.mark.parametrize(
    "case",
    ("missing_length", "noncanonical_length", "transfer", "redirect", "oversize"),
)
def test_redirect_oversize_and_framing_fail_closed(
    monkeypatch: pytest.MonkeyPatch, built_chain: Any, case: str
) -> None:
    _authority, built = built_chain
    _reuse_builder_contract(monkeypatch)
    raw = b"x" * 65_537 if case == "oversize" else built.receipt_bytes
    response = _get_response(
        raw,
        remove="Content-Length" if case == "missing_length" else None,
        replace=("Content-Length", f"0{len(raw)}")
        if case == "noncanonical_length"
        else (("Transfer-Encoding", "chunked") if case == "transfer" else None),
        url="https://redirect.invalid/object" if case == "redirect" else None,
    )
    instance, _identity, blob = _instance(monkeypatch, built, get=response)
    with pytest.raises(Failure, match="readback_invalid"):
        instance.write(built_receipt=built)
    assert [call[0].method for call in blob.calls] == ["PUT", "GET"]


def test_identity_and_get_transport_failures_are_sanitized(
    monkeypatch: pytest.MonkeyPatch, built_chain: Any
) -> None:
    _authority, built = built_chain
    _reuse_builder_contract(monkeypatch)
    identity = Opener([RuntimeError(SECRET)])
    instance, _identity, blob = _instance(
        monkeypatch, built, identity=identity, blob=Opener([])
    )
    with pytest.raises(Failure, match="identity_failed") as error:
        instance.write(built_receipt=built)
    assert SECRET not in repr(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert blob.calls == []

    instance, _identity, blob = _instance(monkeypatch, built, get=RuntimeError(SECRET))
    with pytest.raises(Failure, match="readback_invalid") as error:
        instance.write(built_receipt=built)
    assert SECRET not in repr(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert [call[0].method for call in blob.calls] == ["PUT", "GET"]


def test_total_deadline_and_clock_fail_closed(
    monkeypatch: pytest.MonkeyPatch, built_chain: Any
) -> None:
    _authority, built = built_chain
    _reuse_builder_contract(monkeypatch)
    times = iter((0.0, 0.0, 0.0, 61.0))
    instance, _identity, blob = _instance(
        monkeypatch, built, monotonic=lambda: next(times)
    )
    with pytest.raises(Failure, match="deadline"):
        instance.write(built_receipt=built)
    assert len(blob.calls) <= 1

    values = iter((NOW, NOW - timedelta(seconds=1)))
    instance, _identity, blob = _instance(
        monkeypatch, built, clock=lambda: next(values)
    )
    with pytest.raises(Failure, match="clock_regressed"):
        instance.write(built_receipt=built)
    assert blob.calls == []


def test_one_shot_is_atomic_under_concurrency(
    monkeypatch: pytest.MonkeyPatch, built_chain: Any
) -> None:
    _authority, built = built_chain
    _reuse_builder_contract(monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    identity = Opener([_identity_response(built)])

    def pause() -> None:
        entered.set()
        release.wait(timeout=5)

    identity.effects = [pause]
    instance, _identity, _blob = _instance(monkeypatch, built, identity=identity)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(instance.write, built_receipt=built)
        assert entered.wait(timeout=120)
        second = pool.submit(instance.write, built_receipt=built)
        with pytest.raises(Failure, match="already_used"):
            second.result(timeout=30)
        release.set()
        assert first.result(timeout=180).outcome == "created"
    assert len(identity.calls) == 1


@pytest.mark.parametrize(
    "level", ("built", "inventory", "database", "arm", "production")
)
def test_full_ancestor_tamper_refuses_before_credentials(
    monkeypatch: pytest.MonkeyPatch, built_chain: Any, level: str
) -> None:
    authority, built = built_chain
    targets = {
        "built": (built, "receipt_sha256"),
        "inventory": (authority, "rehearsal_id"),
        "database": (authority._database_observation, "rehearsal_id"),
        "arm": (authority._database_observation._arm_observation, "rehearsal_id"),
        "production": (
            authority._database_observation._production_authority,
            "rehearsal_id",
        ),
    }
    target, field = targets[level]
    original = getattr(target, field)
    try:
        object.__setattr__(target, field, "0" * (71 if level == "built" else 12))
        instance, identity, blob = _instance(monkeypatch, built)
        with pytest.raises(Failure, match="authority_invalid"):
            instance.write(built_receipt=built)
        assert identity.calls == []
        assert blob.calls == []
    finally:
        object.__setattr__(target, field, original)


@pytest.mark.parametrize("stage", ("identity", "put", "get"))
def test_mid_call_tamper_is_detected_without_a_second_put(
    monkeypatch: pytest.MonkeyPatch, built_chain: Any, stage: str
) -> None:
    _authority, built = built_chain
    instance, identity, blob = _instance(monkeypatch, built)
    original = built.receipt_sha256

    def tamper() -> None:
        object.__setattr__(built, "receipt_sha256", "sha256:" + "f" * 64)

    if stage == "identity":
        identity.effects = [tamper]
    elif stage == "put":
        blob.effects = [tamper]
    else:
        blob.effects = [lambda: None, tamper]
    try:
        with pytest.raises(Failure, match="authority_invalid"):
            instance.write(built_receipt=built)
        assert sum(call[0].method == "PUT" for call in blob.calls) <= 1
    finally:
        object.__setattr__(built, "receipt_sha256", original)


def test_policy_api_surface_hashes_dormancy_and_result_tamper(
    monkeypatch: pytest.MonkeyPatch, built_chain: Any
) -> None:
    _authority, built = built_chain
    _reuse_builder_contract(monkeypatch)
    policy = writer.load_controller_receipt_v2_writer_policy()
    assert hashlib.sha256(writer.POLICY_PATH.read_bytes()).hexdigest() == (
        writer.POLICY_SHA256
    )
    assert policy["activation_ready"] is False
    assert policy["activation"]["controller_receipt_writer_implemented"] is True
    assert all(
        value is False
        for name, value in policy["activation"].items()
        if name != "controller_receipt_writer_implemented"
    )
    assert policy["provider_contract"]["preexisting_exact"] == {
        "accepted": False,
        "status_codes": [],
    }
    assert set(
        inspect.signature(writer.ControllerReceiptV2Writer.write).parameters
    ) == {
        "self",
        "built_receipt",
    }
    assert not inspect.signature(writer.build_controller_receipt_v2_writer).parameters
    with pytest.raises(Failure, match="configuration_invalid"):
        writer.ControllerReceiptV2Writer()
    with pytest.raises(TypeError):
        writer.ControllerReceiptV2Writer._from_reviewed_configuration(  # type: ignore[call-arg]
            clock=lambda: NOW
        )
    with pytest.raises(Failure, match="configuration_invalid"):
        writer.ControllerReceiptV2Writer._from_reviewed_configuration(
            clock=lambda: NOW,
            identity_opener=None,  # type: ignore[arg-type]
            blob_opener=None,  # type: ignore[arg-type]
            epoch_clock=lambda: EPOCH_NOW,
            monotonic=lambda: 10.0,
            deadline_factory=_no_deadline,
        )
    with pytest.raises(Failure, match="policy_disabled"):
        writer.build_controller_receipt_v2_writer()
    with pytest.raises(Failure, match="policy_disabled"):
        writer.load_controller_receipt_v2_writer_policy(executable=True)
    changed = deepcopy(policy)
    changed["claims"]["create_only_behavior_proven"] = True
    with pytest.raises(Failure, match="policy_invalid"):
        writer.validate_controller_receipt_v2_writer_policy(changed)

    instance, _identity, _blob = _instance(monkeypatch, built)
    result = instance.write(built_receipt=built)
    original = result.etag
    try:
        object.__setattr__(result, "etag", '"changed"')
        assert result._is_authenticated() is False
    finally:
        object.__setattr__(result, "etag", original)
    with pytest.raises(Failure, match="result_invalid"):
        writer.WrittenControllerReceiptV2()
