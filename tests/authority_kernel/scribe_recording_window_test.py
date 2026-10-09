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
    return replace(
        old,
        creator_actor_id="actor_synthetic",
        session_request=ConfiguredSessionCreate(
            schema_version="1.0",
            language_mode="en-US",
            id=old.session_id,
            **old.session_request.model_dump(),
        ),
    )


def operation() -> PrepareRecordingWindow:
    return PrepareRecordingWindow.model_validate(
        dict(
            kind="prepare_recording_window",
            payload=dict(
                schema_version="1.0",
                binding=dict(key_version="synthetic-key", wrapped_dek="a" * 512),
                bind_idempotency_key="original-bind-key",
                lease_idempotency_key="original-lease-key",
                reserve_idempotency_key="original-reserve-key",
                channel="microphone",
                reservation_count=1,
            ),
        )
    )


def receipts(selected):
    binding = pair("bind_key", selected)[1]
    lease = pair("issue_lease", selected)[1]
    assert isinstance(binding, protocol.KeyReceipt)
    assert isinstance(lease, protocol.LeaseReceipt)
    reservations = pair("reserve_chunks", selected)[1].model_copy(
        update={"capture_authorized_until": lease.expires_at}
    )
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


@pytest.mark.parametrize(
    "mutation",
    ["same_keys", "bool_count", "large_count", "scope", "binding", "unknown"],
)
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


def prepared(selected):
    values = receipts(selected)
    return window.PreparedRecordingWindow(
        binding=values["bind_key"],
        lease=values["issue_lease"],
        reservations=values["reserve_chunks"],
    )


@pytest.mark.asyncio
async def test_lost_combined_reply_replays_original_component_keys(monkeypatch):
    selected = selected_scope()
    committed, calls = {}, []
    lose = True

    class Remote:
        async def capture_operation(self, tenant, session, component, **kwargs):
            nonlocal lose
            assert (tenant, session) == (selected.tenant_id, selected.session_id)
            assert kwargs["creator_actor_id"] == selected.creator_actor_id
            calls.append((component, kwargs))
            key = kwargs["idempotency_key"]
            committed.setdefault(key, prepared(selected))
            if lose:
                lose = False
                from app.services.scribe_workflow import ScribeWorkflowError

                raise ScribeWorkflowError("synthetic_lost_reply")
            return committed[key]

    monkeypatch.setattr(gateway, "authorize_admitted_capture", lambda *_: selected)
    args = arguments(selected) | {"client": Remote()}
    with pytest.raises(gateway.CaptureOperationUnconfirmed):
        await window.execute_recording_window(**args)
    returned = await window.execute_recording_window(**args)
    assert len(calls) == 2 and calls[0] == calls[1] and len(committed) == 1
    assert returned == next(iter(committed.values()))
    remote = calls[0][0]
    for field, kind, original in (
        ("bind_idempotency_key", "bind_key", "original-bind-key"),
        ("lease_idempotency_key", "issue_lease", "original-lease-key"),
        ("reserve_idempotency_key", "reserve_chunks", "original-reserve-key"),
    ):
        assert getattr(remote.payload, field) == gateway.component_request_key(
            selected, kind, original
        )
    assert remote.payload.binding == operation().payload.binding


@pytest.mark.asyncio
@pytest.mark.parametrize("check", [1, 2])
@pytest.mark.parametrize("failure", ["revoked", "changed_scope"])
async def test_current_scope_brackets_combined_request(monkeypatch, check, failure):
    selected, calls = selected_scope(), []
    count = 0

    def authorize(*_):
        nonlocal count
        count += 1
        if count == check:
            if failure == "revoked":
                raise gateway.CaptureOperationUnavailable
            return replace(selected, creator_actor_id="actor_changed")
        return selected

    class Remote:
        async def capture_operation(self, _tenant, _session, component, **kwargs):
            calls.append(component.kind)
            return prepared(selected)

    monkeypatch.setattr(gateway, "authorize_admitted_capture", authorize)
    args = arguments(selected) | {"client": Remote()}
    args.pop("scope")
    with pytest.raises(gateway.CaptureGatewayError):
        await gateway.execute_capture_operation(**args)
    assert calls == (
        [] if check == 1 and failure == "revoked" else ["prepare_recording_window"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage", ["before_dispatch", "after_remote", "during_validation"]
)
async def test_cancelled_budget_cannot_dispatch_or_publish(monkeypatch, stage):
    selected, calls, budget = selected_scope(), [], CaptureBudget(30)

    class Remote:
        async def capture_operation(self, _tenant, _session, component, **kwargs):
            calls.append(component.kind)
            if stage == "after_remote":
                budget.cancel()
            return prepared(selected)

    original = gateway.validate_receipt

    def validate(*args, **kwargs):
        result = original(*args, **kwargs)
        if stage == "during_validation":
            budget.cancel()
        return result

    monkeypatch.setattr(gateway, "authorize_admitted_capture", lambda *_: selected)
    monkeypatch.setattr(gateway, "validate_receipt", validate)
    if stage == "before_dispatch":
        budget.cancel()
    with pytest.raises(CaptureDeadlineExceeded), capture_budget(budget):
        await window.execute_recording_window(
            **(arguments(selected) | {"client": Remote()})
        )
    assert calls == ([] if stage == "before_dispatch" else ["prepare_recording_window"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "binding_session",
        "key_version",
        "lease_session",
        "lease_generation",
        "expired",
        "reservation_session",
        "count",
        "channel",
        "sequence",
        "aad",
        "lease_expiry",
        "injected",
    ],
)
async def test_each_component_receipt_is_validated(monkeypatch, mutation):
    from datetime import UTC, datetime, timedelta

    from pydantic import BaseModel

    selected = selected_scope()
    data = prepared(selected).model_dump(mode="json")
    if mutation == "binding_session":
        data["binding"]["session_id"] = str(uuid4())
    elif mutation == "key_version":
        data["binding"]["key_version"] = "different-key"
    elif mutation == "lease_session":
        data["lease"]["session_id"] = str(uuid4())
    elif mutation == "lease_generation":
        data["lease"]["generation"] = 2
    elif mutation == "expired":
        data["lease"]["expires_at"] = (
            datetime.now(UTC) - timedelta(seconds=1)
        ).isoformat()
    elif mutation == "reservation_session":
        data["reservations"]["session_id"] = str(uuid4())
    elif mutation == "count":
        data["reservations"]["reservations"] = []
    elif mutation == "channel":
        data["reservations"]["reservations"][0]["channel"] = "mixed"
    elif mutation == "sequence":
        data["reservations"]["reservations"][0]["sequence_number"] = 1
    elif mutation == "aad":
        data["reservations"]["reservations"][0]["additional_authenticated_data"] = (
            "invalid-aad"
        )
    elif mutation == "lease_expiry":
        data["reservations"]["capture_authorized_until"] = (
            datetime.now(UTC) + timedelta(seconds=180)
        ).isoformat()
    else:
        data["private_sentinel"] = "private_sentinel"

    class Evidence(BaseModel):
        model_config = {"extra": "allow"}

    class Remote:
        async def capture_operation(self, *_args, **_kwargs):
            return Evidence.model_validate(data)

    monkeypatch.setattr(gateway, "authorize_admitted_capture", lambda *_: selected)
    with pytest.raises(gateway.CaptureReceiptInvalid):
        await window.execute_recording_window(
            **(arguments(selected) | {"client": Remote()})
        )


@pytest.mark.asyncio
async def test_outer_key_cannot_alias_a_component(monkeypatch):
    selected = selected_scope()
    monkeypatch.setattr(
        gateway,
        "execute_capture_operation",
        lambda **_: pytest.fail("mutation reached"),
    )
    with pytest.raises(gateway.CaptureReceiptInvalid):
        kwargs = arguments(selected)
        kwargs["client_request_key"] = "original-bind-key"
        await window.execute_recording_window(**kwargs)


@pytest.mark.asyncio
async def test_real_gateway_and_http_transport_use_one_closed_creator_receiver(
    monkeypatch,
):
    selected, calls = selected_scope(), []

    def handler(request):
        calls.append(request)
        assert request.method == "POST"
        assert (
            request.url.path
            == f"/v1/captured-sessions/{selected.session_id}/recording-window"
        )
        assert request.headers["x-tenant-id"] == str(selected.tenant_id)
        assert request.headers["x-scribe-creator-actor-id"] == selected.creator_actor_id
        assert request.headers["idempotency-key"] == gateway.component_request_key(
            selected, "prepare_recording_window", "original-window-key"
        )
        body = json.loads(request.content)
        assert set(body) == set(operation().payload.model_dump())
        for field, kind in (
            ("bind_idempotency_key", "bind_key"),
            ("lease_idempotency_key", "issue_lease"),
            ("reserve_idempotency_key", "reserve_chunks"),
        ):
            original = getattr(operation().payload, field)
            expected = (
                "core-capture-"
                + hashlib.sha256(
                    json.dumps(
                        [selected.operation_ref, kind, original], separators=(",", ":")
                    ).encode()
                ).hexdigest()
            )
            assert body[field] == expected
        return httpx.Response(200, json=prepared(selected).model_dump(mode="json"))

    monkeypatch.setattr(gateway, "authorize_admitted_capture", lambda *_: selected)
    async with httpx.AsyncClient(
        base_url="https://scribe.invalid", transport=httpx.MockTransport(handler)
    ) as transport:
        remote = HttpScribeWorkflowClient(
            base_url="https://scribe.invalid",
            scope="api://scribe/.default",
            credential=StaticCredential(),
            timeout_seconds=3,
            client=transport,
        )
        args = arguments(selected) | {"client": remote}
        args.pop("scope")
        result = await gateway.execute_capture_operation(**args)
    assert isinstance(result, window.PreparedRecordingWindow) and len(calls) == 1
