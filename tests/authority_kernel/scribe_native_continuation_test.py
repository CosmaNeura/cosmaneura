"""Configured native continuation across the Core/Scribe boundary; synthetic only."""

import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.configuration.scribe_capture import ScribeCaptureSettings
from app.services import scribe_capture_gateway as gateway
from app.services import scribe_capture_protocol as protocol
from app.services.scribe_source_extension_contract import (
    NativeExtensionDecision,
    NativeExtensionPayload,
    NativeExtensionReceipt,
    NativeSourceExtension,
)
from app.services.scribe_workflow import (
    HttpScribeWorkflowClient,
    ScribeWorkflowRejectedError,
    ScribeWorkflowRetryableError,
)

from tests.authority_kernel.scribe_gateway_receiver_test import (
    PATH,
    body,
    client,
    request_headers,
)
from tests.authority_kernel.scribe_release_review_regressions_test import prepared_start
from tests.services.scribe_workflow_test import StaticCredential


def extension():
    selected, start, _, now = prepared_start()
    owner = start.payload.model_copy(update={"source": "ios_microphone"})
    payload = NativeExtensionPayload(
        owner=owner,
        lease_receipt_id=uuid4(),
        lease_generation=2,
        capture_authorized_until=owner.capture_authorized_until + timedelta(minutes=15),
        reservation_batch_id=uuid4(),
        first_sequence_number=90,
        reservation_count=90,
    )
    operation = NativeSourceExtension(kind="source_extend", payload=payload)
    receipt = NativeExtensionReceipt(
        extension_id=uuid4(),
        source_start_id=uuid4(),
        session_id=selected.session_id,
        attempt_id=owner.attempt_id,
        creator_actor_id=selected.creator_actor_id,
        consent_record_id=selected.session_request.consent_record_id,
        source=owner.source,
        lease_id=owner.lease_id,
        lease_receipt_id=payload.lease_receipt_id,
        lease_generation=payload.lease_generation,
        previous_extension_id=None,
        previous_capture_authorized_until=owner.capture_authorized_until,
        capture_authorized_until=payload.capture_authorized_until,
        reservation_batch_id=payload.reservation_batch_id,
        first_sequence_number=90,
        maximum_sequence_number=179,
        issued_at=now,
        expires_at=now + timedelta(seconds=10),
        capability_id=uuid4(),
    )
    return (
        selected,
        operation,
        NativeExtensionDecision(receipt=receipt, secret=owner.owner_proof),
        now,
    )


@pytest.mark.parametrize(
    "fault",
    [
        "session_id",
        "creator_actor_id",
        "consent_record_id",
        "attempt_id",
        "source",
        "lease_id",
        "lease_receipt_id",
        "lease_generation",
        "previous_extension_id",
        "capture_authorized_until",
        "reservation_batch_id",
        "first_sequence_number",
        "maximum_sequence_number",
        "issued_at",
        "expires_at",
        "previous_capture_authorized_until",
        "secret",
        "extra",
    ],
)
def test_extension_requires_exact_current_receipt(fault):
    selected, operation, decision, now = extension()
    assert (
        gateway.validate_receipt(
            operation, decision, selected, upload_origins=(), now=now
        )
        == decision
    )
    receipt = decision.receipt
    value = getattr(receipt, fault, None)
    if fault == "secret":
        changed = decision.model_copy(update={"secret": "b" * 64})
    elif fault == "extra":
        with pytest.raises(ValidationError):
            NativeExtensionDecision.model_validate(
                {**decision.model_dump(), "tenant_id": uuid4()}
            )
        return
    else:
        if fault == "creator_actor_id":
            value = "foreign_creator"
        elif fault == "source":
            value = "desktop_companion"
        elif fault in {"issued_at", "expires_at"}:
            value = now + timedelta(seconds=1) if fault == "issued_at" else now
        elif isinstance(value, int):
            value += 1
        elif fault.endswith("until"):
            value += timedelta(seconds=1)
        else:
            value = uuid4()
        changed = decision.model_copy(
            update={"receipt": receipt.model_copy(update={fault: value})}
        )
    with pytest.raises(gateway.CaptureReceiptInvalid):
        gateway.validate_receipt(
            operation, changed, selected, upload_origins=(), now=now
        )


@pytest.mark.parametrize(
    "patch",
    [
        {"first_sequence_number": 359, "reservation_count": 2},
        {"first_sequence_number": True},
        {"lease_generation": 1},
        {"tenant_id": str(uuid4())},
    ],
)
def test_invalid_extension_is_rejected_before_dispatch(patch):
    _, operation, _, _ = extension()
    payload = operation.payload.model_dump()
    with pytest.raises(ValidationError):
        protocol.OPERATION_ADAPTER.validate_python(
            {"kind": "source_extend", "payload": payload | patch}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", [None, "disabled", "legacy", "missing_actor", "revoked", "changed", "lost"]
)
async def test_extension_retains_admission_and_rechecks_after_io(monkeypatch, fault):
    selected, operation, decision, _ = extension()
    if fault == "legacy":
        from tests.authority_kernel.scribe_gateway_receiver_test import scope

        selected = replace(selected, session_request=scope().session_request)
    if fault == "missing_actor":
        selected = replace(selected, creator_actor_id=None)
    calls, checks = [], []

    def authorize(*_):
        checks.append(True)
        if len(checks) == 2:
            if fault == "revoked":
                raise gateway.CaptureOperationUnavailable
            if fault == "changed":
                return replace(selected, tenant_id=uuid4())
        return selected

    class Receiver:
        async def capture_operation(self, tenant_id, session_id, submitted, **kwargs):
            assert (tenant_id, session_id, submitted) == (
                selected.tenant_id,
                selected.session_id,
                operation,
            )
            assert kwargs == {
                "idempotency_key": "synthetic-extension-intent",
                "creator_actor_id": selected.creator_actor_id,
            }
            calls.append(True)
            if fault == "lost":
                raise ScribeWorkflowRetryableError("synthetic_lost_reply")
            return decision

    monkeypatch.setattr(gateway, "authorize_admitted_capture", authorize)
    args = {
        "caller": SimpleNamespace(
            capture_settings=ScribeCaptureSettings(
                long_visits_enabled=fault != "disabled"
            )
        ),
        "binding_ref": "synthetic-binding",
        "workspace_id": uuid4(),
        "operation": operation,
        "client_request_key": "synthetic-extension-intent",
        "client": Receiver(),
    }
    if fault is None:
        assert await gateway.execute_capture_operation(**args) == decision
        assert checks == [True, True] and calls == [True]
    else:
        with pytest.raises(gateway.CaptureGatewayError):
            await gateway.execute_capture_operation(**args)
        assert len(calls) == (
            0 if fault in {"disabled", "legacy", "missing_actor"} else 1
        )


@pytest.mark.asyncio
async def test_extension_transport_preserves_exact_retry_and_creator():
    selected, operation, decision, _ = extension()
    seen = []

    def remote(request):
        assert request.method == "POST"
        assert (
            request.url.path
            == f"/v1/captured-sessions/{selected.session_id}/source-extension"
        )
        assert request.headers["X-Tenant-ID"] == str(selected.tenant_id)
        assert request.headers["X-Scribe-Creator-Actor-ID"] == selected.creator_actor_id
        assert request.headers["Idempotency-Key"] == "synthetic-extension-intent"
        assert json.loads(request.content) == operation.payload.model_dump(mode="json")
        seen.append((request.content, dict(request.headers)))
        if len(seen) == 1:
            raise httpx.ReadError("synthetic_lost_reply", request=request)
        return httpx.Response(200, json=decision.model_dump(mode="json"))

    async with httpx.AsyncClient(
        base_url="https://scribe.test.invalid", transport=httpx.MockTransport(remote)
    ) as transport:
        receiver = HttpScribeWorkflowClient(
            base_url="https://scribe.test.invalid",
            scope="api://scribe/.default",
            credential=StaticCredential(),
            timeout_seconds=5,
            client=transport,
        )
        args = {
            "idempotency_key": "synthetic-extension-intent",
            "creator_actor_id": selected.creator_actor_id,
        }
        with pytest.raises(ScribeWorkflowRetryableError):
            await receiver.capture_operation(
                selected.tenant_id, selected.session_id, operation, **args
            )
        assert len(seen) == 1  # No automatic second mutation inside Core.
        assert (
            await receiver.capture_operation(
                selected.tenant_id, selected.session_id, operation, **args
            )
            == decision
        )
        assert seen[0] == seen[1]
        with pytest.raises(ScribeWorkflowRejectedError):
            await receiver.capture_operation(
                selected.tenant_id,
                selected.session_id,
                operation,
                idempotency_key="synthetic-extension-intent",
            )
        assert len(seen) == 2


def test_extension_reaches_existing_facade_and_keeps_no_store(monkeypatch):
    _, operation, decision, _ = extension()
    payload = body()
    payload["operation"] = operation.model_dump(mode="json")

    async def execute(**kwargs):
        assert kwargs["operation"] == operation
        return decision

    monkeypatch.setattr(gateway, "execute_capture_operation", execute)
    response = client().post(PATH, headers=request_headers(), json=payload)
    assert (
        response.status_code == 200 and response.headers["cache-control"] == "no-store"
    )
    assert response.json()["receipt"] == decision.model_dump(mode="json")


@pytest.mark.asyncio
async def test_lease_state_uses_its_own_route_and_validates_nested_scope():
    selected, _, _, _ = extension()
    result = protocol.LeaseState(
        session_id=selected.session_id,
        current_lease=None,
        lease_closed=False,
        session_status="created",
    )
    seen = []

    def remote(request):
        seen.append(request)
        assert request.method == "GET"
        assert (
            request.url.path
            == f"/v1/sessions/{selected.session_id}/capture-lease-state"
        )
        return httpx.Response(200, json=result.model_dump(mode="json"))

    async with httpx.AsyncClient(
        base_url="https://scribe.test.invalid", transport=httpx.MockTransport(remote)
    ) as transport:
        receiver = HttpScribeWorkflowClient(
            base_url="https://scribe.test.invalid",
            scope="api://scribe/.default",
            credential=StaticCredential(),
            timeout_seconds=5,
            client=transport,
        )
        operation = protocol.ReadOperation(kind="capture_lease_state")
        response = await receiver.capture_operation(
            selected.tenant_id,
            selected.session_id,
            operation,
            idempotency_key="synthetic-lease-read",
        )
        assert (
            gateway.validate_receipt(operation, response, selected, upload_origins=())
            == result
        )
        assert len(seen) == 1
    with pytest.raises(ValidationError):
        protocol.LeaseState.model_validate(result.model_dump() | {"lease_closed": True})
    _, start, _, now = prepared_start()
    lease = {
        "receipt_id": uuid4(),
        "lease_id": start.payload.lease_id,
        "session_id": uuid4(),
        "generation": 1,
        "issued_at": now,
        "expires_at": now + timedelta(minutes=15),
    }
    with pytest.raises(ValidationError):
        protocol.LeaseState.model_validate(
            result.model_dump() | {"current_lease": lease}
        )
