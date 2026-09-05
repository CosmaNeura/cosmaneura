from __future__ import annotations

import http.client
import importlib.util
import inspect
import io
import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode
from contextlib import nullcontext
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
AUTHENTICATION_SCRIPT = ROOT / "scripts/authenticate_staging_server_scope_receipt.py"
ENVELOPE_POLICY = ROOT / "contracts/staging-rehearsal/v1/restore-envelope-policy.json"
SAFE_POLICY = ROOT / "contracts/staging-rehearsal/v1/restore-safe-submit-policy.json"
RUNTIME_POLICY = (
    ROOT / "contracts/staging-rehearsal/v1/restore-runtime-binding-policy.json"
)
PROVISION_JOB_CLIENT_ID = "00000000-0000-4000-8000-000000000001"
PROVISION_JOB_PRINCIPAL_ID = "00000000-0000-4000-8000-000000000002"
CLEANUP_JOB_CLIENT_ID = "00000000-0000-4000-8000-000000000003"
CLEANUP_JOB_PRINCIPAL_ID = "00000000-0000-4000-8000-000000000004"
IDENTITY_ENDPOINT = "http://169.254.169.254:42342/msi/token"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("staging_restore_safe_submit", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(ROOT))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


safe_submit = _load_module()


def _load_authentication_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "staging_server_scope_authentication_for_safe_submit",
        AUTHENTICATION_SCRIPT,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(ROOT))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


server_authentication = _load_authentication_module()
TEST_NOW = datetime(2026, 9, 3, 12, 1, tzinfo=UTC)


def _config(**changes: Any) -> Any:
    envelope = json.loads(ENVELOPE_POLICY.read_text())
    policy = json.loads(SAFE_POLICY.read_text())
    config = safe_submit.config_from_policies(envelope, policy, executable=False)
    defaults = {
        "blob_marker_store_implemented": True,
        "blob_marker_provider_fidelity_proven": True,
        "private_runner_binding_implemented": True,
        "trusted_evidence_binding_implemented": True,
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


def _binding(**changes: Any) -> Any:
    observed = datetime(2026, 9, 3, 12, tzinfo=UTC)
    policy = json.loads(SAFE_POLICY.read_text())["evidence_binding"]
    values = {
        "schema_version": 1,
        "binding_policy_id": "cosmaneura-staging-restore-evidence-binding-v1",
        "evidence_manifest_policy_id": "cosmaneura-staging-evidence-manifest-v2",
        "evidence_manifest_policy_sha256": policy["manifest_policy_sha256"],
        "manifest_id": "rehearsal-20260903",
        "manifest_file_sha256": f"sha256:{'1' * 64}",
        "bundle_index_sha256": f"sha256:{'2' * 64}",
        "evidence_set_sha256": f"sha256:{'3' * 64}",
        "source_sha": "4" * 40,
        "candidate_sha": "5" * 40,
        "source_server_resource_id": (
            "/subscriptions/bab2a709-1fcb-4c87-a9fc-802f7477ec13/"
            "resourceGroups/cosmaneura-staging/providers/"
            "Microsoft.DBforPostgreSQL/flexibleServers/"
            "cosmaneura-staging-postgres"
        ),
        "source_database_name": "staging",
        "approved_restore_time": observed,
        "observed_at": observed,
        "expires_at": observed + timedelta(minutes=15),
        "candidate_approval_pr_number": 230,
        "candidate_protected_master_sha": "a" * 40,
        "candidate_schema_revision": "89c31f0ae275",
        "candidate_receipt_sha256": f"sha256:{'6' * 64}",
        "inventory_receipt_sha256": f"sha256:{'7' * 64}",
        "quiescence_receipt_sha256": f"sha256:{'8' * 64}",
        "server_scope_receipt_sha256": f"sha256:{'9' * 64}",
        "repository": "CosmaNeura/cosmaneura",
        "protected_ref": "refs/heads/master",
        "workflow_path": ".github/workflows/staging_restore_rehearsal.yml",
        "workflow_sha": "a" * 40,
        "run_id": "901",
        "run_attempt": "1",
        "event_name": "workflow_dispatch",
        "signer_issuer": "https://token.actions.githubusercontent.com",
        "signer_subject": (
            "repo:CosmaNeura/cosmaneura:environment:staging-restore-provision"
        ),
        "attestation_receipt_sha256": f"sha256:{'b' * 64}",
        "provider_receipt_sha256": f"sha256:{'c' * 64}",
        "server_scope_authentication_receipt_sha256": f"sha256:{'d' * 64}",
    }
    values.update(changes)
    # Deliberately breach the module-internal seal for fake transport tests.
    # Production code has no raw-value constructor; a later composite verifier
    # will be the only supported minter after all three proofs exist.
    binding = object.__new__(safe_submit.RestoreEvidenceBinding)
    for name, value in values.items():
        object.__setattr__(binding, name, value)
    object.__setattr__(
        binding,
        "_binding_seal",
        safe_submit._RESTORE_EVIDENCE_BINDING_SEAL,
    )
    return binding


def _rehearsal_id(binding: Any) -> str:
    policy_id = json.loads(SAFE_POLICY.read_text())["policy_id"]
    evidence = safe_submit.SafeRestoreSubmitter._binding_record(binding)
    payload = {
        "schema_version": 1,
        "safe_submit_policy_id": policy_id,
        "evidence_binding": evidence,
    }
    return safe_submit._sha256(safe_submit._canonical(payload))[:12]


def _request(*, binding: Any | None = None, **changes: Any) -> Any:
    evidence = binding or _binding()
    observed = evidence.observed_at
    values = {
        "rehearsal_id": _rehearsal_id(evidence),
        "evidence_binding": evidence,
        "cleanup_not_after": observed + timedelta(hours=6),
        "repository": evidence.repository,
        "workflow_sha": evidence.workflow_sha,
        "run_id": evidence.run_id,
        "run_attempt": evidence.run_attempt,
    }
    values.update(changes)
    return safe_submit.RestoreRequest(
        **values,
    )


REHEARSAL_ID = _request().rehearsal_id


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
        self.target_get_unknowns = 0
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
            if self.target_get_unknowns:
                self.target_get_unknowns -= 1
                raise safe_submit.ArmOutcomeUnknown
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

    def get_all(self, name: str) -> list[str]:
        return [value for key, value in self.items() if key.lower() == name.lower()]


class HeaderPairs:
    def __init__(self, pairs: list[tuple[str, str]]) -> None:
        self.pairs = pairs

    def items(self) -> list[tuple[str, str]]:
        return self.pairs

    def get_all(self, name: str) -> list[str]:
        return [value for key, value in self.pairs if key.lower() == name.lower()]


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
        response_headers = dict(headers or {})
        if not any(key.lower() == "content-length" for key in response_headers):
            response_headers["Content-Length"] = str(len(body))
        self.headers: Any = FakeHeaders(response_headers, fail=failure == "headers")
        self.url = url
        self.failure = failure
        self.offset = 0

    @property
    def status(self) -> int:
        if self.failure == "status":
            raise RuntimeError("status unavailable")
        return self._status

    def geturl(self) -> str:
        return self.url

    def read(self, maximum: int) -> bytes:
        if self.failure == "read":
            raise http.client.IncompleteRead(b"partial", 10)
        chunk = self.body[self.offset : self.offset + maximum]
        self.offset += len(chunk)
        return chunk

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
            if isinstance(response, urllib.error.HTTPError) and not response.url:
                response.url = request.full_url
            raise response
        if not response.url:
            response.url = request.full_url
        return response


def _token_source(
    *, client_id: str = PROVISION_JOB_CLIENT_ID, opener: Any | None = None
) -> Any:
    source = safe_submit.ContainerAppsManagedIdentityTokenSource(
        client_id=client_id,
        identity_endpoint=IDENTITY_ENDPOINT,
        identity_header="identity-secret",
        service_api_version="2019-08-01",
        maximum_response_bytes=65_536,
        expiry_skew_seconds=90,
        maximum_lifetime_seconds=86_400,
        maximum_timeout_seconds=15,
        opener=opener or FakeOpener([]),
        clock=lambda: 1_000,
    )
    source._tokens = {
        safe_submit.ARM_OAUTH_RESOURCE: ("arm-token", 2_000),
        safe_submit.BLOB_OAUTH_RESOURCE: ("blob-token", 2_000),
    }
    return source


def _identity_response(
    *,
    client_id: str,
    resource: str,
    document_changes: Mapping[str, Any] | None = None,
) -> Any:
    document = {
        "access_token": f"token-{resource}",
        "expires_on": "2000",
        "resource": resource,
        "client_id": client_id,
        "token_type": "Bearer",
    }
    document.update(document_changes or {})
    raw = json.dumps(document, separators=(",", ":")).encode()
    return FakeHttpResponse(200, raw, {"Content-Type": "application/json"})


def _runtime_transport_policy() -> dict[str, Any]:
    config = _config(
        provision_client_id=PROVISION_JOB_CLIENT_ID,
        provision_principal_id=PROVISION_JOB_PRINCIPAL_ID,
    )
    policy = json.loads(RUNTIME_POLICY.read_text())
    jobs = policy["identities"]["jobs"]
    resource_prefix = (
        "/subscriptions/00000000-0000-4000-8000-000000000000/"
        "resourceGroups/runtime/providers/Microsoft.ManagedIdentity/"
        "userAssignedIdentities/"
    )
    for action, client_id, principal_id in (
        ("provision", PROVISION_JOB_CLIENT_ID, PROVISION_JOB_PRINCIPAL_ID),
        ("cleanup", CLEANUP_JOB_CLIENT_ID, CLEANUP_JOB_PRINCIPAL_ID),
    ):
        jobs[action].update(
            resource_id=f"{resource_prefix}{action}",
            client_id=client_id,
            principal_id=principal_id,
        )
    marker = policy["private_control_marker_store"]
    marker.update(
        policy_id=config.policy_id,
        account_resource_id=config.marker_account_resource_id,
        account_host=config.marker_account_host,
        oauth_resource=config.marker_oauth_resource,
        container_names=config.marker_containers,
        allowed_job_identity_resource_ids=[
            jobs[action]["resource_id"] for action in ("provision", "cleanup")
        ],
    )
    return policy


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
        def _access_token(self, _timeout_seconds: int) -> str:
            return "test-storage-token"

    return TestStore(
        _config(**changes),
        _token_source(),
        action="provision",
        client_id=PROVISION_JOB_CLIENT_ID,
        opener=opener,
        sleeper=lambda _: None,
        clock=lambda: TEST_NOW,
        monotonic=lambda: 1.0,
        deadline=lambda _: nullcontext(),
    )


def _arm_transport(
    opener: FakeOpener, *, action: str = "provision", **changes: Any
) -> Any:
    client_id = (
        PROVISION_JOB_CLIENT_ID if action == "provision" else CLEANUP_JOB_CLIENT_ID
    )
    config = _config(
        provision_client_id=PROVISION_JOB_CLIENT_ID,
        provision_principal_id=PROVISION_JOB_PRINCIPAL_ID,
        **changes,
    )
    return safe_submit.AzureArmTransport(
        config,
        _token_source(client_id=client_id),
        action=action,
        client_id=client_id,
        opener=opener,
        monotonic=lambda: 1.0,
        deadline=lambda _: nullcontext(),
    )


def _http_error(status: int) -> urllib.error.HTTPError:
    headers = Message()
    headers["Content-Length"] = "0"
    return urllib.error.HTTPError("", status, "bounded", headers, io.BytesIO(b""))


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
    method: str, failure: str, expected: str
) -> None:
    body = (
        b"{"
        if failure == "malformed"
        else b"x" * 1_048_577
        if failure == "oversized"
        else b"{}"
    )
    response: Any = (
        _http_error(409)
        if failure == "http-error"
        else FakeHttpResponse(
            202,
            body,
            {"Content-Type": "application/json"},
            failure="read" if failure == "incomplete" else failure,
        )
    )
    opener = FakeOpener([response])
    config = _config(
        provision_client_id=PROVISION_JOB_CLIENT_ID,
        provision_principal_id=PROVISION_JOB_PRINCIPAL_ID,
    )
    transport = safe_submit.AzureArmTransport(
        config,
        _token_source(),
        action="provision",
        client_id=PROVISION_JOB_CLIENT_ID,
        opener=opener,
        monotonic=lambda: 1.0,
        deadline=lambda _: nullcontext(),
    )
    target_id = (
        f"{config.target_group_id}/providers/"
        "Microsoft.DBforPostgreSQL/flexibleServers/cnr-test-target"
    )

    def make_request() -> Any:
        return transport.request(
            method,
            target_id,
            api_version="2024-08-01",
            body={"properties": {}} if method == "PUT" else None,
            headers={"If-None-Match": "*"} if method == "PUT" else None,
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
    assert len(opener.requests) == 1
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
    assert store.create_if_absent("intent", REHEARSAL_ID, raw) == raw
    assert [item[0].get_method() for item in opener.requests] == ["GET", "PUT", "GET"]
    request, timeout = opener.requests[1]
    assert request.full_url == (
        "https://restorecontrol.blob.core.windows.net/"
        f"restore-intents-v4/{REHEARSAL_ID}.json"
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
    assert _blob_store(opener).create_if_absent("intent", REHEARSAL_ID, raw) == raw
    assert [item[0].get_method() for item in opener.requests].count("PUT") == 1


@pytest.mark.parametrize("failure", ["headers", "read", "exit"])
def test_blob_get_post_status_failures_preserve_present_signal(failure: str) -> None:
    response = FakeHttpResponse(200, failure=failure)
    with pytest.raises(safe_submit.MarkerPresentInvalid):
        _blob_store(FakeOpener([response])).read("cleanup", REHEARSAL_ID)


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
        store.read("intent", REHEARSAL_ID)


def test_blob_read_rejects_short_retention_window() -> None:
    raw = b'{"kind":"intent"}'
    too_short = format_datetime(TEST_NOW + timedelta(days=89), usegmt=True)
    headers = _marker_headers(raw, **{"x-ms-immutability-policy-until-date": too_short})
    with pytest.raises(safe_submit.MarkerPresentInvalid):
        _blob_store(FakeOpener([FakeHttpResponse(200, raw, headers)])).read(
            "intent", REHEARSAL_ID
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
    assert _blob_store(accepted).create_if_absent("intent", REHEARSAL_ID, raw) == raw
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
        _blob_store(absent).create_if_absent("intent", REHEARSAL_ID, raw)
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
        _blob_store(replay).create_if_absent("intent", REHEARSAL_ID, expected)
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
        _blob_store(conflict).create_if_absent("intent", REHEARSAL_ID, expected)

    uncertain = FakeOpener(
        [
            TimeoutError(),
            _http_error(404),
        ]
    )
    with pytest.raises(safe_submit.MarkerOutcomeUnknown):
        _blob_store(uncertain).read("intent", REHEARSAL_ID)


def test_blob_redirect_and_oversize_are_bounded_refusals() -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    default_opener = safe_submit._hardened_opener(tls_context=context)
    default_store = safe_submit.AzureBlobMarkerStore(
        _config(),
        _token_source(),
        action="provision",
        client_id=PROVISION_JOB_CLIENT_ID,
        opener=default_opener,
        monotonic=lambda: 1.0,
        deadline=lambda _: nullcontext(),
    )
    assert any(
        isinstance(handler, safe_submit._RefuseRedirects)
        for handler in default_store.opener.handlers
    )
    assert not urllib.request.getproxies() or any(
        isinstance(handler, urllib.request.ProxyHandler)
        and not getattr(handler, "proxies", None)
        for handler in default_store.opener.handlers
    )
    redirected = FakeHttpResponse(307, url="https://evil.example/marker")
    with pytest.raises(safe_submit.SafeSubmitError, match="blob_redirect_refused"):
        _blob_store(FakeOpener([redirected])).read("intent", REHEARSAL_ID)
    oversized = b"x" * 65_537
    with pytest.raises(safe_submit.MarkerPresentInvalid, match="marker_body_too_large"):
        _blob_store(FakeOpener([FakeHttpResponse(200, oversized)])).read(
            "intent", REHEARSAL_ID
        )


def test_blob_list_is_paginated_and_bounded() -> None:
    first = f"<EnumerationResults><Blobs><Blob><Name>{REHEARSAL_ID}.json</Name></Blob></Blobs><NextMarker>next page</NextMarker></EnumerationResults>".encode()
    second = b"<EnumerationResults><Blobs><Blob><Name>fedcba543210.json</Name></Blob></Blobs><NextMarker /></EnumerationResults>"
    list_headers = {"content-type": "application/xml", "x-ms-version": "2021-12-02"}
    opener = FakeOpener(
        [
            FakeHttpResponse(200, first, list_headers),
            FakeHttpResponse(200, second, list_headers),
        ]
    )
    assert _blob_store(opener).list_intent_ids() == (
        REHEARSAL_ID,
        "fedcba543210",
    )
    assert "marker=next+page" in opener.requests[1][0].full_url
    overflow = FakeOpener([FakeHttpResponse(200, first, list_headers)])
    with pytest.raises(safe_submit.SafeSubmitError, match="marker_list_bound_exceeded"):
        _blob_store(overflow, marker_list_max_pages=1).list_intent_ids()
    two = f"<EnumerationResults><Blobs><Blob><Name>{REHEARSAL_ID}.json</Name></Blob><Blob><Name>fedcba543210.json</Name></Blob></Blobs><NextMarker /></EnumerationResults>".encode()
    with pytest.raises(safe_submit.SafeSubmitError, match="marker_list_bound_exceeded"):
        _blob_store(
            FakeOpener([FakeHttpResponse(200, two, list_headers)]),
            marker_list_max_records=1,
        ).list_intent_ids()


def test_blob_oauth_audience_and_dormant_host_are_exact() -> None:
    token_body = json.dumps(
        {
            "access_token": "token",
            "expires_on": "2000",
            "resource": safe_submit.BLOB_OAUTH_RESOURCE,
            "client_id": PROVISION_JOB_CLIENT_ID,
            "token_type": "Bearer",
        },
        separators=(",", ":"),
    ).encode()
    identity_opener = FakeOpener(
        [
            FakeHttpResponse(
                200,
                token_body,
                {"Content-Type": "application/json"},
            )
        ]
    )
    source = safe_submit.ContainerAppsManagedIdentityTokenSource(
        client_id=PROVISION_JOB_CLIENT_ID,
        identity_endpoint=IDENTITY_ENDPOINT,
        identity_header="identity-secret",
        service_api_version="2019-08-01",
        maximum_response_bytes=65_536,
        expiry_skew_seconds=90,
        maximum_lifetime_seconds=86_400,
        maximum_timeout_seconds=15,
        opener=identity_opener,
        clock=lambda: 1_000,
    )
    store = safe_submit.AzureBlobMarkerStore(
        _config(),
        source,
        action="provision",
        client_id=PROVISION_JOB_CLIENT_ID,
        opener=FakeOpener([]),
        monotonic=lambda: 1.0,
        deadline=lambda _: nullcontext(),
    )
    assert store._access_token(15) == "token"
    request = identity_opener.requests[0][0]
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
    assert query == {
        "api-version": ["2019-08-01"],
        "resource": ["https://storage.azure.com/"],
        "client_id": [PROVISION_JOB_CLIENT_ID],
    }
    headers = {key.lower(): value for key, value in request.header_items()}
    assert headers["x-identity-header"] == "identity-secret"
    assert headers["metadata"] == "true"
    config = safe_submit.config_from_policies(
        json.loads(ENVELOPE_POLICY.read_text()),
        json.loads(SAFE_POLICY.read_text()),
        executable=False,
    )
    with pytest.raises(
        safe_submit.SafeSubmitError, match="blob_store_configuration_invalid"
    ):
        safe_submit.AzureBlobMarkerStore(
            config,
            source,
            action="provision",
            client_id=PROVISION_JOB_CLIENT_ID,
            opener=FakeOpener([]),
        )
    with pytest.raises(
        safe_submit.SafeSubmitError, match="blob_store_configuration_invalid"
    ):
        safe_submit.AzureBlobMarkerStore(
            _config(marker_account_host="otheraccount.blob.core.windows.net"),
            source,
            action="provision",
            client_id=PROVISION_JOB_CLIENT_ID,
            opener=FakeOpener([]),
        )


@pytest.mark.parametrize(
    ("action", "client_id"),
    [
        ("provision", PROVISION_JOB_CLIENT_ID),
        ("cleanup", CLEANUP_JOB_CLIENT_ID),
    ],
)
def test_policy_bound_runtime_selects_exact_job_uami_for_arm_and_blob(
    monkeypatch: pytest.MonkeyPatch, action: str, client_id: str
) -> None:
    policy = _runtime_transport_policy()
    validation_calls: list[bool] = []

    def validate(document: Any, *, executable: bool) -> Any:
        assert document is policy
        validation_calls.append(executable)
        return document

    monkeypatch.setattr(
        safe_submit.runtime_binding, "validate_runtime_binding_policy", validate
    )
    identity_opener = FakeOpener(
        [
            _identity_response(
                client_id=client_id, resource=safe_submit.ARM_OAUTH_RESOURCE
            ),
            _identity_response(
                client_id=client_id, resource=safe_submit.BLOB_OAUTH_RESOURCE
            ),
        ]
    )
    arm_opener = FakeOpener([FakeHttpResponse(404)])
    blob_opener = FakeOpener([FakeHttpResponse(404)])
    deadlines: list[int] = []

    def deadline(seconds: int) -> Any:
        deadlines.append(seconds)
        return nullcontext()

    config = _config(
        provision_client_id=PROVISION_JOB_CLIENT_ID,
        provision_principal_id=PROVISION_JOB_PRINCIPAL_ID,
    )
    runtime = safe_submit.build_policy_bound_runtime(
        config=config,
        runtime_policy=policy,
        action=action,
        environment={
            "IDENTITY_ENDPOINT": IDENTITY_ENDPOINT,
            "IDENTITY_HEADER": "identity-secret",
            "HTTP_PROXY": "http://ambient.invalid",
            "SSL_CERT_FILE": "/ambient/ca.pem",
        },
        identity_opener=identity_opener,
        arm_opener=arm_opener,
        blob_opener=blob_opener,
        epoch_clock=lambda: 1_000,
        monotonic=lambda: 1.0,
        deadline=deadline,
        sleeper=lambda _: None,
        trusted_clock=lambda: TEST_NOW,
    )
    with pytest.raises(safe_submit.SafeSubmitError, match="marker_list_refused"):
        runtime.marker_store.list_intent_ids()
    if action == "cleanup":
        with pytest.raises(safe_submit.SafeSubmitError, match="marker_write_refused"):
            runtime.marker_store.create_if_absent("intent", REHEARSAL_ID, b"{}")
    target_id = (
        f"{config.target_group_id}/providers/"
        "Microsoft.DBforPostgreSQL/flexibleServers/cnr-policy-bound"
    )
    assert (
        runtime.arm_transport.request(
            "GET",
            target_id,
            api_version=config.postgres_api_version,
            timeout_seconds=config.request_timeout_seconds,
        ).status
        == 404
    )
    assert runtime.marker_store.read("cleanup", REHEARSAL_ID) is None
    assert validation_calls == [True]
    assert deadlines == [30, 30]
    assert runtime.action == action
    assert runtime.config is config
    assert len(identity_opener.requests) == 2
    queries = [
        urllib.parse.parse_qs(urllib.parse.urlsplit(item[0].full_url).query)
        for item in identity_opener.requests
    ]
    assert [query["client_id"] for query in queries] == [[client_id], [client_id]]
    assert [query["resource"] for query in queries] == [
        [safe_submit.ARM_OAUTH_RESOURCE],
        [safe_submit.BLOB_OAUTH_RESOURCE],
    ]
    identity_headers = [
        {key.lower(): value for key, value in item[0].header_items()}
        for item in identity_opener.requests
    ]
    assert all(
        headers["x-identity-header"] == "identity-secret"
        for headers in identity_headers
    )
    arm_request = arm_opener.requests[0][0]
    arm_headers = {key.lower(): value for key, value in arm_request.header_items()}
    assert arm_request.full_url == (
        f"{safe_submit.MANAGEMENT_ROOT}{target_id}?api-version=2024-08-01"
    )
    assert arm_headers["authorization"] == (
        f"Bearer token-{safe_submit.ARM_OAUTH_RESOURCE}"
    )
    blob_headers = {
        key.lower(): value for key, value in blob_opener.requests[0][0].header_items()
    }
    assert blob_headers["authorization"] == (
        f"Bearer token-{safe_submit.BLOB_OAUTH_RESOURCE}"
    )


def test_policy_bound_runtime_refuses_dormant_or_cross_identity_policy_before_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = json.loads(RUNTIME_POLICY.read_text())
    with pytest.raises(
        safe_submit.SafeSubmitError, match="safe_submit_runtime_policy_invalid"
    ):
        safe_submit.build_policy_bound_runtime(
            config=_config(),
            runtime_policy=canonical,
            action="provision",
            environment={},
            identity_opener=FakeOpener([]),
            arm_opener=FakeOpener([]),
            blob_opener=FakeOpener([]),
        )

    configured = _runtime_transport_policy()
    monkeypatch.setattr(
        safe_submit.runtime_binding,
        "validate_runtime_binding_policy",
        lambda *_args, **_kwargs: configured,
    )
    no_io = FakeOpener([])
    with pytest.raises(
        safe_submit.SafeSubmitError, match="safe_submit_runtime_policy_mismatch"
    ):
        safe_submit.build_policy_bound_runtime(
            config=_config(
                provision_client_id="00000000-0000-4000-8000-000000000099",
                provision_principal_id=PROVISION_JOB_PRINCIPAL_ID,
            ),
            runtime_policy=configured,
            action="provision",
            environment={
                "IDENTITY_ENDPOINT": IDENTITY_ENDPOINT,
                "IDENTITY_HEADER": "identity-secret",
            },
            identity_opener=no_io,
            arm_opener=no_io,
            blob_opener=no_io,
        )
    assert no_io.requests == []


@pytest.mark.parametrize(
    ("endpoint", "header"),
    [
        ("https://169.254.169.254:42342/msi/token", "secret"),
        ("http://example.com:42342/msi/token", "secret"),
        ("http://169.254.169.254:42342/other", "secret"),
        ("http://169.254.169.254:42342/MSI/token", "secret"),
        ("http://169.254.169.254:42342/msi/token/", "secret"),
        (IDENTITY_ENDPOINT, ""),
    ],
)
def test_managed_identity_rejects_nonlocal_endpoint_or_missing_secret(
    endpoint: str, header: str
) -> None:
    opener = FakeOpener([])
    with pytest.raises(
        safe_submit.SafeSubmitError, match="managed_identity_configuration_invalid"
    ):
        safe_submit.ContainerAppsManagedIdentityTokenSource(
            client_id=PROVISION_JOB_CLIENT_ID,
            identity_endpoint=endpoint,
            identity_header=header,
            service_api_version="2019-08-01",
            maximum_response_bytes=65_536,
            expiry_skew_seconds=90,
            maximum_lifetime_seconds=86_400,
            maximum_timeout_seconds=15,
            opener=opener,
            clock=lambda: 1_000,
        )
    assert opener.requests == []


@pytest.mark.parametrize(
    "changes",
    [
        {"client_id": CLEANUP_JOB_CLIENT_ID},
        {"resource": safe_submit.BLOB_OAUTH_RESOURCE},
        {"token_type": "Basic"},
        {"expires_on": "1090"},
        {"expires_on": "87401"},
    ],
)
def test_managed_identity_response_is_bound_and_time_limited(
    changes: dict[str, str],
) -> None:
    opener = FakeOpener(
        [
            _identity_response(
                client_id=PROVISION_JOB_CLIENT_ID,
                resource=safe_submit.ARM_OAUTH_RESOURCE,
                document_changes=changes,
            )
        ]
    )
    source = _token_source(opener=opener)
    source._tokens = {}
    with pytest.raises(
        safe_submit.SafeSubmitError, match="managed_identity_response_invalid"
    ):
        source.get_token(
            client_id=PROVISION_JOB_CLIENT_ID,
            resource=safe_submit.ARM_OAUTH_RESOURCE,
            timeout_seconds=15,
        )
    assert len(opener.requests) == 1


def test_managed_identity_rejects_duplicate_json_and_ambiguous_framing() -> None:
    duplicate = b'{"access_token":"one","access_token":"two","expires_on":"2000"}'
    responses = [
        FakeHttpResponse(200, duplicate, {"Content-Type": "application/json"}),
        FakeHttpResponse(200, b"{}", {"Content-Type": "application/json"}),
    ]
    responses[1].headers = HeaderPairs(
        [
            ("Content-Type", "application/json"),
            ("Content-Length", "2"),
            ("Content-Length", "2"),
        ]
    )
    for response in responses:
        source = _token_source(opener=FakeOpener([response]))
        source._tokens = {}
        with pytest.raises(safe_submit.SafeSubmitError):
            source.get_token(
                client_id=PROVISION_JOB_CLIENT_ID,
                resource=safe_submit.ARM_OAUTH_RESOURCE,
                timeout_seconds=15,
            )


@pytest.mark.parametrize(
    ("pairs", "body", "url", "expected"),
    [
        ([("Content-Type", "application/json")], b"{}", "", "arm_response_invalid"),
        (
            [("Content-Type", "application/json"), ("Content-Length", "02")],
            b"{}",
            "",
            "arm_response_invalid",
        ),
        (
            [
                ("Content-Type", "application/json"),
                ("Content-Length", "2"),
                ("Transfer-Encoding", "chunked"),
            ],
            b"{}",
            "",
            "arm_response_invalid",
        ),
        (
            [
                ("Content-Type", "application/json"),
                ("Content-Length", "2"),
                ("Content-Range", "bytes 0-1/2"),
            ],
            b"{}",
            "",
            "arm_response_invalid",
        ),
        (
            [
                ("Content-Type", "application/json"),
                ("Content-Length", "2"),
                ("Content-Encoding", "gzip"),
            ],
            b"{}",
            "",
            "arm_response_invalid",
        ),
        (
            [("Content-Type", "application/json"), ("Content-Length", "3")],
            b"{}",
            "",
            "arm_response_invalid",
        ),
        (
            [("Content-Type", "application/json"), ("Content-Length", "2")],
            b"{}",
            "https://redirect.invalid",
            "arm_redirect_refused",
        ),
    ],
)
def test_arm_get_rejects_ambiguous_framing_and_wrong_final_url(
    pairs: list[tuple[str, str]], body: bytes, url: str, expected: str
) -> None:
    response = FakeHttpResponse(200, body, url=url)
    response.headers = HeaderPairs(pairs)
    opener = FakeOpener([response])
    transport = _arm_transport(opener)
    target_id = (
        f"{transport.config.target_group_id}/providers/"
        "Microsoft.DBforPostgreSQL/flexibleServers/cnr-framing"
    )
    with pytest.raises(safe_submit.SafeSubmitError, match=expected):
        transport.request(
            "GET",
            target_id,
            api_version="2024-08-01",
            timeout_seconds=30,
        )
    assert len(opener.requests) == 1


@pytest.mark.parametrize(
    ("extra", "length"),
    [
        ([("Content-Length", "2")], "2"),
        ([("Transfer-Encoding", "chunked")], "2"),
        ([("Content-Range", "bytes 0-1/2")], "2"),
        ([("Content-Encoding", "gzip")], "2"),
        ([], "3"),
    ],
)
def test_marker_get_preserves_presence_on_ambiguous_framing(
    extra: list[tuple[str, str]], length: str
) -> None:
    raw = b"{}"
    response = FakeHttpResponse(200, raw)
    response.headers = HeaderPairs(
        [("Content-Type", "application/json"), ("Content-Length", length), *extra]
    )
    opener = FakeOpener([response])
    with pytest.raises(safe_submit.MarkerPresentInvalid):
        _blob_store(opener).read("cleanup", REHEARSAL_ID)
    assert len(opener.requests) == 1


def test_mutation_auth_failure_does_not_dispatch_and_service_failure_is_unknown() -> (
    None
):
    identity = FakeOpener([TimeoutError("secret must not escape")])
    source = _token_source(opener=identity)
    source._tokens = {}
    service = FakeOpener([])
    config = _config(
        provision_client_id=PROVISION_JOB_CLIENT_ID,
        provision_principal_id=PROVISION_JOB_PRINCIPAL_ID,
    )
    transport = safe_submit.AzureArmTransport(
        config,
        source,
        action="provision",
        client_id=PROVISION_JOB_CLIENT_ID,
        opener=service,
        monotonic=lambda: 1.0,
        deadline=lambda _: nullcontext(),
    )
    target_id = (
        f"{config.target_group_id}/providers/"
        "Microsoft.DBforPostgreSQL/flexibleServers/cnr-unknown"
    )
    with pytest.raises(safe_submit.SafeSubmitError) as raised:
        transport.request(
            "PUT",
            target_id,
            api_version="2024-08-01",
            body={},
            headers={"If-None-Match": "*"},
            timeout_seconds=30,
        )
    assert "secret" not in str(raised.value)
    assert len(identity.requests) == 1 and service.requests == []

    service.responses.append(TimeoutError("ambiguous provider failure"))
    transport = _arm_transport(service)
    with pytest.raises(safe_submit.ArmOutcomeUnknown):
        transport.request(
            "PUT",
            target_id,
            api_version="2024-08-01",
            body={},
            headers={"If-None-Match": "*"},
            timeout_seconds=30,
        )
    assert len(service.requests) == 1


def test_arm_rejects_header_override_and_action_confusion_before_io() -> None:
    opener = FakeOpener([])
    provision = _arm_transport(opener)
    target_id = (
        f"{provision.config.target_group_id}/providers/"
        "Microsoft.DBforPostgreSQL/flexibleServers/cnr-method"
    )
    with pytest.raises(safe_submit.SafeSubmitError, match="arm_request_invalid"):
        provision.request(
            "GET",
            target_id,
            api_version="2024-08-01",
            headers={"Authorization": "Bearer ambient"},
            timeout_seconds=30,
        )
    with pytest.raises(safe_submit.SafeSubmitError, match="arm_request_invalid"):
        provision.request(
            "DELETE",
            target_id,
            api_version="2024-08-01",
            timeout_seconds=30,
        )
    cleanup = _arm_transport(opener, action="cleanup")
    with pytest.raises(safe_submit.SafeSubmitError, match="arm_request_invalid"):
        cleanup.request(
            "PUT",
            target_id,
            api_version="2024-08-01",
            body={},
            headers={"If-None-Match": "*"},
            timeout_seconds=30,
        )
    assert opener.requests == []


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
    assert policy["schema_version"] == 4
    assert policy["marker_store"]["kind"] == "azure-blob-immutable-create-only-v1"
    assert policy["marker_store"]["rbac"]["create_read_data_actions"].startswith(
        "CONFIGURE_EXACT_"
    )
    assert (
        policy["marker_store"]["rbac"]["provision_cleanup_tombstone_assignment_id"]
        == "CONFIGURE_EXACT_RESTORE_PROVISION_CLEANUP_TOMBSTONE_ROLE_ASSIGNMENT_ID"
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
    assert policy["operation_authority"] == {
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
    }
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
    engine = _engine(
        fake,
        markers,
        trusted_evidence_binding_implemented=False,
    )
    with pytest.raises(
        safe_submit.SafeSubmitError, match="trusted_evidence_binding_unproven"
    ):
        engine.submit(_request())
    assert markers.calls == []
    assert fake.calls == []


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
    assert markers.calls[:5] == [
        ("GET", "intent", REHEARSAL_ID),
        ("PUT", "intent", REHEARSAL_ID),
        ("GET", "cleanup", REHEARSAL_ID),
        ("GET", "submission", REHEARSAL_ID),
        ("PUT", "submission", REHEARSAL_ID),
    ]
    restore = writes[0]
    assert restore["api_version"] == "2024-08-01"
    assert restore["headers"] == {"If-None-Match": "*"}
    assert restore["timeout_seconds"] == 30
    assert restore["body"]["properties"]["network"] == {
        "publicNetworkAccess": "Disabled"
    }
    assert set(markers.records) == {
        ("intent", REHEARSAL_ID),
        ("submission", REHEARSAL_ID),
    }
    intent = json.loads(markers.records[("intent", REHEARSAL_ID)])
    submission = json.loads(markers.records[("submission", REHEARSAL_ID)])
    assert intent["schema_version"] == 4
    assert intent["restore_time"] == "2026-09-03T12:00:00Z"
    assert intent["created_at"] == intent["evidence_binding"]["observed_at"]
    assert intent["authorization_id"].startswith("sha256:")
    assert submission["schema_version"] == 4
    assert submission["intent_sha256"] == outcome.intent_sha256
    assert restore["body"]["properties"]["pointInTimeUTC"] == intent["restore_time"]


@pytest.mark.parametrize(
    "binding",
    [
        _binding(evidence_manifest_policy_sha256=f"sha256:{'f' * 64}"),
        _binding(source_server_resource_id="/subscriptions/wrong/source"),
        _binding(source_database_name="other"),
        _binding(approved_restore_time=datetime(2026, 9, 3, 11, 59, tzinfo=UTC)),
        _binding(expires_at=datetime(2026, 9, 3, 12, 16, tzinfo=UTC)),
        _binding(candidate_protected_master_sha="e" * 40),
        _binding(repository="Other/repository"),
        _binding(run_id="0"),
        _binding(attestation_receipt_sha256="not-a-digest"),
    ],
)
def test_invalid_evidence_binding_is_rejected_before_marker_or_arm_access(
    binding: Any,
) -> None:
    fake = FakeArm()
    markers = FakeMarkers()
    engine = _engine(fake, markers)
    with pytest.raises(safe_submit.SafeSubmitError):
        engine.submit(_request(binding=binding))
    assert markers.calls == []
    assert fake.calls == []


def test_raw_evidence_mapping_cannot_cross_the_typed_submit_boundary() -> None:
    fake = FakeArm()
    markers = FakeMarkers()
    engine = _engine(fake, markers)
    request = replace(_request(), evidence_binding={})
    with pytest.raises(
        safe_submit.SafeSubmitError, match="evidence_binding_type_invalid"
    ):
        engine.submit(request)
    assert markers.calls == []
    assert fake.calls == []


def test_unsealed_final_binding_and_sealed_partial_cannot_reach_submit() -> None:
    fake = FakeArm()
    markers = FakeMarkers()
    engine = _engine(fake, markers)

    with pytest.raises(TypeError):
        safe_submit.RestoreEvidenceBinding(schema_version=1)

    unsealed = safe_submit.RestoreEvidenceBinding()
    with pytest.raises(
        safe_submit.SafeSubmitError, match="evidence_binding_type_invalid"
    ):
        engine.submit(replace(_request(), evidence_binding=unsealed))

    class BindingSubclass(safe_submit.RestoreEvidenceBinding):
        pass

    trusted_test_binding = _binding()
    subclass = object.__new__(BindingSubclass)
    for name in safe_submit.RestoreEvidenceBinding.__slots__:
        object.__setattr__(subclass, name, getattr(trusted_test_binding, name))
    with pytest.raises(
        safe_submit.SafeSubmitError, match="evidence_binding_type_invalid"
    ):
        engine.submit(replace(_request(), evidence_binding=subclass))

    final_record = safe_submit.SafeRestoreSubmitter._binding_record(_binding())
    partial_record = {
        name: value
        for name, value in final_record.items()
        if name not in {"attestation_receipt_sha256", "provider_receipt_sha256"}
    }
    # Deliberately breach the authenticator's internal seal to prove that even
    # its exact sealed partial type is not a final safe-submit authorization.
    partial = object.__new__(server_authentication.ServerAuthenticatedSafeSubmitBinding)
    object.__setattr__(
        partial,
        "_record_bytes",
        server_authentication._canonical(partial_record),
    )
    object.__setattr__(
        partial,
        "_authentication_seal",
        server_authentication._SERVER_AUTHENTICATED_SAFE_SUBMIT_SEAL,
    )
    with pytest.raises(
        safe_submit.SafeSubmitError, match="evidence_binding_type_invalid"
    ):
        engine.submit(replace(_request(), evidence_binding=partial))
    assert markers.calls == []
    assert fake.calls == []


def test_rehearsal_id_is_derived_from_the_complete_evidence_authorization() -> None:
    changed = _binding(provider_receipt_sha256=f"sha256:{'e' * 64}")
    request = _request(binding=changed, rehearsal_id=REHEARSAL_ID)
    fake = FakeArm()
    markers = FakeMarkers()
    with pytest.raises(safe_submit.SafeSubmitError, match="workflow_identity_invalid"):
        _engine(fake, markers).submit(request)
    assert markers.calls == []
    assert fake.calls == []


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
        ("intent", REHEARSAL_ID),
        ("submission", REHEARSAL_ID),
        ("cleanup", REHEARSAL_ID),
    }
    assert len(_mutations(fake, "PUT")) == 1
    assert _mutations(fake, "DELETE") == []
    retry = engine.submit(_request())
    assert retry.status == "pending_late_materialization"
    assert len(_mutations(fake, "PUT")) == 1


@pytest.mark.parametrize("put_result", ["unknown", 409, 412, 429, 500])
def test_ambiguous_put_materialization_immediately_enters_handoff(
    put_result: str | int,
) -> None:
    class AmbiguousPutArm(FakeArm):
        def request(self, *args: Any, **kwargs: Any) -> Any:
            response = super().request(*args, **kwargs)
            if args[0] != "PUT":
                return response
            assert self.target is not None
            if put_result == "unknown":
                raise safe_submit.ArmOutcomeUnknown
            return safe_submit.ArmResponse(put_result)

    fake = AmbiguousPutArm()
    markers = FakeMarkers()
    outcome = _engine(fake, markers).submit(_request())

    assert outcome.status == "pending_late_materialization"
    assert outcome.target_observed is False
    assert outcome.cleanup_required is True
    assert fake.target is not None
    assert ("cleanup", REHEARSAL_ID) in markers.records
    assert len(_mutations(fake, "GET")) == 1
    assert len(_mutations(fake, "PUT")) == 1
    assert _mutations(fake, "DELETE") == []


def test_unknown_target_preflight_creates_tombstone_without_submission_or_delete() -> (
    None
):
    fake = FakeArm()
    fake.target_get_unknowns = 1
    markers = FakeMarkers()
    outcome = _engine(fake, markers).submit(_request())

    assert outcome.status == "pending_late_materialization"
    assert outcome.target_observed is False
    assert outcome.cleanup_required is True
    assert set(markers.records) == {
        ("intent", REHEARSAL_ID),
        ("cleanup", REHEARSAL_ID),
    }
    assert _mutations(fake, "PUT") == []
    assert _mutations(fake, "DELETE") == []


def test_initial_target_read_failure_after_exact_submission_returns_handoff() -> None:
    class TargetReadFailureArm(FakeArm):
        def request(self, *args: Any, **kwargs: Any) -> Any:
            if args[0] == "GET":
                super().request(*args, **kwargs)
                raise safe_submit.SafeSubmitError("target_read_refused")
            return super().request(*args, **kwargs)

    fake = TargetReadFailureArm()
    markers = FakeMarkers()
    engine = _engine(fake, markers)
    intent = engine._build_intent(_request())
    intent_digest = f"sha256:{safe_submit._sha256(safe_submit._canonical(intent))}"
    markers.records[("intent", REHEARSAL_ID)] = safe_submit._canonical(intent)
    markers.records[("submission", REHEARSAL_ID)] = safe_submit._canonical(
        engine._submission_record(intent, intent_digest)
    )

    outcome = engine.submit(_request())

    assert outcome.status == "pending_late_materialization"
    assert outcome.target_observed is False
    assert outcome.cleanup_required is True
    assert markers.records[("cleanup", REHEARSAL_ID)] == safe_submit._canonical(
        engine._cleanup_record(intent, intent_digest)
    )
    assert _mutations(fake, "PUT") == []
    assert _mutations(fake, "DELETE") == []


@pytest.mark.parametrize(
    ("preexisting_target", "status", "observed", "put_count"),
    [
        (False, "pending_late_materialization", False, 1),
        (True, "cleanup_pending", True, 0),
    ],
)
def test_poll_target_read_failure_returns_handoff_and_preserves_observation(
    preexisting_target: bool, status: str, observed: bool, put_count: int
) -> None:
    class PollReadFailureArm(FakeArm):
        def __init__(self) -> None:
            super().__init__()
            self.get_count = 0

        def request(self, *args: Any, **kwargs: Any) -> Any:
            if args[0] == "GET":
                self.get_count += 1
                if self.get_count == 2:
                    super().request(*args, **kwargs)
                    raise safe_submit.SafeSubmitError("target_read_refused")
            return super().request(*args, **kwargs)

    fake = PollReadFailureArm()
    markers = FakeMarkers()
    engine = _engine(fake, markers)
    if preexisting_target:
        intent = engine._build_intent(_request())
        intent_digest = f"sha256:{safe_submit._sha256(safe_submit._canonical(intent))}"
        markers.records[("intent", REHEARSAL_ID)] = safe_submit._canonical(intent)
        markers.records[("submission", REHEARSAL_ID)] = safe_submit._canonical(
            engine._submission_record(intent, intent_digest)
        )
        fake.materialize(
            intent["target_server_resource_id"],
            body=engine._restore_body(intent, intent_digest),
        )

    outcome = engine.submit(_request())

    assert outcome.status == status
    assert outcome.target_observed is observed
    assert outcome.cleanup_required is True
    assert ("cleanup", REHEARSAL_ID) in markers.records
    assert len(_mutations(fake, "PUT")) == put_count
    assert _mutations(fake, "DELETE") == []


def test_unknown_cleanup_preflight_reconciles_exact_tombstone_without_arm() -> None:
    fake = FakeArm()
    markers = FakeMarkers()
    markers.unknown_reads[("cleanup", REHEARSAL_ID)] = 1

    outcome = _engine(fake, markers).submit(_request())

    assert outcome.status == "pending_late_materialization"
    assert ("cleanup", REHEARSAL_ID) in markers.records
    assert fake.calls == []


def test_provision_call_graph_has_no_cleanup_delete_path() -> None:
    source = "\n".join(
        (
            inspect.getsource(safe_submit.SafeRestoreSubmitter.submit),
            inspect.getsource(safe_submit.SafeRestoreSubmitter._poll_restore),
            inspect.getsource(safe_submit.SafeRestoreSubmitter._defer_cleanup),
        )
    )

    assert "_delete_target_for_cleanup" not in source
    assert '"DELETE"' not in source


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
    with pytest.raises(safe_submit.SafeSubmitError, match="evidence_expired"):
        engine.submit(_request())
    assert fake.calls == []
    assert markers.calls == [("GET", "intent", REHEARSAL_ID)]


@pytest.mark.parametrize(
    ("before_count", "status", "put_count", "delete_count"),
    [
        (5, "pending_late_materialization", 0, 0),
        (8, "pending_late_materialization", 1, 0),
    ],
)
def test_expiry_at_late_submission_boundaries_enters_cleanup(
    before_count: int, status: str, put_count: int, delete_count: int
) -> None:
    fake = FakeArm()
    markers = FakeMarkers()
    at_expiry = datetime(2026, 9, 3, 12, 10, tzinfo=UTC)
    clock_calls = 0

    def clock() -> datetime:
        nonlocal clock_calls
        clock_calls += 1
        return TEST_NOW if clock_calls <= before_count else at_expiry

    engine = safe_submit.SafeRestoreSubmitter(
        fake, markers, _config(), sleeper=lambda _: None, clock=clock
    )
    outcome = engine.submit(_request(cleanup_not_after=at_expiry))
    assert outcome.status == status
    assert outcome.cleanup_required is True
    assert ("cleanup", REHEARSAL_ID) in markers.records
    assert len(_mutations(fake, "PUT")) == put_count
    assert len(_mutations(fake, "DELETE")) == delete_count


@pytest.mark.parametrize(
    ("mode", "status", "put_count", "delete_count"),
    [
        ("before-put", "pending_late_materialization", 0, 0),
        ("during-put", "pending_late_materialization", 1, 0),
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
                self.markers.records[("cleanup", REHEARSAL_ID)] = (
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
    assert outcome.cleanup_required is True
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


def test_expired_evidence_retry_reconciles_or_cleans_up_without_a_new_put() -> None:
    fake = FakeArm()
    markers = FakeMarkers()
    assert _engine(fake, markers).submit(_request()).status == "ready"
    fake.target = None
    expired = _engine(
        fake,
        markers,
    )
    expired.clock = lambda: datetime(2026, 9, 3, 12, 15, tzinfo=UTC)
    outcome = expired.submit(_request())
    assert outcome.status == "pending_late_materialization"
    assert outcome.cleanup_required is True
    assert ("cleanup", REHEARSAL_ID) in markers.records
    assert len(_mutations(fake, "PUT")) == 1


def test_cleanup_tombstone_prevents_a_retry_from_recreating_the_target() -> None:
    fake = FakeArm()
    markers = FakeMarkers()
    engine = _engine(fake, markers)
    ready = engine.submit(_request())
    assert ready.status == "ready"
    cleaned = engine.cleanup_intent(REHEARSAL_ID)
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
    markers.records[("intent", REHEARSAL_ID)] = safe_submit._canonical(intent)
    assert engine.cleanup_intent(REHEARSAL_ID).status == "prepared_only"
    assert ("cleanup", REHEARSAL_ID) in markers.records
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
    key = ("cleanup", REHEARSAL_ID)
    if invalid_properties:
        markers.invalid_present.add(key)
    else:
        markers.records[key] = b"not-json"
    outcome = engine.submit(_request())
    assert outcome.status == "cleanup_marker_unproven"
    assert outcome.cleanup_required is True
    assert _mutations(fake, "PUT") == []
    if invalid_properties:
        cleanup = engine.cleanup_intent(REHEARSAL_ID)
        assert cleanup.status == "cleanup_marker_unproven"
        assert cleanup.cleanup_required is True


@pytest.mark.parametrize("mode", ["mismatch", "wrong-readback"])
def test_cleanup_requires_exact_canonical_tombstone_before_delete(mode: str) -> None:
    class WrongReadbackMarkers(FakeMarkers):
        def create_if_absent(self, kind: str, rehearsal_id: str, raw: bytes) -> bytes:
            if kind == "cleanup":
                self.calls.append(("PUT", kind, rehearsal_id))
                return b"{}"
            return super().create_if_absent(kind, rehearsal_id, raw)

    fake = FakeArm()
    markers = WrongReadbackMarkers() if mode == "wrong-readback" else FakeMarkers()
    engine = _engine(fake, markers)
    intent = engine._build_intent(_request())
    markers.records[("intent", REHEARSAL_ID)] = safe_submit._canonical(intent)
    if mode == "mismatch":
        wrong = engine._cleanup_record(
            intent,
            f"sha256:{'f' * 64}",
        )
        markers.records[("cleanup", REHEARSAL_ID)] = safe_submit._canonical(wrong)

    outcome = engine.cleanup_intent(REHEARSAL_ID)

    assert outcome.status == "cleanup_marker_unproven"
    assert outcome.cleanup_required is True
    assert _mutations(fake, "PATCH") == []
    assert _mutations(fake, "DELETE") == []


def test_invalid_materialized_target_is_tombstoned_without_delete() -> None:
    class InvalidAfterPutArm(FakeArm):
        def request(self, *args: Any, **kwargs: Any) -> Any:
            response = super().request(*args, **kwargs)
            if args[0] == "PUT":
                assert self.target is not None
                self.target["name"] = "wrong-target"
            return response

    fake = InvalidAfterPutArm()
    markers = FakeMarkers()
    outcome = _engine(fake, markers).submit(_request())

    assert outcome.status == "cleanup_pending"
    assert outcome.target_observed is True
    assert outcome.cleanup_required is True
    assert ("cleanup", REHEARSAL_ID) in markers.records
    assert _mutations(fake, "DELETE") == []


@pytest.mark.parametrize("exact_submission", [False, True])
def test_preexisting_invalid_target_returns_observed_handoff(
    exact_submission: bool,
) -> None:
    fake = FakeArm()
    markers = FakeMarkers()
    engine = _engine(fake, markers)
    intent = engine._build_intent(_request())
    intent_digest = f"sha256:{safe_submit._sha256(safe_submit._canonical(intent))}"
    markers.records[("intent", REHEARSAL_ID)] = safe_submit._canonical(intent)
    if exact_submission:
        markers.records[("submission", REHEARSAL_ID)] = safe_submit._canonical(
            engine._submission_record(intent, intent_digest)
        )
    fake.materialize(
        intent["target_server_resource_id"],
        body=engine._restore_body(intent, intent_digest),
    )
    assert fake.target is not None
    fake.target["name"] = "wrong-target"

    outcome = engine.submit(_request())

    assert outcome.status == "cleanup_pending"
    assert outcome.target_observed is True
    assert outcome.cleanup_required is True
    assert _mutations(fake, "PUT") == []
    assert _mutations(fake, "DELETE") == []


@pytest.mark.parametrize("failure_mode", ["write", "readback", "unexpected"])
def test_tombstone_failure_returns_bounded_unproven_handoff(
    failure_mode: str,
) -> None:
    class InvalidAfterPutArm(FakeArm):
        def request(self, *args: Any, **kwargs: Any) -> Any:
            response = super().request(*args, **kwargs)
            if args[0] == "PUT":
                assert self.target is not None
                self.target["name"] = "wrong-target"
            return response

    class FailingCleanupMarkers(FakeMarkers):
        def create_if_absent(self, kind: str, rehearsal_id: str, raw: bytes) -> bytes:
            if kind == "cleanup" and failure_mode == "unexpected":
                raise RuntimeError("untrusted marker failure text")
            return super().create_if_absent(kind, rehearsal_id, raw)

    fake = InvalidAfterPutArm()
    markers = FailingCleanupMarkers()
    key = ("cleanup", REHEARSAL_ID)
    if failure_mode == "write":
        markers.fail_create_before.add(key)
    elif failure_mode == "readback":
        markers.fail_create_after.add(key)

    outcome = _engine(fake, markers).submit(_request())

    assert outcome.status == "cleanup_marker_unproven"
    assert outcome.target_observed is True
    assert outcome.cleanup_required is True
    assert _mutations(fake, "DELETE") == []


def test_post_put_cleanup_read_failure_returns_bounded_unproven_handoff() -> None:
    fake = FakeArm()
    markers = FakeMarkers()
    cleanup_reads = 0

    def fail_post_put_cleanup_reads(kind: str, _rehearsal_id: str) -> None:
        nonlocal cleanup_reads
        if kind == "cleanup":
            cleanup_reads += 1
            if cleanup_reads >= 3:
                raise safe_submit.SafeSubmitError("marker_read_refused")

    markers.on_read = fail_post_put_cleanup_reads
    outcome = _engine(fake, markers).submit(_request())

    assert outcome.status == "cleanup_marker_unproven"
    assert outcome.target_observed is False
    assert outcome.cleanup_required is True
    assert len(_mutations(fake, "PUT")) == 1
    assert _mutations(fake, "DELETE") == []


def test_post_put_trusted_clock_failure_returns_bounded_handoff() -> None:
    fake = FakeArm()
    markers = FakeMarkers()

    def clock() -> datetime:
        if _mutations(fake, "PUT"):
            raise safe_submit.SafeSubmitError("trusted_clock_unavailable")
        return TEST_NOW

    engine = safe_submit.SafeRestoreSubmitter(
        fake,
        markers,
        _config(),
        sleeper=lambda _: None,
        clock=clock,
    )
    outcome = engine.submit(_request())

    assert outcome.status == "pending_late_materialization"
    assert outcome.target_observed is False
    assert outcome.cleanup_required is True
    assert len(_mutations(fake, "PUT")) == 1
    assert _mutations(fake, "DELETE") == []


def test_post_put_target_state_failure_returns_observed_handoff() -> None:
    class TargetStateFailureSubmitter(safe_submit.SafeRestoreSubmitter):
        def _target_state(self, target: dict[str, Any]) -> str:
            raise RuntimeError("untrusted target state failure")

    fake = FakeArm()
    markers = FakeMarkers()
    engine = TargetStateFailureSubmitter(
        fake,
        markers,
        _config(),
        sleeper=lambda _: None,
        clock=lambda: TEST_NOW,
    )
    outcome = engine.submit(_request())

    assert outcome.status == "cleanup_pending"
    assert outcome.target_observed is True
    assert outcome.cleanup_required is True
    assert len(_mutations(fake, "PUT")) == 1
    assert _mutations(fake, "DELETE") == []


@pytest.mark.parametrize(
    ("marker_readback_fails", "status"),
    [(False, "cleanup_pending"), (True, "cleanup_marker_unproven")],
)
def test_isolation_failure_returns_bounded_handoff_without_delete(
    marker_readback_fails: bool, status: str
) -> None:
    class IsolationFailureArm(FakeArm):
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
            if method == "PATCH":
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
                raise safe_submit.SafeSubmitError("arm_authentication_failed")
            return super().request(
                method,
                resource_id,
                api_version=api_version,
                body=body,
                headers=headers,
                timeout_seconds=timeout_seconds,
            )

    fake = IsolationFailureArm()
    fake.restore_public_access = "Enabled"
    markers = FakeMarkers()
    if marker_readback_fails:
        markers.fail_create_after.add(("cleanup", REHEARSAL_ID))

    outcome = _engine(fake, markers).submit(_request())

    assert outcome.status == status
    assert outcome.target_observed is True
    assert outcome.cleanup_required is True
    assert len(_mutations(fake, "PATCH")) == 1
    assert _mutations(fake, "DELETE") == []


@pytest.mark.parametrize("tags", [None, {}, {"rehearsal-id": REHEARSAL_ID}])
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
    cleaned = engine.cleanup_intent(REHEARSAL_ID)
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
        engine.cleanup_intent(REHEARSAL_ID)
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
        engine.cleanup_intent(REHEARSAL_ID)
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
    cleaned = engine.cleanup_intent(REHEARSAL_ID)
    assert cleaned.status == "cleaned"
    assert len(_mutations(fake, "PATCH")) == 1
    assert len(_mutations(fake, "DELETE")) == 1


def test_submit_defers_cleanup_immediately_on_definite_isolation_refusal() -> None:
    fake = FakeArm()
    fake.restore_public_access = "Enabled"
    fake.patch_status = 403
    engine = _engine(fake, restore_poll_attempts=3)
    outcome = engine.submit(_request())
    assert outcome.status == "cleanup_pending"
    assert len(_mutations(fake, "PATCH")) == 1
    assert _mutations(fake, "DELETE") == []


def test_unknown_submission_record_never_claims_prepared_only() -> None:
    fake = FakeArm()
    markers = FakeMarkers()
    engine = _engine(fake, markers)
    assert engine.submit(_request()).status == "ready"
    fake.target = None
    markers.unknown_reads[("submission", REHEARSAL_ID)] = 1
    outcome = engine.cleanup_intent(REHEARSAL_ID)
    assert outcome.cleanup_required is True
    assert ("cleanup", REHEARSAL_ID) in markers.records
    assert _mutations(fake, "DELETE") == []


def test_unproven_intent_persistence_blocks_restore_submission() -> None:
    fake = FakeArm()
    markers = FakeMarkers()
    markers.fail_create_before.add(("intent", REHEARSAL_ID))
    engine = _engine(fake, markers)
    with pytest.raises(
        safe_submit.SafeSubmitError, match="intent_persistence_unproven"
    ):
        engine.submit(_request())
    assert _mutations(fake, "PUT") == []
