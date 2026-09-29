"""One Core request, unchanged native intents and fresh per-step admission."""

import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.routes.scribe_capture import get_admission_client_factory
from app.services import scribe_capture_gateway as gateway
from app.services.scribe_source_start_contract import (
    NativeSourceStartSequence,
    NativeStartDecision,
    NativeStartSequenceResult,
)
from app.services.scribe_workflow import (
    HttpScribeWorkflowClient,
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


class Sequence:
    def __init__(self, monkeypatch):
        self.scope, start, preparation, at = prepared_start()
        self.operation = NativeSourceStartSequence(
            kind="source_start_sequence",
            payload=start.payload.model_copy(update={"first_sequence_number": 1024}),
            authorize_idempotency_key="synthetic-authorization-intent",
        )
        self.preparation = preparation.model_copy(
            update={"first_sequence_number": 1024}
        )
        self.authorization = NativeStartDecision(
            receipt=self.preparation.model_copy(
                update={
                    "state": "issued",
                    "issued_at": at,
                    "expires_at": at + timedelta(seconds=15),
                }
            ),
            secret=start.payload.owner_proof,
        )
        self.calls, self.checks = [], 0
        self.deny_at = self.change_at = 0
        self.lose = None
        self.cancel_after_prepare = False
        self.closed = False
        monkeypatch.setattr(gateway, "authorize_admitted_capture", self.authorize)

    def authorize(self, *_):
        self.checks += 1
        if self.checks == self.deny_at:
            raise gateway.CaptureOperationUnavailable
        if self.checks == self.change_at:
            return replace(self.scope, session_id=uuid4())
        return self.scope

    async def capture_operation(self, tenant, session, operation, **kwargs):
        assert (tenant, session) == (self.scope.tenant_id, self.scope.session_id)
        assert operation.kind == "source_start"
        assert operation.payload == self.operation.payload
        assert kwargs["creator_actor_id"] == self.scope.creator_actor_id
        self.calls.append((operation.action, kwargs["idempotency_key"]))
        if self.lose == operation.action:
            raise ScribeWorkflowRetryableError("synthetic lost response")
        if operation.action == "prepare":
            if self.cancel_after_prepare:
                asyncio.current_task().cancel()
            return self.preparation
        assert operation.action == "authorize"
        return self.authorization

    async def close(self):
        self.closed = True

    async def execute(self, **changes):
        arguments = {
            "caller": SimpleNamespace(),
            "binding_ref": "synthetic-binding",
            "workspace_id": uuid4(),
            "operation": self.operation,
            "client_request_key": "synthetic-preparation-intent",
            "client": self,
        }
        return await gateway.execute_capture_operation(**(arguments | changes))


@pytest.mark.asyncio
async def test_existing_decisions_keep_distinct_original_keys_and_current_scope(
    monkeypatch,
):
    f = Sequence(monkeypatch)
    result = await f.execute()
    assert result == NativeStartSequenceResult(
        preparation=f.preparation, authorization=f.authorization
    )
    assert f.checks == 4
    assert f.calls == [
        ("prepare", "synthetic-preparation-intent"),
        ("authorize", "synthetic-authorization-intent"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", [1, 2, 3, 4])
async def test_revocation_at_each_step_withholds_next_action_or_result(
    monkeypatch, boundary
):
    f = Sequence(monkeypatch)
    f.deny_at = boundary
    with pytest.raises(gateway.CaptureOperationUnavailable):
        await f.execute()
    assert len(f.calls) == boundary // 2


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", [2, 3, 4])
async def test_changed_admission_cannot_publish_or_authorize_a_different_source(
    monkeypatch, boundary
):
    f = Sequence(monkeypatch)
    f.change_at = boundary
    with pytest.raises(gateway.CaptureReceiptInvalid):
        await f.execute()
    assert len(f.calls) == (1 if boundary < 4 else 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["prepare", "authorize"])
async def test_lost_reply_never_retries_and_explicit_retry_keeps_both_intents(
    monkeypatch, stage
):
    f = Sequence(monkeypatch)
    f.lose = stage
    with pytest.raises(gateway.CaptureOperationUnconfirmed):
        await f.execute()
    failed = list(f.calls)
    assert len(failed) == (1 if stage == "prepare" else 2)
    f.lose = None
    await f.execute()
    assert f.calls[-2:] == [
        ("prepare", "synthetic-preparation-intent"),
        ("authorize", "synthetic-authorization-intent"),
    ]
    assert failed == f.calls[-2:][: len(failed)]


@pytest.mark.asyncio
async def test_cancellation_after_preparation_never_authorizes(monkeypatch):
    f = Sequence(monkeypatch)
    f.cancel_after_prepare = True
    task = asyncio.create_task(f.execute())
    with pytest.raises(asyncio.CancelledError):
        await task
    assert f.calls == [("prepare", "synthetic-preparation-intent")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["source_start_id", "capability_id", "preparation_expires_at"]
)
async def test_individually_valid_but_unrelated_receipts_are_rejected(
    monkeypatch, field
):
    f = Sequence(monkeypatch)
    changed = (
        f.preparation.preparation_expires_at + timedelta(seconds=1)
        if field == "preparation_expires_at"
        else uuid4()
    )
    f.authorization = f.authorization.model_copy(
        update={"receipt": f.authorization.receipt.model_copy(update={field: changed})}
    )
    with pytest.raises(gateway.CaptureReceiptInvalid):
        await f.execute()
    assert len(f.calls) == 2


@pytest.mark.asyncio
async def test_key_collision_rejected_before_any_admission_or_remote_work(monkeypatch):
    f = Sequence(monkeypatch)
    with pytest.raises(gateway.CaptureReceiptInvalid):
        await f.execute(client_request_key=f.operation.authorize_idempotency_key)
    assert not f.calls and not f.checks


@pytest.mark.asyncio
async def test_invalid_preparation_never_reaches_authorization(monkeypatch):
    f = Sequence(monkeypatch)
    f.preparation = f.preparation.model_copy(update={"state": "cancelled"})
    with pytest.raises(gateway.CaptureReceiptInvalid):
        await f.execute()
    assert f.calls == [("prepare", "synthetic-preparation-intent")]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["legacy", "missing_creator"])
async def test_sequence_preserves_configured_creator_requirement(monkeypatch, fault):
    f = Sequence(monkeypatch)
    if fault == "missing_creator":
        f.scope = replace(f.scope, creator_actor_id=None)
    else:
        from tests.authority_kernel.scribe_gateway_receiver_test import scope

        f.scope = replace(f.scope, session_request=scope().session_request)
    with pytest.raises(gateway.CaptureOperationUnavailable):
        await f.execute()
    assert not f.calls


@pytest.mark.asyncio
async def test_real_workflow_client_sends_existing_routes_and_original_keys(
    monkeypatch,
):
    f = Sequence(monkeypatch)
    seen = []

    def remote(request):
        action = request.url.path.rsplit("/", 1)[-1]
        assert (
            request.url.path
            == f"/v1/captured-sessions/{f.scope.session_id}/source-start/{action}"
        )
        assert request.headers["X-Tenant-ID"] == str(f.scope.tenant_id)
        assert request.headers["X-Scribe-Creator-Actor-ID"] == f.scope.creator_actor_id
        assert json.loads(request.content) == f.operation.payload.model_dump(
            mode="json"
        )
        seen.append((action, request.headers["Idempotency-Key"]))
        result = f.preparation if action == "prepare" else f.authorization
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
        result = await f.execute(client=receiver)
    assert isinstance(result, NativeStartSequenceResult) and f.checks == 4
    assert seen == [
        ("prepare", "synthetic-preparation-intent"),
        ("authorize", "synthetic-authorization-intent"),
    ]


@pytest.mark.parametrize("change", ["key", "identity", "unknown", "boolean_position"])
def test_closed_sequence_contract_rejects_untrusted_additions(monkeypatch, change):
    f = Sequence(monkeypatch)
    value = f.operation.model_dump(mode="json")
    if change == "key":
        value["authorize_idempotency_key"] = "private sentinel"
    elif change == "boolean_position":
        value["payload"]["first_sequence_number"] = True
    else:
        value["payload" if change == "identity" else "unused"] = {
            "actor_id": "private sentinel"
        }
    with pytest.raises(ValidationError) as error:
        NativeSourceStartSequence.model_validate(value)
    assert "private sentinel" not in str(error.value)


def test_one_existing_facade_request_runs_both_decisions_and_closes_transport(
    monkeypatch,
):
    f = Sequence(monkeypatch)
    connection = client()
    assert isinstance(connection.app, FastAPI)
    connection.app.dependency_overrides[get_admission_client_factory] = (
        lambda: lambda: f
    )
    payload = body()
    payload["operation"] = f.operation.model_dump(mode="json")
    response = connection.post(
        PATH,
        headers=request_headers() | {"Idempotency-Key": "synthetic-preparation-intent"},
        json=payload,
    )
    assert (
        response.status_code == 200 and response.headers["cache-control"] == "no-store"
    )
    assert response.json()["operation"] == "source_start_sequence"
    assert response.json()["receipt"] == NativeStartSequenceResult(
        preparation=f.preparation, authorization=f.authorization
    ).model_dump(mode="json")
    assert len(f.calls) == 2 and f.checks == 4 and f.closed
