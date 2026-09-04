from __future__ import annotations

import json
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from email.message import Message
from typing import Any, Iterator

import pytest

from scripts import read_staging_runtime_record_material as ingress
from scripts import validate_staging_restore_launch_binding as launch
from scripts import validate_staging_restore_runtime_binding as binding

from tests.infrastructure import test_staging_restore_launch_material as launch_helpers
from tests.infrastructure import test_staging_restore_runtime_binding as helpers

IDENTITY_ENDPOINT = "http://127.0.0.1:42356/msi/token"
IDENTITY_HEADER = "private-identity-header"
EPOCH_NOW = 2_000_000_000
ROOT_BYTES = b'{"root":"exact-private-version"}'


class Response:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        content_type: str = "application/json",
        content_length: bool = True,
        url: str | None = None,
        extra_headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.body = body
        self.status = status
        self.url = url
        self.headers = Message()
        self.headers.add_header("Content-Type", content_type)
        if content_length:
            self.headers.add_header("Content-Length", str(len(body)))
        for name, value in extra_headers:
            self.headers.add_header(name, value)

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def geturl(self) -> str:
        assert self.url is not None
        return self.url

    def read(self, maximum: int) -> bytes:
        result, self.body = self.body[:maximum], self.body[maximum:]
        return result


class Opener:
    def __init__(
        self,
        responses: list[Response],
        *,
        name: str = "request",
        events: list[str] | None = None,
    ) -> None:
        self.responses = responses
        self.calls: list[tuple[Any, int]] = []
        self.effects: list[Any] = []
        self.name = name
        self.events = events

    def open(self, request: Any, *, timeout: int) -> Response:
        self.calls.append((request, timeout))
        if self.events is not None:
            self.events.append(self.name)
        if self.effects:
            self.effects.pop(0)()
        response = self.responses.pop(0)
        response.url = response.url or request.full_url
        return response


@contextmanager
def _no_deadline(_seconds: int) -> Iterator[None]:
    yield


def _headers(request: Any) -> dict[str, str]:
    return {name.lower(): value for name, value in request.header_items()}


def _token(policy: dict[str, Any], resource: str, token: str) -> bytes:
    return json.dumps(
        {
            "access_token": token,
            "client_id": policy["identities"]["jobs"]["provision"]["client_id"],
            "expires_on": str(EPOCH_NOW + 3_600),
            "resource": resource,
            "token_type": "Bearer",
        },
        separators=(",", ":"),
    ).encode()


def _case(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    policy = helpers._configured_policy()
    root = policy["evidence_ingress"]["private_versioned_secret_source"]["secrets"][
        "github_trusted_root"
    ]
    root["sha256"] = binding._digest(ROOT_BYTES)
    material, _sources, _transport = launch_helpers._receive(monkeypatch, policy=policy)
    receipt = material.authenticated_receipt
    references = tuple(
        binding.ExternalRuntimeBindingReference._from_authenticated_broker_launch_receipt(
            policy, receipt, source_job_kind=action
        )
        for action in launch.JOB_KINDS
    )
    storage_resource = policy["runtime_record_ingress"]["oauth_resource"]
    vault_resource = policy["evidence_ingress"]["private_versioned_secret_source"][
        "oauth_resource"
    ]
    events: list[str] = []
    identity = Opener(
        [
            Response(_token(policy, storage_resource, "storage-token")),
            Response(_token(policy, vault_resource, "vault-token")),
        ],
        name="identity",
        events=events,
    )
    blob = Opener(
        [
            Response(launch_helpers.RUNTIME_BYTES),
            Response(
                launch_helpers.RUNTIME_SIGNATURE_BYTES,
                content_type="application/vnd.dev.sigstore.bundle.v0.3+json",
            ),
        ],
        name="blob",
        events=events,
    )
    secret_uri = root["versioned_https_uri"]
    vault_body = json.dumps(
        {
            "value": ROOT_BYTES.decode(),
            "id": secret_uri,
            "attributes": {"enabled": True},
        },
        separators=(",", ":"),
    ).encode()
    vault = Opener([Response(vault_body)], name="vault", events=events)
    seen: list[dict[str, Any]] = []

    def authenticate(_policy: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return material.runtime_signature_verification

    monkeypatch.setattr(
        ingress.authentication, "authenticate_runtime_record_signature", authenticate
    )
    monkeypatch.setenv("IDENTITY_ENDPOINT", IDENTITY_ENDPOINT)
    monkeypatch.setenv("IDENTITY_HEADER", IDENTITY_HEADER)
    verifier = Verifier()
    reader = ingress.ManagedIdentityPrivateRuntimeMaterialReader(
        verifier=verifier,
        identity_opener=identity,
        blob_opener=blob,
        vault_opener=vault,
        epoch_clock=lambda: EPOCH_NOW,
        monotonic=lambda: 10.0,
        deadline_factory=_no_deadline,
    )
    return {
        "policy": policy,
        "receipt": receipt,
        "references": references,
        "proof": material.runtime_signature_verification,
        "verifier": verifier,
        "reader": reader,
        "identity": identity,
        "blob": blob,
        "vault": vault,
        "seen": seen,
        "events": events,
    }


class Verifier:
    def verify(self, **_kwargs: Any) -> None:
        return None


def _read(case: dict[str, Any], clock: Any = lambda: launch_helpers.NOW) -> Any:
    provision, cleanup = case["references"]
    return case["reader"].read_and_authenticate_runtime_record(
        policy=case["policy"],
        authenticated_receipt=case["receipt"],
        provision_reference=provision,
        cleanup_reference=cleanup,
        clock=clock,
    )


def test_exact_private_reads_feed_same_process_authenticator_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(monkeypatch)

    def shared_clock() -> datetime:
        return launch_helpers.NOW

    result = _read(case, shared_clock)
    assert result.record_bytes == launch_helpers.RUNTIME_BYTES
    assert result.signature_verification is case["proof"]
    assert len(case["identity"].calls) == 2
    assert len(case["blob"].calls) == 2
    assert len(case["vault"].calls) == 1
    assert case["events"] == ["identity", "blob", "blob", "identity", "vault"]
    resources = [
        case["policy"]["runtime_record_ingress"]["oauth_resource"],
        case["policy"]["evidence_ingress"]["private_versioned_secret_source"][
            "oauth_resource"
        ],
    ]
    client_id = case["policy"]["identities"]["jobs"]["provision"]["client_id"]
    for (request, timeout), resource in zip(case["identity"].calls, resources):
        parsed = urllib.parse.urlsplit(request.full_url)
        assert request.method == "GET" and timeout == 15
        assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == IDENTITY_ENDPOINT
        assert urllib.parse.parse_qsl(parsed.query) == [
            ("api-version", "2019-08-01"),
            ("resource", resource),
            ("client_id", client_id),
        ]
        assert _headers(request) == {
            "accept": "application/json",
            "accept-encoding": "identity",
            "metadata": "true",
            "x-identity-header": IDENTITY_HEADER,
        }
    provision = case["references"][0]
    assert [call[0].full_url for call in case["blob"].calls] == [
        provision.record_uri,
        provision.signature_bundle_uri,
    ]
    assert [call[0].method for call in case["blob"].calls] == ["GET", "GET"]
    assert [_headers(call[0]) for call in case["blob"].calls] == [
        {
            "accept": "application/json",
            "accept-encoding": "identity",
            "authorization": "Bearer storage-token",
            "x-ms-version": "2021-12-02",
        },
        {
            "accept": "application/vnd.dev.sigstore.bundle.v0.3+json",
            "accept-encoding": "identity",
            "authorization": "Bearer storage-token",
            "x-ms-version": "2021-12-02",
        },
    ]
    root = case["policy"]["evidence_ingress"]["private_versioned_secret_source"][
        "secrets"
    ]["github_trusted_root"]
    assert case["vault"].calls[0][0].full_url == (
        f"{root['versioned_https_uri']}?api-version=7.4"
    )
    assert case["vault"].calls[0][0].method == "GET"
    assert _headers(case["vault"].calls[0][0]) == {
        "accept": "application/json",
        "accept-encoding": "identity",
        "authorization": "Bearer vault-token",
    }
    authenticated = case["seen"][0]
    assert authenticated["record_bytes"] == launch_helpers.RUNTIME_BYTES
    assert authenticated["signature_bundle_bytes"] == (
        launch_helpers.RUNTIME_SIGNATURE_BYTES
    )
    assert authenticated["trusted_root_bytes"] == ROOT_BYTES
    assert authenticated["clock"] is shared_clock
    assert authenticated["verifier"] is case["verifier"]
    assert authenticated["provision_reference"] is case["references"][0]
    assert authenticated["cleanup_reference"] is case["references"][1]
    with pytest.raises(ingress.PrivateRuntimeMaterialFailure, match="already_used"):
        _read(case)


@pytest.mark.parametrize("missing", ("token_type", "resource", "client_id"))
def test_managed_identity_requires_exact_echo_fields(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    case = _case(monkeypatch)
    body = json.loads(_token(case["policy"], "https://storage.azure.com/", "token"))
    body.pop(missing)
    case["identity"].responses[0] = Response(
        json.dumps(body, separators=(",", ":")).encode()
    )
    with pytest.raises(
        ingress.PrivateRuntimeMaterialFailure,
        match="^runtime_ingress.identity_response_invalid$",
    ):
        _read(case)
    assert case["blob"].calls == []


def test_identity_environment_is_snapshotted_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(monkeypatch)
    case["identity"].effects.append(
        lambda: monkeypatch.setenv("IDENTITY_HEADER", "mutated-header")
    )
    _read(case)
    assert [
        _headers(request)["x-identity-header"]
        for request, _timeout in case["identity"].calls
    ] == [IDENTITY_HEADER, IDENTITY_HEADER]


@pytest.mark.parametrize("fault", ("status", "media", "audience"))
def test_managed_identity_refuses_non_success_or_unbound_response(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    case = _case(monkeypatch)
    response = case["identity"].responses[0]
    if fault == "status":
        response.status = 401
        expected = "runtime_ingress.identity_refused"
    elif fault == "media":
        response.headers.replace_header("Content-Type", "text/plain")
        expected = "runtime_ingress.identity_response_invalid"
    else:
        response.body = _token(case["policy"], "https://vault.azure.net", "token")
        response.headers.replace_header("Content-Length", str(len(response.body)))
        expected = "runtime_ingress.identity_response_invalid"
    with pytest.raises(ingress.PrivateRuntimeMaterialFailure, match=f"^{expected}$"):
        _read(case)
    assert case["blob"].calls == []


def test_managed_identity_token_and_clock_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(monkeypatch)
    case["identity"].responses[0] = Response(
        _token(case["policy"], "https://storage.azure.com/", "x" * 16_385)
    )
    with pytest.raises(
        ingress.PrivateRuntimeMaterialFailure,
        match="^runtime_ingress.identity_response_invalid$",
    ):
        _read(case)

    case = _case(monkeypatch)
    times = iter((EPOCH_NOW, EPOCH_NOW - 1))
    case["reader"]._epoch_timeline = ingress._Timeline(
        lambda: next(times), "runtime_ingress.identity_clock_invalid"
    )
    with pytest.raises(
        ingress.PrivateRuntimeMaterialFailure,
        match="^runtime_ingress.identity_clock_invalid$",
    ):
        _read(case)
    assert len(case["vault"].calls) == 0


@pytest.mark.parametrize(
    ("response", "error"),
    (
        (Response(b"{}", content_length=False), "http_response_invalid"),
        (
            Response(b"{}", extra_headers=(("Content-Length", "2"),)),
            "http_response_invalid",
        ),
        (
            Response(b"{}", extra_headers=(("Transfer-Encoding", "chunked"),)),
            "http_response_invalid",
        ),
        (
            Response(b"{}", extra_headers=(("Content-Encoding", "gzip"),)),
            "http_response_invalid",
        ),
        (
            Response(b"{}", url="https://redirect.invalid/object"),
            "http_response_invalid",
        ),
        (
            Response(
                b"{}", content_length=False, extra_headers=(("Content-Length", "11"),)
            ),
            "http_response_too_large",
        ),
    ),
)
def test_wire_response_rejects_unbounded_or_ambiguous_framing(
    response: Response, error: str
) -> None:
    response.url = response.url or "https://expected.invalid/object"
    with pytest.raises(
        ingress.PrivateRuntimeMaterialFailure,
        match=f"^runtime_ingress.{error}$",
    ):
        ingress._read_wire_response(
            response,
            expected_uri="https://expected.invalid/object",
            maximum_bytes=10,
        )


@pytest.mark.parametrize(
    "stage",
    (
        "record",
        "record-status",
        "record-encoding",
        "record-oversize",
        "bundle",
        "root-id",
        "root-value",
        "root-disabled",
        "root-duplicate",
        "root-status",
        "root-content-type",
        "authenticator",
    ),
)
def test_material_or_authentication_failure_is_bounded(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    case = _case(monkeypatch)
    secret = "must-not-escape-private-reader"
    if stage == "record":
        case["blob"].responses[0] = Response(secret.encode())
        expected = "runtime_ingress.record_invalid"
    elif stage == "record-status":
        case["blob"].responses[0] = Response(launch_helpers.RUNTIME_BYTES, status=404)
        expected = "runtime_ingress.record_invalid"
    elif stage == "record-encoding":
        case["blob"].responses[0] = Response(
            launch_helpers.RUNTIME_BYTES,
            extra_headers=(("Content-Encoding", "gzip"),),
        )
        expected = "runtime_ingress.record_invalid"
    elif stage == "record-oversize":
        case["blob"].responses[0] = Response(
            b"x",
            content_length=False,
            extra_headers=(("Content-Length", "262145"),),
        )
        expected = "runtime_ingress.record_invalid"
    elif stage == "bundle":
        case["blob"].responses[1] = Response(
            launch_helpers.RUNTIME_SIGNATURE_BYTES, content_type="application/json"
        )
        expected = "runtime_ingress.signature_bundle_invalid"
    elif stage == "root-id":
        case["vault"].responses[0] = Response(
            json.dumps(
                {"value": secret, "id": "versionless", "attributes": {"enabled": True}}
            ).encode()
        )
        expected = "runtime_ingress.trusted_root_invalid"
    elif stage in {"root-value", "root-disabled"}:
        root = case["policy"]["evidence_ingress"]["private_versioned_secret_source"][
            "secrets"
        ]["github_trusted_root"]
        body = {
            "value": secret if stage == "root-value" else ROOT_BYTES.decode(),
            "id": root["versioned_https_uri"],
            "attributes": {"enabled": stage != "root-disabled"},
        }
        case["vault"].responses[0] = Response(
            json.dumps(body, separators=(",", ":")).encode()
        )
        expected = "runtime_ingress.trusted_root_invalid"
    elif stage == "root-duplicate":
        case["vault"].responses[0] = Response(
            b'{"value":"one","value":"two","id":"x","attributes":{"enabled":true}}'
        )
        expected = "runtime_ingress.trusted_root_invalid"
    elif stage == "root-status":
        case["vault"].responses[0].status = 403
        expected = "runtime_ingress.trusted_root_invalid"
    elif stage == "root-content-type":
        case["vault"].responses[0].headers.replace_header("Content-Type", "text/plain")
        expected = "runtime_ingress.trusted_root_invalid"
    else:

        def refuse(_policy: Any, **_kwargs: Any) -> None:
            raise RuntimeError(secret)

        monkeypatch.setattr(
            ingress.authentication, "authenticate_runtime_record_signature", refuse
        )
        expected = "runtime_ingress.authentication_failed"
    with pytest.raises(
        ingress.PrivateRuntimeMaterialFailure, match=f"^{expected}$"
    ) as raised:
        _read(case)
    assert secret not in str(raised.value)


def test_policy_reference_and_execution_refuse_before_private_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(monkeypatch)
    disabled = json.loads(binding.POLICY_PATH.read_text())
    case["policy"] = disabled
    with pytest.raises(
        ingress.PrivateRuntimeMaterialFailure, match="^runtime_ingress.policy_invalid$"
    ):
        _read(case)
    assert case["identity"].calls == []

    case = _case(monkeypatch)
    root = case["policy"]["evidence_ingress"]["private_versioned_secret_source"][
        "secrets"
    ]["github_trusted_root"]
    root["version"] = "3" * 32
    with pytest.raises(
        ingress.PrivateRuntimeMaterialFailure, match="^runtime_ingress.policy_invalid$"
    ):
        _read(case)
    assert case["identity"].calls == []

    case = _case(monkeypatch)
    object.__setattr__(case["receipt"], "action", "invalid")
    with pytest.raises(
        ingress.PrivateRuntimeMaterialFailure, match="^runtime_ingress.receipt_invalid$"
    ):
        _read(case)
    assert case["identity"].calls == []

    case = _case(monkeypatch)
    case["identity"].effects.append(
        lambda: object.__setattr__(case["receipt"], "action", "cleanup")
    )
    with pytest.raises(
        ingress.PrivateRuntimeMaterialFailure, match="^runtime_ingress.receipt_invalid$"
    ):
        _read(case)
    assert len(case["identity"].calls) == 1
    assert case["blob"].calls == []

    case = _case(monkeypatch)
    case["identity"].effects.append(
        lambda: monkeypatch.setenv(launch.EXECUTION_NAME_ENV, "other-execution")
    )
    with pytest.raises(
        ingress.PrivateRuntimeMaterialFailure,
        match="^runtime_ingress.execution_identity_invalid$",
    ):
        _read(case)
    assert len(case["identity"].calls) == 1
    assert case["blob"].calls == []

    case = _case(monkeypatch)
    case["references"] = (case["references"][0], case["references"][0])
    with pytest.raises(
        ingress.PrivateRuntimeMaterialFailure,
        match="^runtime_ingress.reference_invalid$",
    ):
        _read(case)
    assert case["identity"].calls == []

    case = _case(monkeypatch)
    monkeypatch.setenv(launch.EXECUTION_NAME_ENV, "other-execution")
    with pytest.raises(
        ingress.PrivateRuntimeMaterialFailure,
        match="^runtime_ingress.execution_identity_invalid$",
    ):
        _read(case)
    assert case["identity"].calls == []


def test_concurrent_calls_allow_exactly_one_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(monkeypatch)

    def invoke() -> Any:
        try:
            return _read(case)
        except ingress.PrivateRuntimeMaterialFailure as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: invoke(), range(2)))
    assert (
        sum(
            isinstance(item, ingress.launch_material.AuthenticatedRuntimeRecordMaterial)
            for item in outcomes
        )
        == 1
    )
    assert outcomes.count("runtime_ingress.already_used") == 1
