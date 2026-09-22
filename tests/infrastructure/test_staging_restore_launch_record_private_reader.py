from __future__ import annotations

import json
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event
from typing import Any, Callable

import pytest

from scripts import read_staging_restore_launch_record as ingress
from scripts import receive_staging_broker_launch_receipt as receiver
from scripts import staging_restore_launch_material as launch_material
from scripts import validate_staging_restore_launch_binding as launch
from scripts import validate_staging_restore_runtime_binding as binding

from tests.infrastructure import (
    test_staging_broker_launch_receipt_mailbox as mailbox_helpers,
)
from tests.infrastructure import test_staging_restore_launch_material as launch_helpers
from tests.infrastructure import test_staging_restore_runtime_binding as helpers


class LaunchTransport:
    def __init__(
        self,
        *,
        body: bytes = launch_helpers.LAUNCH_BYTES,
        status: int = 200,
        effect: Callable[[receiver.AuthenticatedMailboxGetRequest], None] | None = None,
        response_changes: dict[str, Any] | None = None,
    ) -> None:
        self.body = body
        self.status = status
        self.effect = effect
        self.response_changes = response_changes or {}
        self.requests: list[receiver.AuthenticatedMailboxGetRequest] = []
        self.responses: list[receiver.AuthenticatedMailboxGetResponse] = []

    def get_exact_private_blob(
        self, request: receiver.AuthenticatedMailboxGetRequest
    ) -> receiver.AuthenticatedMailboxGetResponse:
        self.requests.append(request)
        body = self.body
        arguments: dict[str, Any] = {
            "request": request,
            "final_uri": request.uri,
            "redirect_count": 0,
            "status_code": self.status,
            "content_type_values": ("application/json; charset=UTF-8",),
            "content_encoding_values": ("identity",),
            "content_length_values": (str(len(body)),),
            "content_range_values": (),
            "transfer_encoding_values": (),
            "body": body,
        }
        arguments.update(self.response_changes)
        result = receiver.AuthenticatedMailboxGetResponse._from_reviewed_managed_identity_private_blob_adapter(
            **arguments
        )
        self.responses.append(result)
        if self.effect is not None:
            self.effect(request)
        return result


def _case(
    monkeypatch: pytest.MonkeyPatch,
    *,
    transport: Any | None = None,
) -> tuple[
    dict[str, Any],
    launch.AuthenticatedBrokerLaunchReceipt,
    Any,
    ingress.ManagedIdentityPrivateLaunchRecordReader,
]:
    policy = helpers._configured_policy()
    material, _sources, _receipt_transport = launch_helpers._receive(
        monkeypatch, policy=policy
    )
    selected = transport or LaunchTransport()
    reader = ingress.ManagedIdentityPrivateLaunchRecordReader(selected)
    return policy, material.authenticated_receipt, selected, reader


def _read(
    reader: ingress.ManagedIdentityPrivateLaunchRecordReader,
    policy: dict[str, Any],
    receipt: launch.AuthenticatedBrokerLaunchReceipt,
) -> bytes:
    return reader.read_launch_record(
        policy=policy,
        authenticated_receipt=receipt,
    )


def test_reads_one_exact_receipt_derived_provision_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, receipt, transport, reader = _case(monkeypatch)
    assert _read(reader, policy, receipt) == launch_helpers.LAUNCH_BYTES
    assert len(transport.requests) == 1
    request = transport.requests[0]
    retrieval = policy["launch_binding"]["mailbox"]["launch_record_retrieval"]
    mailbox = policy["launch_binding"]["mailbox"]
    identity = policy["identities"]["jobs"]["provision"]
    assert request._is_sealed()
    assert request.action == "provision"
    assert request.object_kind == "launch_record"
    assert request.uri == receipt.launch_record_uri
    assert request.method == "GET"
    assert request.oauth_resource == mailbox["oauth_resource"]
    assert request.account_resource_id == mailbox["account_resource_id"]
    assert (
        request.container_resource_id == mailbox["container_resource_ids"]["provision"]
    )
    assert request.managed_identity_resource_id == identity["resource_id"]
    assert request.managed_identity_client_id == identity["client_id"]
    assert (
        request.private_endpoint_resource_id == mailbox["private_endpoint_resource_id"]
    )
    assert request.service_api_version == retrieval["service_api_version"]
    assert request.accept == "application/json"
    assert request.optional_charset == "utf-8"
    assert request.accept_encoding == "identity"
    assert request.content_length_required is True
    assert request.content_range_permitted is False
    assert request.transfer_encoding_permitted is False
    assert request.redirects_permitted is False
    assert request.maximum_bytes == 65_536
    assert request.attempt_number == 1
    assert request.timeout_seconds == 10


def test_reader_fills_only_the_launch_material_dependency_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = helpers._configured_policy()
    launch_helpers._configure_trusted_root(policy)
    receipt_transport = mailbox_helpers.FakeTransport(
        launch_helpers._mailbox_objects(policy)
    )
    launch_transport = LaunchTransport()
    reader = ingress.ManagedIdentityPrivateLaunchRecordReader(launch_transport)
    runtime_calls: list[dict[str, Any]] = []

    def runtime_reader(**kwargs: Any) -> Any:
        runtime_calls.append(kwargs)
        return (
            launch_material.AuthenticatedRuntimeRecordMaterial._from_authenticated_read(
                record_bytes=launch_helpers.RUNTIME_BYTES,
                trusted_root_bytes=launch_helpers.TRUSTED_ROOT_BYTES,
                signature_verification=launch_helpers._signature_proof(policy),
            )
        )

    timeline = mailbox_helpers.Clock()
    monkeypatch.setenv(launch.EXECUTION_NAME_ENV, mailbox_helpers.EXECUTION_NAME)
    result = launch_material.receive_provision_launch_material(
        policy,
        dependencies=launch_material.LaunchMaterialDependencies(
            receipt_transport=receipt_transport,
            read_launch_record=reader.read_launch_record,
            read_and_authenticate_runtime_record=runtime_reader,
        ),
        clock=lambda: launch_helpers.NOW,
        monotonic=timeline,
        sleeper=timeline.sleep,
    )
    assert result.launch_record_bytes == launch_helpers.LAUNCH_BYTES
    assert len(launch_transport.requests) == 1
    assert len(runtime_calls) == 1
    assert [request.object_kind for request in receipt_transport.requests] == [
        "receipt",
        "signature_envelope",
    ]


def test_concrete_transport_uses_one_uami_token_and_one_exact_blob_get(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, receipt, _transport, _reader = _case(monkeypatch)
    monkeypatch.setenv("IDENTITY_ENDPOINT", mailbox_helpers.IDENTITY_ENDPOINT)
    monkeypatch.setenv("IDENTITY_HEADER", mailbox_helpers.IDENTITY_HEADER)
    identity_opener = mailbox_helpers.HttpOpener(
        [mailbox_helpers.HttpResponse(mailbox_helpers._token_body(policy))]
    )
    blob_opener = mailbox_helpers.HttpOpener(
        [mailbox_helpers.HttpResponse(launch_helpers.LAUNCH_BYTES)]
    )
    transport = receiver.ManagedIdentityPrivateBlobTransport.from_provision_launch_record_environment(
        policy,
        identity_opener=identity_opener,
        blob_opener=blob_opener,
        epoch_clock=lambda: mailbox_helpers.EPOCH_NOW,
        monotonic=mailbox_helpers.Clock(),
        deadline=mailbox_helpers._no_deadline,
    )
    result = _read(
        ingress.ManagedIdentityPrivateLaunchRecordReader(transport), policy, receipt
    )
    assert result == launch_helpers.LAUNCH_BYTES
    assert len(identity_opener.calls) == 1
    identity_request, identity_timeout = identity_opener.calls[0]
    parsed_identity = urllib.parse.urlsplit(identity_request.full_url)
    assert identity_timeout == 10
    assert (
        f"{parsed_identity.scheme}://{parsed_identity.netloc}{parsed_identity.path}"
        == mailbox_helpers.IDENTITY_ENDPOINT
    )
    assert urllib.parse.parse_qs(parsed_identity.query) == {
        "api-version": ["2019-08-01"],
        "client_id": [policy["identities"]["jobs"]["provision"]["client_id"]],
        "resource": [receiver.STORAGE_OAUTH_RESOURCE],
    }
    assert len(blob_opener.calls) == 1
    blob_request, blob_timeout = blob_opener.calls[0]
    assert blob_timeout == 10
    assert blob_request.full_url == receipt.launch_record_uri
    assert blob_request.method == "GET"
    assert {name.lower(): value for name, value in blob_request.header_items()} == {
        "accept": "application/json",
        "accept-encoding": "identity",
        "authorization": f"Bearer {mailbox_helpers.TOKEN}",
        "x-ms-version": "2021-12-02",
    }


@pytest.mark.parametrize(
    "headers",
    (
        {"Content-Type": "application/json"},
        {
            "Content-Type": "application/json",
            "Content-Length": [str(len(launch_helpers.LAUNCH_BYTES))] * 2,
        },
        {
            "Content-Type": "application/json",
            "Content-Length": str(len(launch_helpers.LAUNCH_BYTES)),
            "Content-Range": "bytes 0-17/18",
        },
        {
            "Content-Type": "application/json",
            "Content-Length": str(len(launch_helpers.LAUNCH_BYTES)),
            "Transfer-Encoding": "chunked",
        },
    ),
)
def test_launch_response_requires_unambiguous_content_length_framing(
    monkeypatch: pytest.MonkeyPatch, headers: dict[str, Any]
) -> None:
    policy, receipt, _transport, _reader = _case(monkeypatch)
    monkeypatch.setenv("IDENTITY_ENDPOINT", mailbox_helpers.IDENTITY_ENDPOINT)
    monkeypatch.setenv("IDENTITY_HEADER", mailbox_helpers.IDENTITY_HEADER)
    transport = receiver.ManagedIdentityPrivateBlobTransport.from_provision_launch_record_environment(
        policy,
        identity_opener=mailbox_helpers.HttpOpener(
            [mailbox_helpers.HttpResponse(mailbox_helpers._token_body(policy))]
        ),
        blob_opener=mailbox_helpers.HttpOpener(
            [
                mailbox_helpers.HttpResponse(
                    launch_helpers.LAUNCH_BYTES,
                    headers=headers,
                )
            ]
        ),
        epoch_clock=lambda: mailbox_helpers.EPOCH_NOW,
        deadline=mailbox_helpers._no_deadline,
    )
    with pytest.raises(
        ingress.PrivateLaunchRecordFailure,
        match="^launch_record_ingress.transport_failed$",
    ):
        _read(
            ingress.ManagedIdentityPrivateLaunchRecordReader(transport),
            policy,
            receipt,
        )


@pytest.mark.parametrize(
    ("response", "failure"),
    (
        (
            mailbox_helpers.HttpResponse(
                launch_helpers.LAUNCH_BYTES,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": f"0{len(launch_helpers.LAUNCH_BYTES)}",
                },
            ),
            "transport_failed",
        ),
        (
            mailbox_helpers.HttpResponse(
                launch_helpers.LAUNCH_BYTES,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": "1",
                },
            ),
            "transport_failed",
        ),
        (
            mailbox_helpers.HttpResponse(
                launch_helpers.LAUNCH_BYTES,
                headers={
                    "Content-Type": "text/plain",
                    "Content-Length": str(len(launch_helpers.LAUNCH_BYTES)),
                },
            ),
            "transport_failed",
        ),
        (
            mailbox_helpers.HttpResponse(
                launch_helpers.LAUNCH_BYTES,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(len(launch_helpers.LAUNCH_BYTES)),
                    "Content-Encoding": "gzip",
                },
            ),
            "transport_failed",
        ),
        (
            mailbox_helpers.HttpResponse(
                launch_helpers.LAUNCH_BYTES,
                url="https://restorelaunch.blob.core.windows.net/wrong/object.json",
            ),
            "transport_failed",
        ),
        (
            mailbox_helpers.HttpResponse(launch_helpers.LAUNCH_BYTES, status=302),
            "transport_failed",
        ),
        (
            mailbox_helpers.HttpResponse(launch_helpers.LAUNCH_BYTES, status=206),
            "response_invalid",
        ),
        (mailbox_helpers.HttpResponse(b"x" * 65_537), "transport_failed"),
    ),
)
def test_concrete_transport_refuses_ambiguous_or_inexact_launch_responses(
    monkeypatch: pytest.MonkeyPatch,
    response: mailbox_helpers.HttpResponse,
    failure: str,
) -> None:
    policy, receipt, _transport, _reader = _case(monkeypatch)
    monkeypatch.setenv("IDENTITY_ENDPOINT", mailbox_helpers.IDENTITY_ENDPOINT)
    monkeypatch.setenv("IDENTITY_HEADER", mailbox_helpers.IDENTITY_HEADER)
    transport = receiver.ManagedIdentityPrivateBlobTransport.from_provision_launch_record_environment(
        policy,
        identity_opener=mailbox_helpers.HttpOpener(
            [mailbox_helpers.HttpResponse(mailbox_helpers._token_body(policy))]
        ),
        blob_opener=mailbox_helpers.HttpOpener([response]),
        epoch_clock=lambda: mailbox_helpers.EPOCH_NOW,
        deadline=mailbox_helpers._no_deadline,
    )
    with pytest.raises(
        ingress.PrivateLaunchRecordFailure,
        match=rf"^launch_record_ingress.{failure}$",
    ):
        _read(
            ingress.ManagedIdentityPrivateLaunchRecordReader(transport),
            policy,
            receipt,
        )


@pytest.mark.parametrize(
    ("status", "body", "failure"),
    (
        (201, launch_helpers.LAUNCH_BYTES, "response_invalid"),
        (404, b"private response must not escape", "response_invalid"),
        (500, b"secret remote details", "response_invalid"),
        (200, b"wrong digest", "response_invalid"),
        (200, b"", "transport_failed"),
    ),
)
def test_non_success_or_non_exact_body_refuses_once_without_contents(
    monkeypatch: pytest.MonkeyPatch, status: int, body: bytes, failure: str
) -> None:
    transport = LaunchTransport(status=status, body=body)
    policy, receipt, transport, reader = _case(monkeypatch, transport=transport)
    with pytest.raises(
        ingress.PrivateLaunchRecordFailure,
        match=rf"^launch_record_ingress.{failure}$",
    ) as error:
        _read(reader, policy, receipt)
    assert len(transport.requests) == 1
    if status != 200:
        assert transport.responses[0].body == b""
    assert "secret" not in str(error.value)
    assert "private" not in str(error.value)
    assert len(str(error.value)) < 64


def test_concrete_transport_suppresses_non_success_wire_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, receipt, _transport, _reader = _case(monkeypatch)
    monkeypatch.setenv("IDENTITY_ENDPOINT", mailbox_helpers.IDENTITY_ENDPOINT)
    monkeypatch.setenv("IDENTITY_HEADER", mailbox_helpers.IDENTITY_HEADER)
    identity_opener = mailbox_helpers.HttpOpener(
        [mailbox_helpers.HttpResponse(mailbox_helpers._token_body(policy))]
    )
    secret = b"must-not-escape-private-wire-body"
    blob_opener = mailbox_helpers.HttpOpener([mailbox_helpers._http_error(403, secret)])
    transport = receiver.ManagedIdentityPrivateBlobTransport.from_provision_launch_record_environment(
        policy,
        identity_opener=identity_opener,
        blob_opener=blob_opener,
        epoch_clock=lambda: mailbox_helpers.EPOCH_NOW,
        deadline=mailbox_helpers._no_deadline,
    )
    snapshot = ingress._authority_snapshot(policy, receipt)
    request = receiver._sealed_provision_launch_record_request(
        document=policy,
        authenticated_receipt=receipt,
        authority_receipt_sha256=snapshot.receipt_sha256,
        authority_execution_name=snapshot.execution_name,
        authority_launch_record_uri=snapshot.launch_record_uri,
        authority_launch_record_sha256=snapshot.launch_record_sha256,
    )
    response = transport.get_exact_private_blob(request)
    assert response.status_code == 403
    assert response.body == b""
    assert len(identity_opener.calls) == len(blob_opener.calls) == 1
    refusing_transport = receiver.ManagedIdentityPrivateBlobTransport.from_provision_launch_record_environment(
        policy,
        identity_opener=mailbox_helpers.HttpOpener(
            [mailbox_helpers.HttpResponse(mailbox_helpers._token_body(policy))]
        ),
        blob_opener=mailbox_helpers.HttpOpener(
            [mailbox_helpers._http_error(403, secret)]
        ),
        epoch_clock=lambda: mailbox_helpers.EPOCH_NOW,
        deadline=mailbox_helpers._no_deadline,
    )
    with pytest.raises(
        ingress.PrivateLaunchRecordFailure,
        match="^launch_record_ingress.response_invalid$",
    ) as error:
        _read(
            ingress.ManagedIdentityPrivateLaunchRecordReader(refusing_transport),
            policy,
            receipt,
        )
    assert secret.decode() not in str(error.value)


def test_response_sealed_for_an_equal_but_different_request_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, receipt, _transport, _reader = _case(monkeypatch)
    snapshot = ingress._authority_snapshot(policy, receipt)
    other_request = receiver._sealed_provision_launch_record_request(
        document=policy,
        authenticated_receipt=receipt,
        authority_receipt_sha256=snapshot.receipt_sha256,
        authority_execution_name=snapshot.execution_name,
        authority_launch_record_uri=snapshot.launch_record_uri,
        authority_launch_record_sha256=snapshot.launch_record_sha256,
    )
    reused = receiver.AuthenticatedMailboxGetResponse._from_reviewed_managed_identity_private_blob_adapter(
        request=other_request,
        final_uri=other_request.uri,
        redirect_count=0,
        status_code=200,
        content_type_values=("application/json",),
        content_encoding_values=("identity",),
        content_length_values=(str(len(launch_helpers.LAUNCH_BYTES)),),
        body=launch_helpers.LAUNCH_BYTES,
    )

    class ReusedTransport:
        calls = 0

        def get_exact_private_blob(
            self, request: receiver.AuthenticatedMailboxGetRequest
        ) -> receiver.AuthenticatedMailboxGetResponse:
            del request
            self.calls += 1
            return reused

    transport = ReusedTransport()
    with pytest.raises(
        ingress.PrivateLaunchRecordFailure,
        match="^launch_record_ingress.response_invalid$",
    ):
        _read(
            ingress.ManagedIdentityPrivateLaunchRecordReader(transport),
            policy,
            receipt,
        )
    assert transport.calls == 1


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("action", "cleanup"),
        ("execution_name", "execution-2"),
        ("launch_record_uri", "https://attacker.invalid/object.json"),
        ("launch_record_sha256", f"sha256:{'f' * 64}"),
        ("receipt_sha256", f"sha256:{'e' * 64}"),
        ("signature_envelope_sha256", "not-a-digest"),
    ),
)
def test_mutated_sealed_receipt_refuses_before_io(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    policy, receipt, transport, reader = _case(monkeypatch)
    object.__setattr__(receipt, field, value)
    with pytest.raises(ingress.PrivateLaunchRecordFailure):
        _read(reader, policy, receipt)
    assert transport.requests == []


@pytest.mark.parametrize(
    "variant",
    (
        "noncanonical-bytes",
        "schema",
        "selected-job",
        "execution-resource",
        "receipt-route",
        "signature-route",
        "runtime-reference",
    ),
)
def test_receipt_bytes_and_authority_projections_are_rechecked_pre_io(
    monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    policy, receipt, transport, reader = _case(monkeypatch)
    if variant in {
        "noncanonical-bytes",
        "schema",
        "selected-job",
        "execution-resource",
    }:
        signed = json.loads(receipt.receipt_bytes)
        if variant == "schema":
            signed["schema_version"] = 2
        elif variant == "selected-job":
            signed["selected_job_resource_id"] = policy["runtime"]["jobs"]["cleanup"][
                "resource_id"
            ]
        elif variant == "execution-resource":
            signed["execution_resource_id"] += "-other"
        raw = binding._canonical(signed)
        if variant == "noncanonical-bytes":
            raw += b"\n"
        object.__setattr__(receipt, "receipt_bytes", raw)
        object.__setattr__(receipt, "receipt_sha256", binding._digest(raw))
    elif variant == "receipt-route":
        object.__setattr__(receipt, "receipt_uri", receipt.receipt_uri + ".other")
    elif variant == "signature-route":
        object.__setattr__(
            receipt,
            "signature_envelope_uri",
            receipt.signature_envelope_uri + ".other",
        )
    else:
        changed = dict(receipt.runtime_reference)
        changed["record_sha256"] = f"sha256:{'e' * 64}"
        object.__setattr__(receipt, "runtime_reference", changed)
    with pytest.raises(
        ingress.PrivateLaunchRecordFailure,
        match="^launch_record_ingress.receipt_invalid$",
    ):
        _read(reader, policy, receipt)
    assert transport.requests == []


@pytest.mark.parametrize(
    "uri",
    (
        "https://user@restorelaunch.blob.core.windows.net/restore-provision-launches-v1/object.json",
        "https://restorelaunch.blob.core.windows.net:443/restore-provision-launches-v1/object.json",
        "https://restorelaunch.blob.core.windows.net/restore-provision-launches-v1/object.json?x=1",
        "https://restorelaunch.blob.core.windows.net/restore-provision-launches-v1/object.json#x",
        "https://restorelaunch.blob.core.windows.net/restore-provision-launches-v1/%2e%2e/object.json",
        "https://restorelaunch.blob.core.windows.net/restore-provision-launches-v1/../object.json",
        "https://restorelaunch.blob.core.windows.net//restore-provision-launches-v1/object.json",
    ),
)
def test_launch_uri_variants_never_reach_transport(
    monkeypatch: pytest.MonkeyPatch, uri: str
) -> None:
    policy, receipt, transport, reader = _case(monkeypatch)
    object.__setattr__(receipt, "launch_record_uri", uri)
    with pytest.raises(
        ingress.PrivateLaunchRecordFailure,
        match="^launch_record_ingress.receipt_invalid$",
    ):
        _read(reader, policy, receipt)
    assert transport.requests == []


def test_mutation_between_checkpoint_and_request_factory_cannot_redirect_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, receipt, transport, reader = _case(monkeypatch)
    original = receiver._sealed_provision_launch_record_request

    def mutate_before_factory(**kwargs: Any) -> Any:
        replacement = f"sha256:{'f' * 64}"
        object.__setattr__(
            receipt,
            "launch_record_uri",
            "https://restorelaunch.blob.core.windows.net/"
            f"restore-provision-launches-v1/{replacement[7:]}.json",
        )
        object.__setattr__(receipt, "launch_record_sha256", replacement)
        return original(**kwargs)

    monkeypatch.setattr(
        receiver, "_sealed_provision_launch_record_request", mutate_before_factory
    )
    with pytest.raises(
        ingress.PrivateLaunchRecordFailure,
        match="^launch_record_ingress.request_invalid$",
    ):
        _read(reader, policy, receipt)
    assert transport.requests == []


def test_independent_expected_request_check_prevents_bad_factory_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, receipt, transport, reader = _case(monkeypatch)
    original = receiver._sealed_provision_launch_record_request

    def bad_factory(**kwargs: Any) -> Any:
        request = original(**kwargs)
        object.__setattr__(request, "uri", "https://attacker.invalid/object.json")
        return request

    monkeypatch.setattr(
        receiver, "_sealed_provision_launch_record_request", bad_factory
    )
    with pytest.raises(
        ingress.PrivateLaunchRecordFailure,
        match="^launch_record_ingress.request_invalid$",
    ):
        _read(reader, policy, receipt)
    assert transport.requests == []


@pytest.mark.parametrize("mutation", ("receipt", "execution"))
def test_authority_and_execution_are_rechecked_after_io(
    monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    policy, receipt, _transport, _reader = _case(monkeypatch)

    def mutate(_request: receiver.AuthenticatedMailboxGetRequest) -> None:
        if mutation == "receipt":
            object.__setattr__(receipt, "launch_record_sha256", f"sha256:{'f' * 64}")
        else:
            monkeypatch.setenv(launch.EXECUTION_NAME_ENV, "execution-2")

    transport = LaunchTransport(effect=mutate)
    reader = ingress.ManagedIdentityPrivateLaunchRecordReader(transport)
    with pytest.raises(ingress.PrivateLaunchRecordFailure):
        _read(reader, policy, receipt)
    assert len(transport.requests) == 1
    assert transport.requests[0].uri.startswith(
        "https://restorelaunch.blob.core.windows.net/"
    )


def test_reader_is_one_shot_even_when_calls_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, receipt, _transport, _reader = _case(monkeypatch)
    entered = Event()
    release = Event()

    class BlockingTransport(LaunchTransport):
        def get_exact_private_blob(
            self, request: receiver.AuthenticatedMailboxGetRequest
        ) -> receiver.AuthenticatedMailboxGetResponse:
            entered.set()
            assert release.wait(timeout=2)
            return super().get_exact_private_blob(request)

    transport = BlockingTransport()
    reader = ingress.ManagedIdentityPrivateLaunchRecordReader(transport)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_read, reader, policy, receipt)
        assert entered.wait(timeout=2)
        second = pool.submit(_read, reader, policy, receipt)
        with pytest.raises(
            ingress.PrivateLaunchRecordFailure,
            match="^launch_record_ingress.already_used$",
        ):
            second.result(timeout=2)
        release.set()
        assert first.result(timeout=2) == launch_helpers.LAUNCH_BYTES
    assert len(transport.requests) == 1
    with pytest.raises(
        ingress.PrivateLaunchRecordFailure,
        match="^launch_record_ingress.already_used$",
    ):
        _read(reader, policy, receipt)


def test_policy_and_transport_failures_are_bounded_and_pre_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked_in = helpers._policy()
    _configured, receipt, _transport, _reader = _case(monkeypatch)
    transport = LaunchTransport()
    reader = ingress.ManagedIdentityPrivateLaunchRecordReader(transport)
    with pytest.raises(
        ingress.PrivateLaunchRecordFailure,
        match="^launch_record_ingress.policy_invalid$",
    ):
        _read(reader, checked_in, receipt)
    assert transport.requests == []
    with pytest.raises(
        ingress.PrivateLaunchRecordFailure,
        match="^launch_record_ingress.already_used$",
    ):
        _read(reader, _configured, receipt)

    class BrokenTransport:
        def get_exact_private_blob(
            self, request: receiver.AuthenticatedMailboxGetRequest
        ) -> receiver.AuthenticatedMailboxGetResponse:
            del request
            raise RuntimeError("must-not-escape-private-transport")

    policy, receipt, _transport, _reader = _case(monkeypatch)
    broken_reader = ingress.ManagedIdentityPrivateLaunchRecordReader(BrokenTransport())
    with pytest.raises(
        ingress.PrivateLaunchRecordFailure,
        match="^launch_record_ingress.transport_failed$",
    ) as error:
        _read(broken_reader, policy, receipt)
    assert "must-not-escape" not in str(error.value)
    with pytest.raises(
        ingress.PrivateLaunchRecordFailure,
        match="^launch_record_ingress.already_used$",
    ):
        _read(broken_reader, policy, receipt)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("maximum_object_get_requests", 2),
        ("polling_permitted", True),
        ("listing_permitted", True),
        ("redirects_permitted", True),
        ("private_endpoint_required", False),
        ("content_length_required", False),
        ("content_range_permitted", True),
        ("transfer_encoding_permitted", True),
        ("managed_identity_client_id_source", "caller"),
    ),
)
def test_policy_pins_the_single_exact_private_read_contract(
    field: str, value: Any
) -> None:
    policy = helpers._configured_policy()
    retrieval = policy["launch_binding"]["mailbox"]["launch_record_retrieval"]
    assert retrieval["maximum_object_get_requests"] == 1
    assert retrieval["maximum_managed_identity_token_requests"] == 1
    assert retrieval["maximum_total_http_requests"] == 2
    assert retrieval["managed_identity_resource_id_source"] == (
        "identities.jobs.provision.resource_id"
    )
    assert retrieval["managed_identity_client_id_source"] == (
        "identities.jobs.provision.client_id"
    )
    assert retrieval["oauth_resource_source"] == (
        "launch_binding.mailbox.oauth_resource"
    )
    changed = deepcopy(policy)
    changed["launch_binding"]["mailbox"]["launch_record_retrieval"][field] = value
    with pytest.raises(binding.RuntimeBindingFailure):
        binding.validate_runtime_binding_policy(changed, executable=True)
