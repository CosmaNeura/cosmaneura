"""Combined creator continuation keeps ordinary lineage and replay namespaces."""

import base64
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.services import scribe_capture_gateway as gateway
from app.services import scribe_capture_protocol as p
from app.services.scribe_capture_admission import CaptureCaller
from app.services.scribe_capture_deadline import (
    CaptureBudget,
    DeadlineScribeClient,
    capture_budget,
)
from app.services.scribe_continuation_window import (
    PreparedContinuationWindow,
    validate_prepared_continuation_window,
)
from app.services.scribe_continuation_window_contract import PrepareContinuationWindow
from app.services.scribe_workflow import HttpScribeWorkflowClient, ScribeWorkflowClient

from tests.authority_kernel.scribe_recording_window_test import selected_scope
from tests.services.scribe_workflow_test import StaticCredential


def fixture(count=1):
    selected = selected_scope()
    now = datetime.now(UTC)
    lease = p.LeaseReceipt(receipt_id=uuid4(), lease_id=uuid4(), session_id=selected.session_id,
        generation=2, issued_at=now, expires_at=now + timedelta(minutes=15))
    operation = PrepareContinuationWindow.model_validate(dict(kind="prepare_continuation_window", payload=dict(
        schema_version="1.0", channel="mixed", first_sequence_number=1, previous_lease_id=lease.lease_id,
        previous_lease_generation=1, renew_idempotency_key="original-renew-key", reservation_idempotency_key="original-reserve-key")))
    rows = []
    for sequence in range(1, count + 1):
        obj = uuid4()
        aad = base64.urlsafe_b64encode(json.dumps(dict(tenant_id=str(selected.tenant_id), session_id=str(selected.session_id),
            object_id=str(obj), sequence_number=sequence, channel="mixed"), sort_keys=True, separators=(",", ":")).encode()).decode().rstrip("=")
        rows.append(p.Reservation(object_id=obj, session_id=selected.session_id, channel="mixed", sequence_number=sequence,
            additional_authenticated_data=aad, created_at=now))
    reply = PreparedContinuationWindow(lease=lease, reservations=p.ReservationReceipt(batch_id=uuid4(),
        session_id=selected.session_id, capture_authorized_until=lease.expires_at, reservations=tuple(rows)))
    return selected, operation, reply


def arguments(selected, operation, remote):
    return dict(caller=cast(CaptureCaller, None), binding_ref="synthetic", workspace_id=uuid4(), operation=operation,
        client_request_key="original-continuation-key", client=cast(ScribeWorkflowClient, remote))


@pytest.mark.parametrize("mutation", ["bool_sequence", "channel", "component_alias", "client_count", "session", "profile"])
def test_continuation_rejects_scope_or_count_injection(mutation):
    _, operation, _ = fixture()
    body = operation.model_dump()
    change = {"bool_sequence": {"first_sequence_number": True}, "channel": {"channel": "system_audio"},
        "component_alias": {"renew_idempotency_key": "original-reserve-key"}, "client_count": {"count": 90},
        "session": {"session_id": uuid4()}, "profile": {"profile_receipt_id": uuid4()}}[mutation]
    body["payload"].update(change)
    with pytest.raises(ValidationError):
        p.OPERATION_ADAPTER.validate_python(body)


@pytest.mark.parametrize("mutation", ["lease_session", "batch_session", "lease_id", "generation", "expired", "expiry_mismatch",
    "channel", "sequence", "aad", "empty", "duplicate", "oversized"])
def test_combined_receipt_cannot_invent_authority_or_reservations(mutation):
    selected, operation, reply = fixture()
    body = reply.model_dump()
    if mutation == "lease_session":
        body["lease"]["session_id"] = uuid4()
    elif mutation == "batch_session":
        body["reservations"]["session_id"] = uuid4()
    elif mutation == "lease_id":
        body["lease"]["lease_id"] = uuid4()
    elif mutation == "generation":
        body["lease"]["generation"] = 3
    elif mutation == "expired":
        body["lease"]["issued_at"] -= timedelta(hours=1)
        body["lease"]["expires_at"] -= timedelta(hours=1)
    elif mutation == "expiry_mismatch":
        body["reservations"]["capture_authorized_until"] += timedelta(seconds=1)
    elif mutation in {"channel", "sequence", "aad"}:
        body["reservations"]["reservations"][0].update({"channel": "microphone"} if mutation == "channel" else
            {"sequence_number": 2} if mutation == "sequence" else {"additional_authenticated_data": "e30"})
    elif mutation == "empty":
        body["reservations"]["reservations"] = ()
    else:
        body["reservations"]["reservations"] *= 2 if mutation == "duplicate" else 91
    # model_construct deliberately bypasses receipt decoding to exercise the
    # gateway's own parser against malformed transport/test-double output.
    unsafe = PreparedContinuationWindow.model_construct(**body)
    with pytest.warns(UserWarning, match="Pydantic serializer warnings"), pytest.raises((gateway.CaptureReceiptInvalid, ValidationError)):
        validate_prepared_continuation_window(operation, unsafe, selected)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing_creator", "legacy_scope", "outer_alias", "revoked_after", "changed_scope"])
async def test_current_creator_scope_before_and_after_remote(monkeypatch, failure):
    selected, operation, reply = fixture()
    if failure == "missing_creator":
        selected = replace(selected, creator_actor_id=None)
    if failure == "legacy_scope":
        from tests.authority_kernel.scribe_gateway_receiver_test import scope
        selected = replace(scope(), creator_actor_id="actor_synthetic")
    calls = []
    authorizations = 0

    def authorize(*_):
        nonlocal authorizations
        authorizations += 1
        if authorizations == 2:
            if failure == "revoked_after":
                raise gateway.CaptureOperationUnavailable
            if failure == "changed_scope":
                return replace(selected, creator_actor_id="actor_changed")
        return selected

    class Remote:
        async def capture_operation(self, *_, **kwargs):
            calls.append(kwargs)
            return reply

    monkeypatch.setattr(gateway, "authorize_admitted_capture", authorize)
    kwargs = arguments(selected, operation, Remote())
    if failure == "outer_alias":
        kwargs["client_request_key"] = operation.payload.renew_idempotency_key
    with pytest.raises(gateway.CaptureGatewayError):
        await gateway.execute_capture_operation(**kwargs)
    assert len(calls) == (1 if failure in {"revoked_after", "changed_scope"} else 0)


@pytest.mark.asyncio
async def test_real_route_deadline_wrapper_rejects_late_committed_reply(monkeypatch):
    selected, operation, reply = fixture()
    budget, calls = CaptureBudget(30), []

    class Remote:
        async def capture_operation(self, *_, **__):
            calls.append(True)
            budget.cancel()
            return reply

    monkeypatch.setattr(gateway, "authorize_admitted_capture", lambda *_: selected)
    with capture_budget(budget), pytest.raises(gateway.CaptureOperationUnconfirmed):
        await gateway.execute_capture_operation(**arguments(selected, operation, DeadlineScribeClient(Remote())))
    assert calls == [True]


@pytest.mark.asyncio
async def test_ninety_reservations_use_one_http_request_and_exact_component_namespace(monkeypatch):
    selected, operation, reply = fixture(90)
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == f"/v1/captured-sessions/{selected.session_id}/continuation-window"
        assert request.headers["x-tenant-id"] == str(selected.tenant_id)
        assert request.headers["x-scribe-creator-actor-id"] == selected.creator_actor_id
        assert request.headers["idempotency-key"] == "original-continuation-key"
        expected = operation.payload.model_dump(mode="json")
        expected["renew_idempotency_key"] = gateway.component_request_key(selected, "renew_lease", operation.payload.renew_idempotency_key)
        expected["reservation_idempotency_key"] = gateway.component_request_key(selected, "reserve_chunks", operation.payload.reservation_idempotency_key)
        assert json.loads(request.content) == expected
        return httpx.Response(200, json=reply.model_dump(mode="json"))

    monkeypatch.setattr(gateway, "authorize_admitted_capture", lambda *_: selected)
    async with httpx.AsyncClient(base_url="https://scribe.invalid", transport=httpx.MockTransport(handler)) as transport:
        remote = HttpScribeWorkflowClient(base_url="https://scribe.invalid", scope="api://scribe/.default",
            credential=StaticCredential(), timeout_seconds=3, client=transport)
        result = await gateway.execute_capture_operation(**arguments(selected, operation, remote))
    assert result == reply and len(calls) == 1
