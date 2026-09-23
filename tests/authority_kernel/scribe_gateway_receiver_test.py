"""Native facade and transport reject scope injection and unconfirmed responses."""

from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.routes.scribe_capture import get_admission_client_factory
from app.services import scribe_capture_gateway as gateway
from app.services.scribe_capture_admission import AdmittedCaptureScope
from app.services.scribe_workflow import (
    CaptureTranscriptionStatus,
    DisabledScribeWorkflowClient,
    HttpScribeWorkflowClient,
    ScribeWorkflowError,
    SessionCreate,
)

from tests.authority_kernel.scribe_capture_gateway_test import KINDS, pair
from tests.authority_kernel.scribe_capture_receiver_test import client, headers
from tests.services.scribe_workflow_test import StaticCredential

PATH = "/service/v1/agent/scribe/capture-operations"


def scope():
    return AdmittedCaptureScope(
        operation_ref="synthetic-admission",
        tenant_id=uuid4(),
        session_id=uuid4(),
        session_request=SessionCreate(
            appointment_id="appointment-synthetic",
            practitioner_id="doctor-synthetic",
            patient_id="patient-synthetic",
            consent_record_id=uuid4(),
            modality="in_person",
        ),
    )


def body():
    return {
        "binding_ref": "scribe_capture_binding_01j00000000000000000000000",
        "workspace_id": str(uuid4()),
        "request_nonce": str(uuid4()),
        "operation": {"kind": "capabilities"},
    }


def request_headers():
    return {**headers(), "Idempotency-Key": uuid4().hex}


@pytest.mark.parametrize(
    "mutation",
    [
        "session",
        "tenant",
        "nested_identity",
        "unknown",
        "path",
        "oversized",
        "key",
        "truthy",
    ],
)
def test_invalid_native_requests_do_not_reach_gateway(monkeypatch, mutation):
    payload = body()
    request = request_headers()
    if mutation in {"session", "tenant"}:
        payload[mutation + "_id"] = str(uuid4())
    elif mutation == "nested_identity":
        payload["operation"]["patient_id"] = "private_sentinel"
    elif mutation == "unknown":
        payload["operation"]["kind"] = "private_sentinel"
    elif mutation == "path":
        payload["operation"] = {
            "kind": "chunk_receipt",
            "object_id": "../private_sentinel",
        }
    elif mutation == "oversized":
        payload["extra"] = "private_sentinel" * 200
    elif mutation == "key":
        request.pop("Idempotency-Key")
    else:
        payload["operation"] = {
            "kind": "renew_lease",
            "lease_id": str(uuid4()),
            "payload": {"expected_generation": True},
        }
    monkeypatch.setattr(
        gateway, "execute_capture_operation", lambda **_: pytest.fail("gateway reached")
    )
    response = client().post(PATH, headers=request, json=payload)
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert "private_sentinel" not in response.text


def test_disabled_native_facade_never_constructs_client():
    connection = client(enabled=False)
    assert isinstance(connection.app, FastAPI)
    connection.app.dependency_overrides[get_admission_client_factory] = lambda: (
        lambda: pytest.fail("client constructed")
    )
    assert connection.post(PATH, content="private_sentinel").status_code == 503


def test_native_facade_preserves_nonce_and_closes_client(monkeypatch):
    payload = body()
    closed = []

    class LocalClient(DisabledScribeWorkflowClient):
        async def close(self):
            closed.append(True)

    connection = client()
    assert isinstance(connection.app, FastAPI)
    connection.app.dependency_overrides[get_admission_client_factory] = lambda: (
        LocalClient
    )

    async def execute(**kwargs):
        assert kwargs["operation"].kind == "capabilities"
        return pair("capabilities", scope())[1]

    monkeypatch.setattr(gateway, "execute_capture_operation", execute)
    response = connection.post(PATH, headers=request_headers(), json=payload)
    assert response.status_code == 200
    assert response.json()["request_nonce"] == payload["request_nonce"]
    assert response.json()["workspace_id"] == payload["workspace_id"]
    assert response.headers["x-cosmaneura-capture-version"] == "1"
    assert "mctx_" not in response.text and closed == [True]


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (gateway.CaptureOperationUnavailable, 409),
        (gateway.CaptureReceiptInvalid, 409),
        (gateway.CaptureOperationUnconfirmed, 503),
    ],
)
def test_native_errors_never_disclose_remote_content(monkeypatch, error, code):
    async def reject(**_):
        raise error("private_sentinel")

    monkeypatch.setattr(gateway, "execute_capture_operation", reject)
    response = client().post(PATH, headers=request_headers(), json=body())
    assert response.status_code == code
    assert "private_sentinel" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_native_transport_uses_closed_routes_and_only_server_selected_tenant_session(
    kind,
):
    selected = scope()
    operation, receipt = pair(kind, selected)
    seen = []
    routes = {
        "encryption_key": "/v1/audio-encryption-key",
        "capabilities": "/capture-capabilities",
        "bind_key": "/audio-key",
        "issue_lease": "/capture-lease",
        "renew_lease": "/renew",
        "reserve_chunks": "/chunk-reservations",
        "authorize_upload": "/upload-authorization",
        "chunk_receipt": f"/chunks/{getattr(operation, 'object_id', '')}",
        "register_chunk": "/chunks",
        "complete": "/capture-complete",
        "transcription_status": "/transcription-status",
        "transcript": "/transcript",
    }

    def handler(request):
        seen.append(request)
        if kind == "transcript" and request.url.path.endswith("/transcription-status"):
            from datetime import UTC, datetime

            return httpx.Response(
                200,
                json=CaptureTranscriptionStatus(
                    session_id=selected.session_id,
                    status="completed",
                    transcript_available=True,
                    updated_at=datetime.now(UTC),
                ).model_dump(mode="json"),
            )
        assert request.url.path.endswith(routes[kind])
        assert request.headers["x-tenant-id"] == str(selected.tenant_id)
        if kind != "encryption_key":
            assert str(selected.session_id) in request.url.path
        if kind not in {
            "encryption_key",
            "capabilities",
            "chunk_receipt",
            "transcription_status",
            "transcript",
        }:
            assert request.headers["idempotency-key"] == "synthetic-stable-operation"
        return httpx.Response(200, json=receipt.model_dump(mode="json"))

    async with httpx.AsyncClient(
        base_url="https://scribe.internal.example",
        transport=httpx.MockTransport(handler),
    ) as wire:
        service = HttpScribeWorkflowClient(
            base_url="https://scribe.internal.example",
            scope="api://scribe/.default",
            timeout_seconds=5,
            credential=StaticCredential(),
            client=wire,
        )
        actual = await service.capture_operation(
            selected.tenant_id,
            selected.session_id,
            operation,
            idempotency_key="synthetic-stable-operation",
        )
    assert actual == receipt and len(seen) == (2 if kind == "transcript" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["encryption_key", "bind_key", "reserve_chunks"])
@pytest.mark.parametrize(
    "fault", ["redirect", "oversized", "non_json", "malformed", "transport"]
)
async def test_native_transport_faults_do_not_follow_or_reflect(kind, fault):
    selected = scope()
    operation, _ = pair(kind, selected)
    seen = []

    def handler(request):
        seen.append(request)
        if fault == "redirect":
            return httpx.Response(
                307, headers={"Location": "https://other.invalid/private_sentinel"}
            )
        if fault == "oversized":
            return httpx.Response(
                200, headers={"Content-Type": "application/json"}, content=b" " * 131073
            )
        if fault == "non_json":
            return httpx.Response(200, text="private_sentinel")
        if fault == "malformed":
            return httpx.Response(200, json={"private_sentinel": "private_sentinel"})
        raise httpx.ReadError("private_sentinel", request=request)

    async with httpx.AsyncClient(
        base_url="https://scribe.internal.example",
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    ) as wire:
        service = HttpScribeWorkflowClient(
            base_url="https://scribe.internal.example",
            scope="api://scribe/.default",
            timeout_seconds=5,
            credential=StaticCredential(),
            client=wire,
        )
        with pytest.raises(ScribeWorkflowError) as error:
            await service.capture_operation(
                selected.tenant_id,
                selected.session_id,
                operation,
                idempotency_key=uuid4().hex,
            )
    assert "private_sentinel" not in str(error.value) and error.value.__cause__ is None
    assert len(seen) == 1
