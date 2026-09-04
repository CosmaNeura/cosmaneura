from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any

import pytest

from scripts import receive_staging_broker_launch_receipt as receiver
from scripts import validate_staging_restore_launch_binding as launch
from scripts import validate_staging_restore_runtime_binding as binding

from tests.infrastructure import (
    test_staging_broker_launch_receipt_authentication as auth_helpers,
)
from tests.infrastructure import test_staging_restore_runtime_binding as helpers

EXECUTION_NAME = "execution-1"
OBSERVED_AT = datetime(2026, 9, 4, 12, 4, tzinfo=UTC)
IDENTITY_ENDPOINT = "http://127.0.0.1:42356/msi/token"
IDENTITY_HEADER = "opaque-platform-secret"
EPOCH_NOW = 2_000_000_000
TOKEN = "header.payload.signature"


class Headers:
    def __init__(self, values: Mapping[str, str | list[str]]) -> None:
        self.values = {
            name.lower(): value if isinstance(value, list) else [value]
            for name, value in values.items()
        }

    def get_all(self, name: str) -> list[str] | None:
        return self.values.get(name.lower())


class HttpResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        url: str = "",
        headers: Mapping[str, str | list[str]] | None = None,
    ) -> None:
        self.body = body
        self.offset = 0
        self.status = status
        self.url = url
        self.headers = Headers(
            headers
            or {
                "Content-Type": "application/json; charset=utf-8",
                "Content-Length": str(len(body)),
            }
        )

    def __enter__(self) -> HttpResponse:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def geturl(self) -> str:
        return self.url

    def read(self, maximum: int) -> bytes:
        chunk = self.body[self.offset : self.offset + maximum]
        self.offset += len(chunk)
        return chunk


class HttpOpener:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[Any, int]] = []

    def open(self, request: Any, *, timeout: int) -> Any:
        self.calls.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, urllib.error.HTTPError) and not response.url:
            response.url = request.full_url
        if isinstance(response, Exception):
            raise response
        if not response.url:
            response.url = request.full_url
        return response


def _no_deadline(timeout_seconds: int) -> Any:
    del timeout_seconds
    return nullcontext()


def _http_error(status: int, body: bytes) -> urllib.error.HTTPError:
    headers: Any = Headers(
        {"Content-Type": "application/json", "Content-Length": str(len(body))}
    )
    return urllib.error.HTTPError("", status, "bounded", headers, io.BytesIO(body))


class Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class FakeTransport:
    def __init__(
        self,
        bodies: dict[str, bytes],
        statuses: dict[str, list[int]] | None = None,
        *,
        response_changes: dict[str, Any] | None = None,
        after_request: Callable[[receiver.AuthenticatedMailboxGetRequest], None]
        | None = None,
    ) -> None:
        self.bodies = bodies
        self.statuses = statuses or {
            "receipt": [200],
            "signature_envelope": [200],
        }
        self.response_changes = response_changes or {}
        self.after_request = after_request
        self.requests: list[receiver.AuthenticatedMailboxGetRequest] = []

    def get_exact_private_blob(
        self, request: receiver.AuthenticatedMailboxGetRequest
    ) -> receiver.AuthenticatedMailboxGetResponse:
        self.requests.append(request)
        status = self.statuses[request.object_kind].pop(0)
        body = self.bodies[request.object_kind] if status == 200 else b""
        arguments: dict[str, Any] = {
            "request": request,
            "final_uri": request.uri,
            "redirect_count": 0,
            "status_code": status,
            "content_type_values": ("application/json; charset=UTF-8",)
            if status == 200
            else (),
            "content_encoding_values": ("identity",),
            "content_length_values": (str(len(body)),),
            "body": body,
        }
        arguments.update(self.response_changes)
        response = receiver.AuthenticatedMailboxGetResponse._from_reviewed_managed_identity_private_blob_adapter(
            **arguments
        )
        if self.after_request is not None:
            self.after_request(request)
        return response


def _policy() -> dict[str, Any]:
    return helpers._configured_policy()


def _receipt(policy: dict[str, Any], action: str) -> bytes:
    launch_sha = f"sha256:{'a' * 64}"
    runtime_sha = f"sha256:{'b' * 64}"
    runtime_bundle_sha = f"sha256:{'c' * 64}"
    mailbox = policy["launch_binding"]["mailbox"]
    runtime_ingress = policy["runtime_record_ingress"]
    runtime_base = (
        f"https://{runtime_ingress['account_host']}/{runtime_ingress['container_name']}"
    )
    launch_uri = (
        f"https://{mailbox['account_host']}/{mailbox['container_names'][action]}/"
        f"{launch_sha[7:]}.json"
    )
    values: dict[str, Any] = {
        "schema_version": 1,
        "launch_record_uri": launch_uri,
        "launch_record_sha256": launch_sha,
        "action": action,
        "selected_job_resource_id": policy["runtime"]["jobs"][action]["resource_id"],
        "runtime_record_uri": f"{runtime_base}/{runtime_sha[7:]}.json",
        "runtime_record_sha256": runtime_sha,
        "runtime_signature_bundle_uri": (
            f"{runtime_base}/{runtime_sha[7:]}.sigstore.json"
        ),
        "runtime_signature_bundle_sha256": runtime_bundle_sha,
        "broker_oidc_jti_sha256": f"sha256:{'d' * 64}",
        "broker_oidc_replay_receipt_sha256": f"sha256:{'e' * 64}",
        "provision_job_body_sha256": f"sha256:{'1' * 64}",
        "provision_job_projection_sha256": f"sha256:{'2' * 64}",
        "provision_job_execution_template_sha256": f"sha256:{'3' * 64}",
        "provision_job_image_reference": "restoreexecutor.azurecr.io/cosmaneura/restore-executor@sha256:"
        + "4" * 64,
        "cleanup_job_body_sha256": f"sha256:{'5' * 64}",
        "cleanup_job_projection_sha256": f"sha256:{'6' * 64}",
        "cleanup_job_execution_template_sha256": f"sha256:{'7' * 64}",
        "cleanup_job_image_reference": "restoreexecutor.azurecr.io/cosmaneura/restore-executor@sha256:"
        + "8" * 64,
        "start_response_body_sha256": f"sha256:{'9' * 64}",
        "start_client_request_id": "12345678-1234-4234-8234-123456789abc",
        "start_provider_request_id": "87654321-4321-4321-8321-cba987654321",
        "execution_resource_id": (
            f"{policy['runtime']['jobs'][action]['resource_id']}/executions/"
            f"{EXECUTION_NAME}"
        ),
        "execution_body_sha256": f"sha256:{'0' * 64}",
        "execution_template_sha256": f"sha256:{'f' * 64}",
        "observed_at": "2026-09-04T12:03:00Z",
    }
    assert set(values) == set(
        policy["launch_binding"]["launch_receipt"]["required_fields"]
    )
    return binding._canonical(values)


def _mailbox_objects(policy: dict[str, Any], action: str) -> dict[str, bytes]:
    receipt = _receipt(policy, action)
    envelope = auth_helpers._envelope(policy, receipt)
    return {"receipt": receipt, "signature_envelope": envelope}


def _token_body(
    policy: dict[str, Any], action: str = "provision", **changes: Any
) -> bytes:
    value = {
        "access_token": TOKEN,
        "client_id": policy["identities"]["jobs"][action]["client_id"],
        "expires_on": str(EPOCH_NOW + 3_600),
        "resource": receiver.STORAGE_OAUTH_RESOURCE,
        "token_type": "Bearer",
    }
    value.update(changes)
    return json.dumps(value, separators=(",", ":")).encode()


def _token_source(
    policy: dict[str, Any],
    opener: HttpOpener,
    *,
    action: str = "provision",
    clock: Callable[[], float] = lambda: EPOCH_NOW,
) -> receiver.ContainerAppsStorageTokenSource:
    retrieval = policy["launch_binding"]["mailbox"]["execution_receipt_handoff"][
        "retrieval"
    ]
    return receiver.ContainerAppsStorageTokenSource(
        client_id=policy["identities"]["jobs"][action]["client_id"],
        identity_endpoint=IDENTITY_ENDPOINT,
        identity_header=IDENTITY_HEADER,
        service_api_version=retrieval["managed_identity_api_version"],
        maximum_response_bytes=retrieval["managed_identity_response_maximum_bytes"],
        expiry_skew_seconds=retrieval["managed_identity_expiry_skew_seconds"],
        maximum_lifetime_seconds=retrieval[
            "managed_identity_maximum_token_lifetime_seconds"
        ],
        maximum_timeout_seconds=retrieval["maximum_request_timeout_seconds"],
        opener=opener,
        clock=clock,
    )


def _receive(
    monkeypatch: pytest.MonkeyPatch,
    policy: dict[str, Any],
    transport: Any,
    clock: Clock,
    *,
    action: str = "provision",
    wall_clock: Callable[[], datetime] = lambda: OBSERVED_AT,
) -> launch.AuthenticatedBrokerLaunchReceipt:
    monkeypatch.setenv(launch.EXECUTION_NAME_ENV, EXECUTION_NAME)
    return receiver.receive_broker_launch_receipt(
        policy,
        action=action,
        transport=transport,
        monotonic=clock,
        sleeper=clock.sleep,
        wall_clock=wall_clock,
    )


@pytest.mark.parametrize("action", ("provision", "cleanup"))
def test_concrete_transport_uses_exact_job_uami_and_storage_requests(
    monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    policy = _policy()
    objects = _mailbox_objects(policy, action)
    receipt, envelope = objects["receipt"], objects["signature_envelope"]
    identity_opener = HttpOpener([HttpResponse(_token_body(policy, action))])
    blob_opener = HttpOpener(
        [
            _http_error(404, b'{"private":"not exposed"}'),
            HttpResponse(receipt),
            HttpResponse(envelope),
        ]
    )
    clock = Clock()
    transport = receiver.ManagedIdentityPrivateBlobTransport(
        _token_source(policy, identity_opener, action=action),
        opener=blob_opener,
        monotonic=clock,
        deadline=_no_deadline,
    )
    result = _receive(monkeypatch, policy, transport, clock, action=action)
    assert result._is_authenticated()
    assert result.action == action
    assert result.receipt_bytes == receipt
    assert result.signature_envelope_sha256 == binding._digest(envelope)
    assert len(identity_opener.calls) == 1
    identity_request, identity_timeout = identity_opener.calls[0]
    assert identity_timeout == 10
    identity_uri = urllib.parse.urlsplit(identity_request.full_url)
    assert (
        f"{identity_uri.scheme}://{identity_uri.netloc}{identity_uri.path}"
        == IDENTITY_ENDPOINT
    )
    assert urllib.parse.parse_qs(identity_uri.query) == {
        "api-version": ["2019-08-01"],
        "client_id": [policy["identities"]["jobs"][action]["client_id"]],
        "resource": [receiver.STORAGE_OAUTH_RESOURCE],
    }
    identity_headers = {
        name.lower(): value for name, value in identity_request.header_items()
    }
    assert identity_headers["x-identity-header"] == IDENTITY_HEADER
    assert identity_headers["metadata"] == "true"
    assert "authorization" not in identity_headers
    assert len(blob_opener.calls) == 3
    for blob_request, timeout in blob_opener.calls:
        assert blob_request.method == "GET"
        assert timeout == 10
        assert (
            urllib.parse.urlsplit(blob_request.full_url).hostname
            == (policy["launch_binding"]["mailbox"]["account_host"])
        )
        assert not urllib.parse.urlsplit(blob_request.full_url).query
        headers = {name.lower(): value for name, value in blob_request.header_items()}
        assert headers == {
            "accept": "application/json",
            "accept-encoding": "identity",
            "authorization": f"Bearer {TOKEN}",
            "x-ms-version": "2021-12-02",
        }
    assert blob_opener.calls[0][0].full_url.endswith(
        f"/executions/{EXECUTION_NAME}/broker-launch-receipt.json"
    )
    assert blob_opener.calls[-1][0].full_url.endswith(
        f"/executions/{EXECUTION_NAME}/broker-launch-receipt.signature.json"
    )


def test_storage_token_cache_refresh_and_expiry_bounds() -> None:
    policy = _policy()
    now = [float(EPOCH_NOW)]
    first = _token_body(
        policy,
        access_token="first.token",
        expires_on=str(EPOCH_NOW + 100),
    )
    second = _token_body(
        policy,
        access_token="second.token",
        expires_on=EPOCH_NOW + 3_600,
        provider_extension={"bounded": True},
    )
    opener = HttpOpener([HttpResponse(first), HttpResponse(second)])
    source = _token_source(policy, opener, clock=lambda: now[0])
    client_id = policy["identities"]["jobs"]["provision"]["client_id"]
    arguments = {
        "client_id": client_id,
        "resource": receiver.STORAGE_OAUTH_RESOURCE,
        "timeout_seconds": 10,
    }
    assert source.get_token(**arguments) == "first.token"
    assert source.get_token(**arguments) == "first.token"
    assert len(opener.calls) == 1
    now[0] += 71
    assert source.get_token(**arguments) == "second.token"
    assert len(opener.calls) == 2
    now[0] -= 1
    with pytest.raises(
        receiver.LaunchReceiptMailboxFailure,
        match="^mailbox_receiver.identity_clock_invalid$",
    ):
        source.get_token(**arguments)
    minimal = json.dumps(
        {"access_token": "minimal.token", "expires_on": EPOCH_NOW + 3_600}
    ).encode()
    assert (
        _token_source(policy, HttpOpener([HttpResponse(minimal)])).get_token(
            **arguments
        )
        == "minimal.token"
    )


@pytest.mark.parametrize(
    ("body_changes", "response_changes"),
    [
        ({"client_id": "22222222-2222-4222-8222-222222222222"}, {}),
        ({"resource": "https://management.azure.com/"}, {}),
        ({"token_type": "bearer"}, {}),
        ({"expires_on": str(EPOCH_NOW + 30)}, {}),
        ({"expires_on": str(EPOCH_NOW + 86_401)}, {}),
        ({}, {"status": 401}),
        ({}, {"status": 302}),
        ({}, {"url": "http://127.0.0.1:42356/other"}),
        ({}, {"headers": {"Content-Type": "text/plain"}}),
        (
            {},
            {"headers": {"Content-Type": ["application/json", "application/json"]}},
        ),
        (
            {},
            {
                "headers": {
                    "Content-Type": "application/json",
                    "Content-Encoding": "gzip",
                }
            },
        ),
    ],
)
def test_storage_token_endpoint_rejects_unbound_or_ambiguous_responses(
    body_changes: dict[str, Any],
    response_changes: dict[str, Any],
) -> None:
    configured = _policy()
    response = HttpResponse(_token_body(configured, **body_changes), **response_changes)
    source = _token_source(configured, HttpOpener([response]))
    with pytest.raises(receiver.LaunchReceiptMailboxFailure):
        source.get_token(
            client_id=configured["identities"]["jobs"]["provision"]["client_id"],
            resource=receiver.STORAGE_OAUTH_RESOURCE,
            timeout_seconds=10,
        )


def test_storage_token_rejects_duplicate_json_wrong_request_and_secret_errors() -> None:
    policy = _policy()
    client_id = policy["identities"]["jobs"]["provision"]["client_id"]
    duplicate = b'{"access_token":"one","access_token":"two"}'
    source = _token_source(policy, HttpOpener([HttpResponse(duplicate)]))
    with pytest.raises(
        receiver.LaunchReceiptMailboxFailure,
        match="^mailbox_receiver.identity_response_invalid$",
    ):
        source.get_token(
            client_id=client_id,
            resource=receiver.STORAGE_OAUTH_RESOURCE,
            timeout_seconds=10,
        )
    source = _token_source(policy, HttpOpener([]))
    with pytest.raises(
        receiver.LaunchReceiptMailboxFailure,
        match="^mailbox_receiver.identity_request_invalid$",
    ):
        source.get_token(
            client_id=client_id,
            resource="https://management.azure.com/",
            timeout_seconds=10,
        )
    secret = "must-not-escape-token-endpoint"
    source = _token_source(policy, HttpOpener([RuntimeError(secret)]))
    with pytest.raises(
        receiver.LaunchReceiptMailboxFailure,
        match="^mailbox_receiver.identity_unavailable$",
    ) as error:
        source.get_token(
            client_id=client_id,
            resource=receiver.STORAGE_OAUTH_RESOURCE,
            timeout_seconds=10,
        )
    assert secret not in str(error.value)


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (
            HttpResponse(b"{}", status=302),
            "mailbox_receiver.redirect_refused",
        ),
        (
            HttpResponse(b"{}", url="https://wrong.invalid/object"),
            "mailbox_receiver.http_response_invalid",
        ),
        (
            HttpResponse(
                b"{}",
                headers={"Content-Type": ["application/json", "application/json"]},
            ),
            "mailbox_receiver.http_response_invalid",
        ),
        (
            HttpResponse(
                b"{}",
                headers={"Content-Type": "text/plain", "Content-Length": "2"},
            ),
            "mailbox_receiver.response_invalid",
        ),
        (
            HttpResponse(
                b"{}",
                headers={"Content-Type": "application/json", "Content-Length": "02"},
            ),
            "mailbox_receiver.http_response_invalid",
        ),
        (
            HttpResponse(
                b"{}",
                headers={
                    "Content-Type": "application/json",
                    "Content-Encoding": "gzip",
                },
            ),
            "mailbox_receiver.response_invalid",
        ),
        (
            HttpResponse(b"x" * 65_537),
            "mailbox_receiver.http_response_too_large",
        ),
        (
            HttpResponse(b"{}", status=206),
            "mailbox_receiver.refused",
        ),
    ],
)
def test_concrete_blob_transport_rejects_redirects_metadata_size_and_206(
    monkeypatch: pytest.MonkeyPatch,
    response: HttpResponse,
    error: str,
) -> None:
    policy = _policy()
    token_opener = HttpOpener([HttpResponse(_token_body(policy))])
    transport = receiver.ManagedIdentityPrivateBlobTransport(
        _token_source(policy, token_opener),
        opener=HttpOpener([response]),
        deadline=_no_deadline,
    )
    with pytest.raises(receiver.LaunchReceiptMailboxFailure, match=f"^{error}$"):
        _receive(monkeypatch, policy, transport, Clock())


def test_concrete_transport_from_environment_and_invalid_action_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _policy()
    monkeypatch.setenv("IDENTITY_ENDPOINT", IDENTITY_ENDPOINT)
    monkeypatch.setenv("IDENTITY_HEADER", IDENTITY_HEADER)
    transport = receiver.ManagedIdentityPrivateBlobTransport.from_environment(
        policy,
        action="cleanup",
        identity_opener=HttpOpener([]),
        blob_opener=HttpOpener([]),
        deadline=_no_deadline,
    )
    assert isinstance(transport, receiver.ManagedIdentityPrivateBlobTransport)
    with pytest.raises(
        receiver.LaunchReceiptMailboxFailure,
        match="^mailbox_receiver.transport_configuration_invalid$",
    ):
        receiver.ManagedIdentityPrivateBlobTransport(
            object(),  # type: ignore[arg-type]
            opener=HttpOpener([]),
            deadline=_no_deadline,
        )
    for action in ("other", [], None):
        with pytest.raises(
            receiver.LaunchReceiptMailboxFailure,
            match="^mailbox_receiver.transport_configuration_invalid$",
        ):
            receiver.ManagedIdentityPrivateBlobTransport.from_environment(
                policy,
                action=action,  # type: ignore[arg-type]
                identity_opener=HttpOpener([]),
                blob_opener=HttpOpener([]),
                deadline=_no_deadline,
            )


def test_checked_in_dormant_policy_refuses_before_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = helpers._policy()
    transport = FakeTransport({"receipt": b"{}", "signature_envelope": b"{}"})
    with pytest.raises(
        receiver.LaunchReceiptMailboxFailure, match="^mailbox_receiver.policy_invalid$"
    ):
        _receive(monkeypatch, policy, transport, Clock())
    assert transport.requests == []


@pytest.mark.parametrize("status", (201, 202, 400, 401, 403, 409, 429, 500, 503))
def test_every_non_404_failure_refuses_without_retry(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    policy = _policy()
    objects = _mailbox_objects(policy, "provision")
    transport = FakeTransport(
        objects, {"receipt": [status], "signature_envelope": [200]}
    )
    with pytest.raises(
        receiver.LaunchReceiptMailboxFailure, match="^mailbox_receiver.refused$"
    ):
        _receive(monkeypatch, policy, transport, Clock())
    assert len(transport.requests) == 1


def test_404_polling_is_bounded_by_attempts_and_global_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _policy()
    objects = _mailbox_objects(policy, "provision")
    attempts = policy["launch_binding"]["mailbox"]["execution_receipt_handoff"][
        "retrieval"
    ]["maximum_attempts"]
    exhausted = FakeTransport(
        objects,
        {"receipt": [404] * attempts, "signature_envelope": [200]},
    )
    with pytest.raises(
        receiver.LaunchReceiptMailboxFailure, match="^mailbox_receiver.refused$"
    ):
        _receive(monkeypatch, policy, exhausted, Clock())
    assert len(exhausted.requests) == attempts

    clock = Clock()

    def advance(_request: receiver.AuthenticatedMailboxGetRequest) -> None:
        clock.value += 61

    late = FakeTransport(objects, after_request=advance)
    with pytest.raises(
        receiver.LaunchReceiptMailboxFailure, match="^mailbox_receiver.deadline$"
    ):
        _receive(monkeypatch, policy, late, clock)
    assert len(late.requests) == 1


def test_retry_budget_has_a_40_request_aggregate_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _policy()
    objects = _mailbox_objects(policy, "provision")
    transport = FakeTransport(
        objects,
        {
            "receipt": [404] * 19 + [200],
            "signature_envelope": [404] * 20,
        },
    )
    monkeypatch.setenv(launch.EXECUTION_NAME_ENV, EXECUTION_NAME)
    with pytest.raises(
        receiver.LaunchReceiptMailboxFailure, match="^mailbox_receiver.refused$"
    ):
        receiver.receive_broker_launch_receipt(
            policy,
            action="provision",
            transport=transport,
            monotonic=Clock(),
            sleeper=lambda _seconds: None,
            wall_clock=lambda: OBSERVED_AT,
        )
    assert len(transport.requests) == 40


def test_unsealed_or_reused_response_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _policy()
    objects = _mailbox_objects(policy, "provision")

    class Unsealed:
        def get_exact_private_blob(
            self, _request: receiver.AuthenticatedMailboxGetRequest
        ) -> receiver.AuthenticatedMailboxGetResponse:
            return object.__new__(receiver.AuthenticatedMailboxGetResponse)

    with pytest.raises(
        receiver.LaunchReceiptMailboxFailure,
        match="^mailbox_receiver.response_invalid$",
    ):
        _receive(monkeypatch, policy, Unsealed(), Clock())

    old_request = receiver._sealed_request(
        document=policy,
        action="provision",
        object_kind="receipt",
        execution_name=EXECUTION_NAME,
        uri=launch._execution_receipt_uris(policy, "provision", EXECUTION_NAME)[0],
        maximum_bytes=65_536,
        attempt_number=2,
        timeout_seconds=10,
    )
    reused = receiver.AuthenticatedMailboxGetResponse._from_reviewed_managed_identity_private_blob_adapter(
        request=old_request,
        final_uri=old_request.uri,
        redirect_count=0,
        status_code=200,
        content_type_values=("application/json",),
        content_encoding_values=(),
        content_length_values=(str(len(objects["receipt"])),),
        body=objects["receipt"],
    )

    class Reused:
        def get_exact_private_blob(
            self, _request: receiver.AuthenticatedMailboxGetRequest
        ) -> receiver.AuthenticatedMailboxGetResponse:
            return reused

    with pytest.raises(
        receiver.LaunchReceiptMailboxFailure,
        match="^mailbox_receiver.response_invalid$",
    ):
        _receive(monkeypatch, policy, Reused(), Clock())


@pytest.mark.parametrize("object_kind", ("receipt", "signature_envelope"))
def test_tampered_object_fails_closed_without_exposing_contents(
    monkeypatch: pytest.MonkeyPatch, object_kind: str
) -> None:
    policy = _policy()
    objects = _mailbox_objects(policy, "provision")
    objects[object_kind] += b" private-secret"
    with pytest.raises(
        receiver.LaunchReceiptMailboxFailure,
        match="^mailbox_receiver.authentication$",
    ) as error:
        _receive(monkeypatch, policy, FakeTransport(objects), Clock())
    assert "private-secret" not in str(error.value)
    assert len(str(error.value)) < 64


def test_execution_identity_change_during_retrieval_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _policy()
    objects = _mailbox_objects(policy, "provision")

    def change_environment(request: receiver.AuthenticatedMailboxGetRequest) -> None:
        if request.object_kind == "signature_envelope":
            monkeypatch.setenv(launch.EXECUTION_NAME_ENV, "execution-2")

    transport = FakeTransport(objects, after_request=change_environment)
    with pytest.raises(
        receiver.LaunchReceiptMailboxFailure,
        match="^mailbox_receiver.execution_identity_invalid$",
    ):
        _receive(monkeypatch, policy, transport, Clock())


def test_configuration_and_transport_failures_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _policy()
    objects = _mailbox_objects(policy, "provision")

    class BrokenTransport:
        def get_exact_private_blob(
            self, _request: receiver.AuthenticatedMailboxGetRequest
        ) -> receiver.AuthenticatedMailboxGetResponse:
            raise RuntimeError("private transport details")

    with pytest.raises(
        receiver.LaunchReceiptMailboxFailure,
        match="^mailbox_receiver.transport_unavailable$",
    ) as error:
        _receive(monkeypatch, policy, BrokenTransport(), Clock())
    assert "private transport details" not in str(error.value)
    monkeypatch.delenv(launch.EXECUTION_NAME_ENV)
    with pytest.raises(
        receiver.LaunchReceiptMailboxFailure,
        match="^mailbox_receiver.execution_identity_invalid$",
    ):
        receiver.receive_broker_launch_receipt(
            policy, action="provision", transport=FakeTransport(objects)
        )
    secret = "must-not-escape-wall-clock"
    with pytest.raises(
        receiver.LaunchReceiptMailboxFailure, match="^mailbox_receiver.clock_invalid$"
    ) as error:
        _receive(
            monkeypatch,
            policy,
            FakeTransport(objects),
            Clock(),
            wall_clock=lambda: (_ for _ in ()).throw(RuntimeError(secret)),
        )
    assert secret not in str(error.value)


def test_default_clock_is_exact_utc_second(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = _policy()
    objects = _mailbox_objects(policy, "provision")
    monkeypatch.setenv(launch.EXECUTION_NAME_ENV, EXECUTION_NAME)
    result = receiver.receive_broker_launch_receipt(
        policy, action="provision", transport=FakeTransport(objects)
    )
    assert (result.observed_at.tzinfo, result.observed_at.microsecond) == (UTC, 0)
