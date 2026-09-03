from __future__ import annotations

import http.client
import importlib.util
import json
import sys
import urllib.error
from base64 import b64encode
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from email.message import Message
from email.utils import format_datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/staging_restore_safe_submit.py"
ENVELOPE_POLICY = ROOT / "contracts/staging-rehearsal/v1/restore-envelope-policy.json"
SAFE_POLICY = ROOT / "contracts/staging-rehearsal/v1/restore-safe-submit-policy.json"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("staging_restore_safe_submit", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


safe_submit = _load_module()
TEST_NOW = datetime(2026, 9, 3, 12, 1, tzinfo=UTC)


def _config(**changes: Any) -> Any:
    envelope = json.loads(ENVELOPE_POLICY.read_text())
    policy = json.loads(SAFE_POLICY.read_text())
    config = safe_submit.config_from_policies(envelope, policy, executable=False)
    defaults = {
        "blob_marker_store_implemented": True,
        "blob_marker_provider_fidelity_proven": True,
        "private_runner_binding_implemented": True,
        "execution_authorized": True,
        "provision_client_id": "configured-for-pure-fake-test",
        "provision_principal_id": "configured-for-pure-fake-test",
        "marker_account_resource_id": "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/restore-control/providers/Microsoft.Storage/storageAccounts/restorecontrol",
        "marker_account_host": "restorecontrol.blob.core.windows.net",
        "poll_interval_seconds": 0,
        "marker_read_poll_attempts": 2,
        "marker_read_poll_interval_seconds": 0,
        "restore_poll_attempts": 2,
        "cleanup_poll_attempts": 2,
    }
    defaults.update(changes)
    return replace(config, **defaults)


def _request() -> Any:
    created = datetime(2026, 9, 3, 12, tzinfo=UTC)
    return safe_submit.RestoreRequest(
        rehearsal_id="012345abcdef",
        restore_time=created - timedelta(minutes=5),
        created_at=created,
        cleanup_not_after=created + timedelta(hours=6),
        repository="CosmaNeura/cosmaneura",
        workflow_sha="a" * 40,
        run_id="901",
        run_attempt="1",
    )


def _engine(
    transport: Any, marker_store: Any | None = None, **config_changes: Any
) -> Any:
    return safe_submit.SafeRestoreSubmitter(
        transport,
        marker_store or FakeMarkers(),
        _config(**config_changes),
        sleeper=lambda _: None,
        clock=lambda: TEST_NOW,
    )


class FakeArm:
    def __init__(self) -> None:
        self.target: dict[str, Any] | None = None
        self.calls: list[dict[str, Any]] = []
        self.restore_put_unknown = False
        self.patch_status = 202
        self.restore_public_access = "Disabled"

    def request(
        self,
        method: str,
        resource_id: str,
        *,
        api_version: str,
        body: dict[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_seconds: int,
    ) -> Any:
        self.calls.append(
            {
                "method": method,
                "resource_id": resource_id,
                "api_version": api_version,
                "body": deepcopy(body),
                "headers": dict(headers or {}),
                "timeout_seconds": timeout_seconds,
            }
        )
        if method == "GET":
            if self.target is None:
                return safe_submit.ArmResponse(404)
            return safe_submit.ArmResponse(200, deepcopy(self.target))
        if method == "PUT":
            if self.restore_put_unknown:
                raise safe_submit.ArmOutcomeUnknown
            assert body is not None
            self.materialize(
                resource_id, body=body, public_access=self.restore_public_access
            )
            return safe_submit.ArmResponse(202)
        if method == "PATCH":
            if self.target is None:
                return safe_submit.ArmResponse(404)
            if self.patch_status in {200, 202, 204}:
                self.target["properties"]["network"]["publicNetworkAccess"] = "Disabled"
            return safe_submit.ArmResponse(self.patch_status)
        if method == "DELETE":
            self.target = None
            return safe_submit.ArmResponse(202)
        raise AssertionError(f"unhandled fake ARM request: {method} {resource_id}")

    def materialize(
        self,
        resource_id: str,
        *,
        body: dict[str, Any] | None = None,
        tags: dict[str, str] | None | object = ...,  # type: ignore[assignment]
        source_server_resource_id: str | None = None,
        public_access: str = "Disabled",
        state: str = "Ready",
        include_create_fields: bool = True,
        created_by: str = "configured-for-pure-fake-test",
    ) -> None:
        assert body is not None or source_server_resource_id is not None
        name = resource_id.rsplit("/", 1)[-1]
        properties: dict[str, Any]
        if body:
            properties = deepcopy(body["properties"])
        elif include_create_fields:
            properties = {
                "createMode": "PointInTimeRestore",
                "sourceServerResourceId": source_server_resource_id,
                "pointInTimeUTC": "2026-09-03T11:55:00Z",
                "network": {"publicNetworkAccess": public_access},
            }
        else:
            properties = {"network": {"publicNetworkAccess": public_access}}
        properties["state"] = state
        properties.setdefault("network", {})["publicNetworkAccess"] = public_access
        target: dict[str, Any] = {
            "id": resource_id,
            "name": name,
            "type": "Microsoft.DBforPostgreSQL/flexibleServers",
            "location": "northcentralus",
            "etag": '"target-etag"',
            "properties": properties,
            "systemData": {
                "createdAt": "2026-09-03T12:00:00.123456Z",
                "createdBy": created_by,
                "createdByType": "Application",
            },
        }
        if tags is ...:
            target["tags"] = deepcopy(body["tags"] if body else {})
        elif tags is not None:
            target["tags"] = deepcopy(tags)
        self.target = target


class FakeMarkers:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], bytes] = {}
        self.calls: list[tuple[str, str, str]] = []
        self.unknown_reads: dict[tuple[str, str], int] = {}
        self.invalid_present: set[tuple[str, str]] = set()
        self.fail_create_before: set[tuple[str, str]] = set()
        self.fail_create_after: set[tuple[str, str]] = set()
        self.on_read: Any | None = None
        self.cleanup_raw = b""

    def read(self, kind: str, rehearsal_id: str) -> bytes | None:
        key = (kind, rehearsal_id)
        self.calls.append(("GET", kind, rehearsal_id))
        if self.on_read:
            self.on_read(kind, rehearsal_id)
        if self.unknown_reads.get(key, 0):
            self.unknown_reads[key] -= 1
            raise safe_submit.MarkerOutcomeUnknown
        if key in self.invalid_present:
            raise safe_submit.MarkerPresentInvalid("marker_properties_unproven")
        return self.records.get(key)

    def create_if_absent(self, kind: str, rehearsal_id: str, raw: bytes) -> bytes:
        key = (kind, rehearsal_id)
        self.calls.append(("PUT", kind, rehearsal_id))
        if key in self.fail_create_before:
            raise safe_submit.MarkerOutcomeUnknown
        existing = self.records.get(key)
        if existing is not None and existing != raw:
            raise safe_submit.MarkerConflict("marker_record_conflict")
        self.records.setdefault(key, raw)
        if key in self.fail_create_after:
            raise safe_submit.MarkerOutcomeUnknown
        return self.records[key]

    def list_intent_ids(self) -> tuple[str, ...]:
        return tuple(
            rehearsal_id for kind, rehearsal_id in self.records if kind == "intent"
        )


class FakeHeaders(dict[str, str]):
    def __init__(self, *args: Any, fail: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fail = fail

    def items(self) -> Any:
        if self.fail:
            raise RuntimeError("headers unavailable")
        return super().items()


class FakeHttpResponse:
    def __init__(
        self,
        status: int,
        body: bytes = b"",
        headers: Mapping[str, str] | None = None,
        *,
        url: str = "",
        failure: str = "",
    ) -> None:
        self._status = status
        self.body = body
        self.headers = FakeHeaders(headers or {}, fail=failure == "headers")
        self.url = url
        self.failure = failure

    @property
    def status(self) -> int:
        if self.failure == "status":
            raise RuntimeError("status unavailable")
        return self._status

    def geturl(self) -> str:
        return self.url

    def read(self, _: int) -> bytes:
        if self.failure == "read":
            raise http.client.IncompleteRead(b"partial", 10)
        return self.body

    def __enter__(self) -> FakeHttpResponse:
        return self

    def __exit__(self, *_: Any) -> None:
        if self.failure == "exit":
            raise RuntimeError("close failed")


class FakeOpener:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.requests: list[Any] = []

    def open(self, request: Any, *, timeout: int) -> Any:
        self.requests.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if not response.url:
            response.url = request.full_url
        return response


def _marker_headers(raw: bytes, **changes: str) -> dict[str, str]:
    modified = TEST_NOW
    headers = {
        "content-length": str(len(raw)),
        "content-md5": b64encode(
            safe_submit.hashlib.md5(raw, usedforsecurity=False).digest()
        ).decode(),
        "content-type": "application/json",
        "etag": '"marker-etag"',
        "last-modified": format_datetime(modified, usegmt=True),
        "x-ms-blob-type": "BlockBlob",
        "x-ms-immutability-policy-mode": "locked",
        "x-ms-immutability-policy-until-date": format_datetime(
            modified + timedelta(days=90), usegmt=True
        ),
        "x-ms-legal-hold": "true",
        "x-ms-server-encrypted": "true",
        "x-ms-version": "2021-12-02",
    }
    headers.update(changes)
    return headers


def _create_headers(**changes: str) -> dict[str, str]:
    headers = {
        "etag": '"marker-etag"',
        "x-ms-request-server-encrypted": "true",
        "x-ms-version": "2021-12-02",
    }
    headers.update(changes)
    return headers


def _blob_store(opener: FakeOpener, **changes: Any) -> Any:
    class TestStore(safe_submit.AzureBlobMarkerStore):
        def _access_token(self) -> str:
            return "test-storage-token"

    return TestStore(
        _config(**changes),
        opener=opener,
        sleeper=lambda _: None,
        clock=lambda: TEST_NOW,
    )


def _http_error(status: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("test", status, "bounded", Message(), None)


def _target_id(engine: Any, request: Any) -> str:
    return engine._build_intent(request)["target_server_resource_id"]


def _mutations(fake: FakeArm, method: str) -> list[dict[str, Any]]:
    return [call for call in fake.calls if call["method"] == method]


def _configure_cleanup_race(markers: Any, engine: Any) -> None:
    intent = engine._build_intent(_request())
    digest = f"sha256:{safe_submit._sha256(safe_submit._canonical(intent))}"
    markers.cleanup_raw = safe_submit._canonical(engine._cleanup_record(intent, digest))


@pytest.mark.parametrize(
    ("method", "failure", "expected"),
    [
        *[
            ("PUT", failure, "unknown")
            for failure in "malformed oversized incomplete status headers exit".split()
        ],
        ("GET", "status", "arm_response_invalid"),
        ("GET", "oversized", "arm_response_too_large"),
        ("PUT", "http-error", "status:409"),
    ],
)
def test_response_processing_failures_have_bounded_method_semantics(
    monkeypatch: pytest.MonkeyPatch, method: str, failure: str, expected: str
) -> None:
    class Headers(dict[str, str]):
        def items(self) -> Any:
            if failure == "headers":
                raise RuntimeError("broken headers")
            return super().items()

    class HttpResponse:
        @property
        def status(self) -> int:
            if failure == "status":
                raise RuntimeError("broken status")
            return 202

        headers = Headers({"x-ms-request-id": "test-request"})

        def __enter__(self) -> HttpResponse:
            return self

        def __exit__(self, *_: Any) -> None:
            if failure == "exit":
                raise RuntimeError("broken close")

        def read(self, _: int) -> bytes:
            if failure == "incomplete":
                raise http.client.IncompleteRead(b"partial", 10)
            if failure == "malformed":
                return b"{"
            if failure == "oversized":
                return b"x" * 1_048_577
            return b"{}"

    transport = safe_submit.AzureArmTransport()
    monkeypatch.setattr(transport, "_access_token", lambda _: "test-token")

    def urlopen(*_args: Any, **_kwargs: Any) -> HttpResponse:
        if failure == "http-error":
            raise _http_error(409)
        return HttpResponse()

    monkeypatch.setattr(safe_submit.urllib.request, "urlopen", urlopen)

    def make_request() -> Any:
        return transport.request(
            method,
            "/subscriptions/test/resource",
            api_version="2024-08-01",
            timeout_seconds=30,
        )

    if expected == "status:409":
        assert make_request().status == 409
        return
    error_type = (
        safe_submit.ArmOutcomeUnknown
        if expected == "unknown"
        else safe_submit.SafeSubmitError
    )
    with pytest.raises(error_type) as raised:
        make_request()
    if expected != "unknown":
        assert raised.value.code == expected


def test_blob_create_is_exact_conditional_raw_byte_write_with_readback() -> None:
    raw = b'{"kind":"intent"}'
    opener = FakeOpener(
        [
            _http_error(404),
            FakeHttpResponse(201, headers=_create_headers()),
            FakeHttpResponse(200, raw, _marker_headers(raw)),
        ]
    )
    store = _blob_store(opener)
    assert store.create_if_absent("intent", "012345abcdef", raw) == raw
    assert [item[0].get_method() for item in opener.requests] == ["GET", "PUT", "GET"]
    request, timeout = opener.requests[1]
    assert request.full_url == (
        "https://restorecontrol.blob.core.windows.net/"
        "restore-intents-v3/012345abcdef.json"
    )
    headers = {key.lower(): value for key, value in request.header_items()}
    assert headers["authorization"] == "Bearer test-storage-token"
    assert headers["if-none-match"] == "*"
    assert headers["x-ms-blob-type"] == "BlockBlob"
    assert headers["x-ms-version"] == "2021-12-02"
    assert headers["content-md5"] == headers["x-ms-blob-content-md5"]
    assert request.data == raw and timeout == 30


@pytest.mark.parametrize(
    "failure", ["status", "headers", "read", "exit", "oversized", "missing-proof"]
)
def test_blob_mutation_response_failures_reconcile_without_resubmit(
    failure: str,
) -> None:
    raw = b'{"kind":"intent"}'
    opener = FakeOpener(
        [
            _http_error(404),
            FakeHttpResponse(
                201,
                b"x" * 65_537 if failure == "oversized" else b"",
                {} if failure == "missing-proof" else _create_headers(),
                failure=failure,
            ),
            FakeHttpResponse(200, raw, _marker_headers(raw)),
        ]
    )
    assert _blob_store(opener).create_if_absent("intent", "012345abcdef", raw) == raw
    assert [item[0].get_method() for item in opener.requests].count("PUT") == 1


@pytest.mark.parametrize("failure", ["headers", "read", "exit"])
def test_blob_get_post_status_failures_preserve_present_signal(failure: str) -> None:
    response = FakeHttpResponse(200, failure=failure)
    with pytest.raises(safe_submit.MarkerPresentInvalid):
        _blob_store(FakeOpener([response])).read("cleanup", "012345abcdef")


@pytest.mark.parametrize(
    "missing",
    [
        "etag",
        "content-md5",
        "x-ms-blob-type",
        "x-ms-version",
        "x-ms-server-encrypted",
        "x-ms-immutability-policy-mode",
        "x-ms-immutability-policy-until-date",
        "x-ms-legal-hold",
    ],
)
def test_blob_read_rejects_partial_worm_or_encryption_proof(missing: str) -> None:
    raw = b'{"kind":"intent"}'
    headers = _marker_headers(raw)
    del headers[missing]
    store = _blob_store(FakeOpener([FakeHttpResponse(200, raw, headers)]))
    with pytest.raises(safe_submit.MarkerPresentInvalid):
        store.read("intent", "012345abcdef")


def test_blob_read_rejects_short_retention_window() -> None:
    raw = b'{"kind":"intent"}'
    too_short = format_datetime(TEST_NOW + timedelta(days=89), usegmt=True)
    headers = _marker_headers(raw, **{"x-ms-immutability-policy-until-date": too_short})
    with pytest.raises(safe_submit.MarkerPresentInvalid):
        _blob_store(FakeOpener([FakeHttpResponse(200, raw, headers)])).read(
            "intent", "012345abcdef"
        )


def test_blob_timeout_reconciliation_never_repeats_put() -> None:
    raw = b'{"kind":"intent"}'
    accepted = FakeOpener(
        [
            _http_error(404),
            TimeoutError(),
            FakeHttpResponse(200, raw, _marker_headers(raw)),
        ]
    )
    assert _blob_store(accepted).create_if_absent("intent", "012345abcdef", raw) == raw
    assert [item[0].get_method() for item in accepted.requests].count("PUT") == 1

    absent = FakeOpener(
        [
            _http_error(404),
            TimeoutError(),
            _http_error(404),
            _http_error(404),
        ]
    )
    with pytest.raises(safe_submit.MarkerOutcomeUnknown):
        _blob_store(absent).create_if_absent("intent", "012345abcdef", raw)
    assert [item[0].get_method() for item in absent.requests].count("PUT") == 1


def test_blob_conditional_conflict_and_unknown_then_404_fail_closed() -> None:
    expected = b'{"kind":"intent"}'
    other = b'{"kind":"other"}'
    replay = FakeOpener(
        [
            _http_error(404),
            _http_error(412),
            FakeHttpResponse(200, expected, _marker_headers(expected)),
        ]
    )
    assert (
        _blob_store(replay).create_if_absent("intent", "012345abcdef", expected)
        == expected
    )
    conflict = FakeOpener(
        [
            _http_error(404),
            _http_error(412),
            FakeHttpResponse(200, other, _marker_headers(other)),
        ]
    )
    with pytest.raises(safe_submit.MarkerConflict):
        _blob_store(conflict).create_if_absent("intent", "012345abcdef", expected)

    uncertain = FakeOpener(
        [
            TimeoutError(),
            _http_error(404),
        ]
    )
    with pytest.raises(safe_submit.MarkerOutcomeUnknown):
        _blob_store(uncertain).read("intent", "012345abcdef")


def test_blob_redirect_and_oversize_are_bounded_refusals() -> None:
    default_store = safe_submit.AzureBlobMarkerStore(_config())
    assert any(
        isinstance(handler, safe_submit._RefuseRedirects)
        for handler in default_store.opener.handlers
    )
    redirected = FakeHttpResponse(307, url="https://evil.example/marker")
    with pytest.raises(safe_submit.SafeSubmitError, match="blob_redirect_refused"):
        _blob_store(FakeOpener([redirected])).read("intent", "012345abcdef")
    oversized = b"x" * 65_537
    with pytest.raises(safe_submit.MarkerPresentInvalid, match="marker_body_too_large"):
        _blob_store(FakeOpener([FakeHttpResponse(200, oversized)])).read(
            "intent", "012345abcdef"
        )


def test_blob_list_is_paginated_and_bounded() -> None:
    first = b"<EnumerationResults><Blobs><Blob><Name>012345abcdef.json</Name></Blob></Blobs><NextMarker>next page</NextMarker></EnumerationResults>"
    second = b"<EnumerationResults><Blobs><Blob><Name>fedcba543210.json</Name></Blob></Blobs><NextMarker /></EnumerationResults>"
    list_headers = {"content-type": "application/xml", "x-ms-version": "2021-12-02"}
    opener = FakeOpener(
        [
            FakeHttpResponse(200, first, list_headers),
            FakeHttpResponse(200, second, list_headers),
        ]
    )
    assert _blob_store(opener).list_intent_ids() == (
        "012345abcdef",
        "fedcba543210",
    )
    assert "marker=next+page" in opener.requests[1][0].full_url
    overflow = FakeOpener([FakeHttpResponse(200, first, list_headers)])
    with pytest.raises(safe_submit.SafeSubmitError, match="marker_list_bound_exceeded"):
        _blob_store(overflow, marker_list_max_pages=1).list_intent_ids()
    two = b"<EnumerationResults><Blobs><Blob><Name>012345abcdef.json</Name></Blob><Blob><Name>fedcba543210.json</Name></Blob></Blobs><NextMarker /></EnumerationResults>"
    with pytest.raises(safe_submit.SafeSubmitError, match="marker_list_bound_exceeded"):
        _blob_store(
            FakeOpener([FakeHttpResponse(200, two, list_headers)]),
            marker_list_max_records=1,
        ).list_intent_ids()


def test_blob_oauth_audience_and_dormant_host_are_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _blob_store(FakeOpener([]))
    seen: list[str] = []

    class Result:
        returncode = 0
        stdout = "token"

    def run(command: list[str], **_kwargs: Any) -> Result:
        seen.extend(command)
        return Result()

    monkeypatch.setattr(safe_submit.subprocess, "run", run)
    assert safe_submit.AzureBlobMarkerStore._access_token(store) == "token"
    assert seen[seen.index("--resource") + 1] == "https://storage.azure.com/"
    config = safe_submit.config_from_policies(
        json.loads(ENVELOPE_POLICY.read_text()),
        json.loads(SAFE_POLICY.read_text()),
        executable=False,
    )
    with pytest.raises(
        safe_submit.SafeSubmitError, match="blob_store_configuration_invalid"
    ):
        safe_submit.AzureBlobMarkerStore(config)
    with pytest.raises(
        safe_submit.SafeSubmitError, match="blob_store_configuration_invalid"
    ):
        safe_submit.AzureBlobMarkerStore(
            _config(marker_account_host="otheraccount.blob.core.windows.net")
        )


def test_canonical_contract_is_pinned_and_dormant() -> None:
    envelope, policy = safe_submit.validate_canonical_contracts()
    assert envelope["trusted_evidence_binding_implemented"] is False
    assert tuple(sorted(safe_submit._placeholder_values(envelope))) == (
        safe_submit.EXPECTED_PLACEHOLDERS
    )
    assert policy["activation"] == {
        "blob_marker_store_implemented": False,
        "blob_marker_provider_fidelity_proven": False,
        "private_runner_binding_implemented": False,
        "workflow_integrated": False,
        "trusted_evidence_binding_implemented": False,
    }
    assert policy["schema_version"] == 3
    assert policy["marker_store"]["kind"] == "azure-blob-immutable-create-only-v1"
    assert policy["marker_store"]["rbac"]["create_read_data_actions"].startswith(
        "CONFIGURE_EXACT_"
    )
    assert policy["marker_store"]["immutability"] == {
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
    }
    assert len(safe_submit._placeholder_values(policy["marker_store"])) >= 25
    with pytest.raises(safe_submit.SafeSubmitError, match="safe_submit_not_activated"):
        safe_submit.config_from_policies(envelope, policy, executable=True)
    config = safe_submit.config_from_policies(envelope, policy, executable=False)
    fake = FakeArm()
    markers = FakeMarkers()
    engine = safe_submit.SafeRestoreSubmitter(
        fake, markers, config, sleeper=lambda _: None, clock=lambda: TEST_NOW
    )
    with pytest.raises(safe_submit.SafeSubmitError, match="safe_submit_not_activated"):
        engine.submit(_request())
    assert fake.calls == []
    engine = safe_submit.SafeRestoreSubmitter(
        fake,
        markers,
        replace(config, execution_authorized=True),
        clock=lambda: TEST_NOW,
    )
    with pytest.raises(
        safe_submit.SafeSubmitError, match="blob_marker_store_not_implemented"
    ):
        engine.submit(_request())


@pytest.mark.parametrize("changed", ["envelope", "safe", "both"])
def test_modified_policy_copies_cannot_create_an_executable_config(
    changed: str,
) -> None:
    envelope = json.loads(ENVELOPE_POLICY.read_text())
    policy = json.loads(SAFE_POLICY.read_text())
    if changed in {"envelope", "both"}:
        serialized = json.dumps(envelope)
        for placeholder in safe_submit.EXPECTED_PLACEHOLDERS:
            serialized = serialized.replace(placeholder, "configured-for-test")
        envelope = json.loads(serialized)
        envelope["trusted_evidence_binding_implemented"] = True
    if changed in {"safe", "both"}:
        policy["activation"] = {key: True for key in policy["activation"]}
    with pytest.raises(
        safe_submit.SafeSubmitError, match="executable_policy_not_canonical"
    ):
        safe_submit.config_from_policies(envelope, policy, executable=True)


def test_accepted_restore_persists_both_markers_before_exact_arm_put() -> None:
    fake = FakeArm()
    markers = FakeMarkers()
    engine = _engine(fake, markers)
    outcome = engine.submit(_request())
    assert outcome.status == "ready"
    writes = [call for call in fake.calls if call["method"] == "PUT"]
    assert len(writes) == 1
    assert markers.calls[:4] == [
        ("PUT", "intent", "012345abcdef"),
        ("GET", "cleanup", "012345abcdef"),
        ("GET", "submission", "012345abcdef"),
        ("PUT", "submission", "012345abcdef"),
    ]
    restore = writes[0]
    assert restore["api_version"] == "2024-08-01"
    assert restore["headers"] == {"If-None-Match": "*"}
    assert restore["timeout_seconds"] == 30
    assert restore["body"]["properties"]["network"] == {
        "publicNetworkAccess": "Disabled"
    }
    assert set(markers.records) == {
        ("intent", "012345abcdef"),
        ("submission", "012345abcdef"),
    }


def test_unknown_submission_never_claims_absence_or_completion() -> None:
    fake = FakeArm()
    fake.restore_put_unknown = True
    markers = FakeMarkers()
    engine = _engine(fake, markers)
    outcome = engine.submit(_request())
    assert outcome.status == "pending_late_materialization"
    assert outcome.target_observed is False
    assert outcome.cleanup_required is True
    assert set(markers.records) == {
        ("intent", "012345abcdef"),
        ("submission", "012345abcdef"),
        ("cleanup", "012345abcdef"),
    }
    assert len(_mutations(fake, "PUT")) == 1
    assert _mutations(fake, "DELETE") == []
    retry = engine.submit(_request())
    assert retry.status == "pending_late_materialization"
    assert len(_mutations(fake, "PUT")) == 1


def test_expired_fresh_intent_is_rejected_before_any_target_access() -> None:
    fake = FakeArm()
    markers = FakeMarkers()
    engine = safe_submit.SafeRestoreSubmitter(
        fake,
        markers,
        _config(),
        sleeper=lambda _: None,
        clock=lambda: datetime(2026, 9, 3, 18, tzinfo=UTC),
    )
    with pytest.raises(safe_submit.SafeSubmitError, match="intent_expired"):
        engine.submit(_request())
    assert fake.calls == []
    assert markers.calls == [("GET", "intent", "012345abcdef")]


@pytest.mark.parametrize(
    ("before_count", "status", "put_count", "delete_count"),
    [(3, "pending_late_materialization", 0, 0), (4, "cleaned", 1, 1)],
)
def test_expiry_at_late_submission_boundaries_enters_cleanup(
    before_count: int, status: str, put_count: int, delete_count: int
) -> None:
    fake = FakeArm()
    markers = FakeMarkers()
    at_expiry = datetime(2026, 9, 3, 18, tzinfo=UTC)
    times = iter([TEST_NOW] * before_count + [at_expiry])
    engine = safe_submit.SafeRestoreSubmitter(
        fake, markers, _config(), sleeper=lambda _: None, clock=lambda: next(times)
    )
    outcome = engine.submit(_request())
    assert outcome.status == status
    assert outcome.cleanup_required is (status != "cleaned")
    assert ("cleanup", "012345abcdef") in markers.records
    assert len(_mutations(fake, "PUT")) == put_count
    assert len(_mutations(fake, "DELETE")) == delete_count


@pytest.mark.parametrize(
    ("mode", "status", "put_count", "delete_count"),
    [
        ("before-put", "pending_late_materialization", 0, 0),
        ("during-put", "cleaned", 1, 1),
    ],
)
def test_cleanup_tombstone_wins_restore_put_races(
    mode: str, status: str, put_count: int, delete_count: int
) -> None:
    class CleanupRaceArm(FakeArm):
        def __init__(self, markers: FakeMarkers) -> None:
            super().__init__()
            self.markers = markers

        def request(self, *args: Any, **kwargs: Any) -> Any:
            method, _resource_id = args
            if mode == "during-put" and method == "PUT":
                self.markers.records[("cleanup", "012345abcdef")] = (
                    self.markers.cleanup_raw
                )
            return super().request(*args, **kwargs)

    markers = FakeMarkers()
    fake = CleanupRaceArm(markers)
    engine = _engine(fake, markers)
    _configure_cleanup_race(markers, engine)
    cleanup_reads = 0

    def on_read(kind: str, rehearsal_id: str) -> None:
        nonlocal cleanup_reads
        if kind == "cleanup":
            cleanup_reads += 1
        if mode == "before-put" and cleanup_reads == 2:
            markers.records[(kind, rehearsal_id)] = markers.cleanup_raw

    markers.on_read = on_read
    outcome = engine.submit(_request())
    assert outcome.status == status
    assert outcome.cleanup_required is (status != "cleaned")
    assert len(_mutations(fake, "PUT")) == put_count
    assert len(_mutations(fake, "DELETE")) == delete_count


def test_retry_reconciles_the_exact_resource_without_a_second_restore_put() -> None:
    fake = FakeArm()
    markers = FakeMarkers()
    engine = _engine(fake, markers)
    first = engine.submit(_request())
    second = engine.submit(_request())
    assert first.status == second.status == "ready"
    assert first.target_resource_id == second.target_resource_id
    assert len(_mutations(fake, "PUT")) == 1
    assert len(markers.records) == 2


def test_cleanup_tombstone_prevents_a_retry_from_recreating_the_target() -> None:
    fake = FakeArm()
    markers = FakeMarkers()
    engine = _engine(fake, markers)
    ready = engine.submit(_request())
    assert ready.status == "ready"
    cleaned = engine.cleanup_intent("012345abcdef")
    assert cleaned.status == "cleaned"
    retry = engine.submit(_request())
    assert retry.status == "pending_late_materialization"
    assert retry.cleanup_required is True
    assert len(_mutations(fake, "PUT")) == 1
    assert len(markers.records) == 3


def test_cleanup_after_intent_before_submission_persists_dominant_tombstone() -> None:
    fake = FakeArm()
    markers = FakeMarkers()
    engine = _engine(fake, markers)
    intent = engine._build_intent(_request())
    markers.records[("intent", "012345abcdef")] = safe_submit._canonical(intent)
    assert engine.cleanup_intent("012345abcdef").status == "prepared_only"
    assert ("cleanup", "012345abcdef") in markers.records
    retry = engine.submit(_request())
    assert retry.cleanup_required is True
    assert _mutations(fake, "PUT") == []


@pytest.mark.parametrize("invalid_properties", [False, True])
def test_malformed_cleanup_marker_presence_cancels_submit(
    invalid_properties: bool,
) -> None:
    fake = FakeArm()
    markers = FakeMarkers()
    engine = _engine(fake, markers)
    key = ("cleanup", "012345abcdef")
    if invalid_properties:
        markers.invalid_present.add(key)
    else:
        markers.records[key] = b"not-json"
    outcome = engine.submit(_request())
    assert outcome.status == (
        "cleanup_marker_unproven"
        if invalid_properties
        else "pending_late_materialization"
    )
    assert outcome.cleanup_required is True
    assert _mutations(fake, "PUT") == []
    if invalid_properties:
        cleanup = engine.cleanup_intent("012345abcdef")
        assert cleanup.status == "cleanup_marker_unproven"
        assert cleanup.cleanup_required is True


@pytest.mark.parametrize("tags", [None, {}, {"rehearsal-id": "012345abcdef"}])
def test_late_materialization_without_complete_tags_is_isolated_and_deleted(
    tags: dict[str, str] | None,
) -> None:
    fake = FakeArm()
    fake.restore_put_unknown = True
    engine = _engine(fake)
    pending = engine.submit(_request())
    assert pending.status == "pending_late_materialization"
    fake.restore_put_unknown = False
    fake.materialize(
        pending.target_resource_id,
        tags=tags,
        source_server_resource_id=engine.config.source_server_id,
        public_access="Enabled",
        state="Creating",
        include_create_fields=False,
    )
    cleaned = engine.cleanup_intent("012345abcdef")
    assert cleaned.status == "cleaned"
    assert cleaned.target_observed is True
    assert len(_mutations(fake, "PATCH")) >= 1
    assert len(_mutations(fake, "DELETE")) == 1
    assert fake.target is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda target: target["properties"].update(
            sourceServerResourceId="/subscriptions/wrong/source"
        ),
        lambda target: target.setdefault("tags", {}).update(
            {"rehearsal-id": "ffffffffffff"}
        ),
        lambda target: target.update(type="Microsoft.Storage/storageAccounts"),
        lambda target: target["systemData"].update(createdBy="wrong-principal"),
    ],
)
def test_cleanup_refuses_a_wrong_resource_before_any_mutation(mutate: Any) -> None:
    fake = FakeArm()
    fake.restore_put_unknown = True
    engine = _engine(fake)
    pending = engine.submit(_request())
    fake.materialize(
        pending.target_resource_id,
        tags=None,
        source_server_resource_id=engine.config.source_server_id,
        public_access="Enabled",
    )
    assert fake.target is not None
    mutate(fake.target)
    with pytest.raises(safe_submit.SafeSubmitError):
        engine.cleanup_intent("012345abcdef")
    assert _mutations(fake, "PATCH") == []
    assert _mutations(fake, "DELETE") == []


def test_cleanup_without_a_durable_intent_is_fail_closed() -> None:
    fake = FakeArm()
    engine = _engine(fake)
    target_id = _target_id(engine, _request())
    fake.materialize(
        target_id,
        tags=None,
        source_server_resource_id=engine.config.source_server_id,
        public_access="Enabled",
    )
    with pytest.raises(safe_submit.SafeSubmitError, match="intent_record_missing"):
        engine.cleanup_intent("012345abcdef")
    assert _mutations(fake, "PATCH") == []
    assert _mutations(fake, "DELETE") == []


def test_isolation_refusal_still_attempts_guarded_deletion() -> None:
    fake = FakeArm()
    fake.restore_put_unknown = True
    engine = _engine(fake)
    pending = engine.submit(_request())
    fake.materialize(
        pending.target_resource_id,
        tags=None,
        source_server_resource_id=engine.config.source_server_id,
        public_access="Enabled",
        include_create_fields=False,
    )
    fake.patch_status = 403
    cleaned = engine.cleanup_intent("012345abcdef")
    assert cleaned.status == "cleaned"
    assert len(_mutations(fake, "PATCH")) == 1
    assert len(_mutations(fake, "DELETE")) == 1


def test_submit_enters_cleanup_immediately_on_definite_isolation_refusal() -> None:
    fake = FakeArm()
    fake.restore_public_access = "Enabled"
    fake.patch_status = 403
    engine = _engine(fake, restore_poll_attempts=3)
    outcome = engine.submit(_request())
    assert outcome.status == "cleaned"
    assert len(_mutations(fake, "PATCH")) == 2
    assert len(_mutations(fake, "DELETE")) == 1


def test_unknown_submission_record_never_claims_prepared_only() -> None:
    fake = FakeArm()
    markers = FakeMarkers()
    engine = _engine(fake, markers)
    assert engine.submit(_request()).status == "ready"
    fake.target = None
    markers.unknown_reads[("submission", "012345abcdef")] = 1
    outcome = engine.cleanup_intent("012345abcdef")
    assert outcome.cleanup_required is True
    assert ("cleanup", "012345abcdef") in markers.records
    assert _mutations(fake, "DELETE") == []


def test_unproven_intent_persistence_blocks_restore_submission() -> None:
    fake = FakeArm()
    markers = FakeMarkers()
    markers.fail_create_before.add(("intent", "012345abcdef"))
    engine = _engine(fake, markers)
    with pytest.raises(
        safe_submit.SafeSubmitError, match="intent_persistence_unproven"
    ):
        engine.submit(_request())
    assert _mutations(fake, "PUT") == []
