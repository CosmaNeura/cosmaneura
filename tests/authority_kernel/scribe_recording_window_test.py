"""Synthetic first-window sequencing, exact replay and current authority gates."""

import hashlib
import json
from dataclasses import replace
from typing import TypedDict, cast
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.services import scribe_capture_gateway as gateway
from app.services import scribe_capture_protocol as protocol
from app.services import scribe_recording_window as window
from app.services.scribe_capture_admission import AdmittedCaptureScope, CaptureCaller
from app.services.scribe_capture_deadline import (
    CaptureBudget,
    CaptureDeadlineExceeded,
    capture_budget,
)
from app.services.scribe_configured_capture_contract import ConfiguredSessionCreate
from app.services.scribe_recording_window_contract import PrepareRecordingWindow
from app.services.scribe_workflow import HttpScribeWorkflowClient, ScribeWorkflowClient

from tests.authority_kernel.scribe_capture_gateway_test import pair
from tests.authority_kernel.scribe_gateway_receiver_test import scope
from tests.services.scribe_workflow_test import StaticCredential


class RecordingWindowArguments(TypedDict):
    caller: CaptureCaller
    binding_ref: str
    workspace_id: UUID
    scope: AdmittedCaptureScope
    operation: PrepareRecordingWindow
    client_request_key: str
    client: ScribeWorkflowClient


def selected_scope() -> AdmittedCaptureScope:
    old = scope()
    return replace(old, creator_actor_id="actor_synthetic", session_request=ConfiguredSessionCreate(
        schema_version="1.0", language_mode="en-US", id=old.session_id,
        **old.session_request.model_dump(),
    ))


def operation() -> PrepareRecordingWindow:
    return PrepareRecordingWindow.model_validate(dict(kind="prepare_recording_window", payload=dict(
        schema_version="1.0", binding=dict(key_version="synthetic-key", wrapped_dek="a" * 512),
        bind_idempotency_key="original-bind-key", lease_idempotency_key="original-lease-key",
        reserve_idempotency_key="original-reserve-key", channel="microphone", reservation_count=1,
    )))


def receipts(selected):
    binding = pair("bind_key", selected)[1]
    lease = pair("issue_lease", selected)[1]
    assert isinstance(binding, protocol.KeyReceipt)
    assert isinstance(lease, protocol.LeaseReceipt)
    reservations = pair("reserve_chunks", selected)[1].model_copy(update={"capture_authorized_until": lease.expires_at})
    return {"bind_key": binding, "issue_lease": lease, "reserve_chunks": reservations}


def arguments(selected: AdmittedCaptureScope) -> RecordingWindowArguments:
    return {
        "caller": cast(CaptureCaller, None),
        "binding_ref": "synthetic",
        "workspace_id": uuid4(),
        "scope": selected,
        "operation": operation(),
        "client_request_key": "original-window-key",
        "client": cast(ScribeWorkflowClient, None),
    }


@pytest.mark.parametrize("mutation", ["same_keys", "bool_count", "large_count", "scope", "binding", "unknown"])
def test_closed_window_rejects_malformed_or_injected_recipe(mutation):
    data = operation().model_dump()
    payload = data["payload"]
    if mutation == "same_keys":
        payload["lease_idempotency_key"] = payload["bind_idempotency_key"]
    elif mutation == "bool_count":
        payload["reservation_count"] = True
    elif mutation == "large_count":
        payload["reservation_count"] = 91
    elif mutation == "scope":
        payload["session_id"] = str(uuid4())
    elif mutation == "binding":
        payload["binding"]["wrapped_dek"] = "private_sentinel"
    else:
        payload["private_sentinel"] = "private_sentinel"
    with pytest.raises(ValidationError):
        PrepareRecordingWindow.model_validate(data)
    assert "original-bind-key" not in repr(operation())


@pytest.mark.asyncio
@pytest.mark.parametrize("lost", ["bind_key", "issue_lease", "reserve_chunks"])
async def test_partial_commit_replays_the_same_component_receipts(monkeypatch, lost):
    selected = selected_scope()
    values = receipts(selected)
    calls, committed = [], {}
    lose = True

    async def execute(**kwargs):
        nonlocal lose
        component, key = kwargs["operation"], kwargs["client_request_key"]
        calls.append((component, key))
        committed.setdefault(key, values[component.kind])
        if component.kind == lost and lose:
            lose = False
            raise gateway.CaptureOperationUnconfirmed
        return committed[key]

    monkeypatch.setattr(gateway, "execute_capture_operation", execute)
    monkeypatch.setattr(window, "authorize_admitted_capture", lambda *_: selected)
    kwargs = arguments(selected)
    with pytest.raises(gateway.CaptureOperationUnconfirmed):
        await window.execute_recording_window(**kwargs)
    returned = await window.execute_recording_window(**kwargs)
    assert returned.binding == values["bind_key"] and returned.lease == values["issue_lease"]
    assert returned.reservations == values["reserve_chunks"] and len(committed) == 3
    for kind, key in (("bind_key", "original-bind-key"), ("issue_lease", "original-lease-key"), ("reserve_chunks", "original-reserve-key")):
        matching = [op for op, saved_key in calls if saved_key == key]
        assert matching and all(op == matching[0] and op.kind == kind for op in matching)
    reserve = calls[-1][0].payload
    assert reserve.capture_lease_id == returned.lease.lease_id
    assert reserve.capture_lease_generation == returned.lease.generation
    assert (reserve.channel, reserve.first_sequence_number, reserve.count) == ("microphone", 0, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("check", range(1, 7))
@pytest.mark.parametrize("failure", ["revoked", "changed_scope"])
async def test_every_component_checks_scope_before_and_after_io(monkeypatch, check, failure):
    selected = selected_scope()
    values, calls = receipts(selected), []
    count = 0

    def authorize(*_):
        nonlocal count
        count += 1
        if count == check:
            if failure == "revoked":
                raise gateway.CaptureOperationUnavailable
            return replace(selected, creator_actor_id="actor_changed")
        return selected

    async def execute(**kwargs):
        calls.append(kwargs["operation"].kind)
        return values[calls[-1]]

    monkeypatch.setattr(window, "authorize_admitted_capture", authorize)
    monkeypatch.setattr(gateway, "execute_capture_operation", execute)
    with pytest.raises(gateway.CaptureGatewayError):
        await window.execute_recording_window(**arguments(selected))
    assert len(calls) == check // 2


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_after", ["bind_key", "issue_lease", "reserve_chunks"])
async def test_cancelled_budget_cannot_advance_or_publish(monkeypatch, cancel_after):
    selected = selected_scope()
    values, calls = receipts(selected), []
    budget = CaptureBudget(30)

    async def execute(**kwargs):
        kind = kwargs["operation"].kind
        calls.append(kind)
        if kind == cancel_after:
            budget.cancel()
        return values[kind]

    monkeypatch.setattr(window, "authorize_admitted_capture", lambda *_: selected)
    monkeypatch.setattr(gateway, "execute_capture_operation", execute)
    with capture_budget(budget), pytest.raises(CaptureDeadlineExceeded):
        await window.execute_recording_window(**arguments(selected))
    assert calls[-1] == cancel_after


@pytest.mark.asyncio
async def test_outer_key_cannot_alias_a_component(monkeypatch):
    selected = selected_scope()
    monkeypatch.setattr(gateway, "execute_capture_operation", lambda **_: pytest.fail("mutation reached"))
    with pytest.raises(gateway.CaptureReceiptInvalid):
        kwargs = arguments(selected)
        kwargs["client_request_key"] = "original-bind-key"
        await window.execute_recording_window(**kwargs)


@pytest.mark.asyncio
async def test_real_gateway_and_http_transport_keep_existing_receipt_routes(monkeypatch):
    selected = selected_scope()
    values, calls = receipts(selected), []
    view = {"schema_version": "1.0", "session_id": str(selected.session_id), "status": "created"}

    def handler(request):
        calls.append(request)
        assert request.headers["x-tenant-id"] == str(selected.tenant_id)
        if request.url.path.endswith("/review"):
            assert request.headers["x-scribe-creator-actor-id"] == selected.creator_actor_id
            return httpx.Response(200, json=view)
        position = len([call for call in calls if not call.url.path.endswith("/review")]) - 1
        kind = ["bind_key", "issue_lease", "reserve_chunks"][position]
        key = ["original-bind-key", "original-lease-key", "original-reserve-key"][position]
        expected = "core-capture-" + hashlib.sha256(json.dumps(
            [selected.operation_ref, kind, key], separators=(",", ":")
        ).encode()).hexdigest()
        assert request.headers["idempotency-key"] == expected
        if kind != "bind_key":
            assert f"/captured-sessions/{selected.session_id}/" in request.url.path
            assert request.headers["x-scribe-creator-actor-id"] == selected.creator_actor_id
        return httpx.Response(200, json=values[kind].model_dump(mode="json"))

    monkeypatch.setattr(gateway, "authorize_admitted_capture", lambda *_: selected)
    monkeypatch.setattr(window, "authorize_admitted_capture", lambda *_: selected)
    async with httpx.AsyncClient(base_url="https://scribe.invalid", transport=httpx.MockTransport(handler)) as transport:
        remote = HttpScribeWorkflowClient(base_url="https://scribe.invalid", scope="api://scribe/.default",
            credential=StaticCredential(), timeout_seconds=3, client=transport)
        kwargs = arguments(selected)
        result = await gateway.execute_capture_operation(
            caller=kwargs["caller"],
            binding_ref=kwargs["binding_ref"],
            workspace_id=kwargs["workspace_id"],
            operation=kwargs["operation"],
            client_request_key=kwargs["client_request_key"],
            client=remote,
        )
    assert isinstance(result, window.PreparedRecordingWindow)
    assert len(calls) == 4
