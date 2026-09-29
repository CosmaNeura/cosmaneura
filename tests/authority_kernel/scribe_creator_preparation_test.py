"""One creator-checked preparation request; current Core gates remain before/after."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.services import scribe_capture_gateway as gateway
from app.services.scribe_workflow import HttpScribeWorkflowClient

from tests.authority_kernel.scribe_capture_gateway_test import pair
from tests.authority_kernel.scribe_gateway_receiver_test import scope as legacy_scope
from tests.authority_kernel.scribe_release_review_regressions_test import prepared_start
from tests.services.scribe_workflow_test import StaticCredential

KINDS = ("issue_lease", "renew_lease", "reserve_chunks")


class Preparation:
    def __init__(self, monkeypatch, kind, *, configured=True):
        self.scope = prepared_start()[0] if configured else legacy_scope()
        self.operation, self.receipt = pair(kind, self.scope)
        self.calls = []
        self.checks = 0
        self.deny_at = self.change_at = 0
        self.status = 200
        self.malformed = False
        monkeypatch.setattr(gateway, "authorize_admitted_capture", self.authorize)

    def authorize(self, *_):
        self.checks += 1
        if self.checks == self.deny_at:
            raise gateway.CaptureOperationUnavailable
        if self.checks == self.change_at:
            return replace(self.scope, session_id=uuid4())
        return self.scope

    def wire(self, request):
        self.calls.append(request)
        if request.url.path.endswith("/review"):
            return httpx.Response(
                200,
                json={
                    "schema_version": "1.0",
                    "session_id": str(self.scope.session_id),
                    "status": "capturing",
                    "source": None,
                    "working": None,
                    "review_queue": None,
                    "translation_progress": [],
                },
            )
        if request.url.path.endswith("/transcription-status"):
            return httpx.Response(
                200,
                json={
                    "session_id": str(self.scope.session_id),
                    "status": "not_requested",
                    "transcript_available": False,
                    "updated_at": datetime.now(UTC).isoformat(),
                },
            )
        value = (
            {"private_sentinel": True}
            if self.malformed
            else self.receipt.model_dump(mode="json")
        )
        return httpx.Response(self.status, json=value)

    async def execute(self):
        async with httpx.AsyncClient(
            base_url="https://scribe.synthetic.invalid",
            transport=httpx.MockTransport(self.wire),
        ) as wire:
            client = HttpScribeWorkflowClient(
                base_url="https://scribe.synthetic.invalid",
                scope="api://scribe/.default",
                timeout_seconds=5,
                credential=StaticCredential(),
                client=wire,
            )
            return await gateway.execute_capture_operation(
                caller=SimpleNamespace(),
                binding_ref="synthetic-binding",
                workspace_id=uuid4(),
                operation=self.operation,
                client_request_key="retained-preparation-key",
                client=client,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_configured_preparation_uses_one_creator_checked_http_request(
    monkeypatch, kind
):
    f = Preparation(monkeypatch, kind)
    assert await f.execute() == f.receipt
    assert len(f.calls) == 1 and f.checks == 3
    request = f.calls[0]
    assert request.url.path.startswith(f"/v1/captured-sessions/{f.scope.session_id}/")
    assert request.headers["x-scribe-creator-actor-id"] == f.scope.creator_actor_id
    assert request.headers["x-tenant-id"] == str(f.scope.tenant_id)
    assert request.headers["idempotency-key"].startswith("core-capture-")
    assert "creator_actor_id" not in request.content.decode()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_legacy_preparation_keeps_status_read_and_identical_remote_key(
    monkeypatch, kind
):
    configured = Preparation(monkeypatch, kind)
    await configured.execute()
    f = Preparation(monkeypatch, kind, configured=False)
    f.scope = replace(f.scope, operation_ref=configured.scope.operation_ref)
    await f.execute()
    assert len(f.calls) == 2
    assert all("/v1/sessions/" in str(r.url) for r in f.calls)
    assert "x-scribe-creator-actor-id" not in f.calls[-1].headers
    assert (
        f.calls[-1].headers["idempotency-key"]
        == configured.calls[0].headers["idempotency-key"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("boundary", (1, 2, 3))
async def test_revocation_before_dispatch_or_before_receipt_remains_closed(
    monkeypatch, kind, boundary
):
    f = Preparation(monkeypatch, kind)
    f.deny_at = boundary
    with pytest.raises(gateway.CaptureOperationUnavailable):
        await f.execute()
    assert len(f.calls) == (1 if boundary == 3 else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_changed_admission_after_remote_commit_withholds_receipt(
    monkeypatch, kind
):
    f = Preparation(monkeypatch, kind)
    f.change_at = 3
    with pytest.raises(gateway.CaptureReceiptInvalid):
        await f.execute()
    assert len(f.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("status", (401, 404, 409, 503))
async def test_rejection_and_lost_response_never_fall_back_or_retry(
    monkeypatch, kind, status
):
    f = Preparation(monkeypatch, kind)
    f.status = status
    with pytest.raises(
        (gateway.CaptureOperationUnavailable, gateway.CaptureOperationUnconfirmed)
    ):
        await f.execute()
    assert len(f.calls) == 1
    retained = f.calls[0]
    # Explicit recovery keeps the exact mutation key and payload.
    f.status = 200
    assert await f.execute() == f.receipt
    assert len(f.calls) == 2
    assert f.calls[1].headers["idempotency-key"] == retained.headers["idempotency-key"]
    assert f.calls[1].content == retained.content


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_missing_creator_never_dispatches_and_bad_receipt_never_publishes(
    monkeypatch, kind
):
    f = Preparation(monkeypatch, kind)
    original = f.scope
    f.scope = replace(f.scope, creator_actor_id=None)
    with pytest.raises(gateway.CaptureOperationUnavailable):
        await f.execute()
    assert not f.calls
    f.scope = original
    f.malformed = True
    with pytest.raises(gateway.CaptureOperationUnconfirmed) as failure:
        await f.execute()
    assert len(f.calls) == 1 and "private_sentinel" not in str(failure.value)


@pytest.mark.asyncio
async def test_global_encryption_key_still_requires_separate_creator_review(
    monkeypatch,
):
    f = Preparation(monkeypatch, "encryption_key")
    assert await f.execute() == f.receipt
    assert len(f.calls) == 2 and f.calls[0].url.path.endswith("/review")
    assert f.calls[1].url.path == "/v1/audio-encryption-key"
