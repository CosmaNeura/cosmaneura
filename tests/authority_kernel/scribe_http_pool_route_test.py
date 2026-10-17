"""Real private capture boundaries with an in-memory downstream transport."""

import asyncio
import time
from contextvars import ContextVar
from threading import BoundedSemaphore, get_ident
from uuid import uuid4

import httpx
import pytest
from azure.core.credentials import AccessToken
from fastapi import APIRouter, FastAPI, Request
from pydantic import BaseModel

from app.configuration.scribe_workflow import ScribeWorkflowSettings
from app.routes import scribe_capture_deadline as boundary
from app.services import scribe_http_pool as connections
from app.services.scribe_workflow import build_scribe_workflow_client

ORIGIN = "https://scribe.internal.example"
PATH = "/synthetic-capture"


class Receipt(BaseModel):
    ok: bool


class Credential:
    calls = 0
    closed = False

    async def get_token(self, scope):
        self.calls += 1
        return AccessToken("synthetic-fresh", int(time.time()) + 60)

    async def close(self):
        self.closed = True


class Stream(httpx.AsyncByteStream):
    def __init__(self, *, read_gate=None, close_gate=None):
        self.read_gate, self.close_gate = read_gate, close_gate
        self.close_entered, self.closed = asyncio.Event(), False

    async def __aiter__(self):
        if self.read_gate:
            await self.read_gate.wait()
        yield b'{"ok":true}'

    async def aclose(self):
        self.close_entered.set()
        if self.close_gate:
            await self.close_gate.wait()
        self.closed = True


def application(monkeypatch, handler, *, outer, seconds=1):
    monkeypatch.setattr(boundary, "CAPTURE_REQUEST_SECONDS", seconds)
    original = connections.ScribeHttpPool
    transport = httpx.MockTransport(handler)

    def create(origin):
        return original(origin, transport=transport)

    monkeypatch.setattr(connections, "ScribeHttpPool", create)
    app = FastAPI()
    router = APIRouter(route_class=boundary.CaptureDeadlineRoute)
    parent_thread = get_ident()
    private_context = ContextVar("test_pool_private_scope", default=None)
    credentials = []

    @router.post(PATH)
    async def operation(request: Request):
        assert get_ident() != parent_thread
        assert private_context.get() is None
        private_context.set("synthetic-private-transaction")
        await request.body()
        credential = Credential()
        credentials.append(credential)
        client = build_scribe_workflow_client(
            ScribeWorkflowSettings(
                enabled=True,
                base_url=ORIGIN,
                scope="synthetic/.default",
                managed_identity_client_id="11111111-1111-4111-8111-111111111111",
                timeout_seconds=5,
            ),
            credential_factory=lambda _client_id: credential,
        )
        try:
            result = await client._post(
                "/v1/fixed", uuid4(), Receipt(ok=True), "synthetic-key", Receipt
            )
            return result.model_dump()
        finally:
            await client.close()

    app.include_router(router)
    if outer:
        app.add_middleware(boundary.CaptureBoundary, paths=frozenset({PATH}))
    connections.install_scribe_connections(app, ORIGIN)
    return app, credentials, private_context


@pytest.mark.asyncio
@pytest.mark.parametrize("outer", [False, True])
async def test_real_route_factory_uses_parent_pool_with_private_credentials(
    monkeypatch, outer
):
    parent_loop, parent_thread = asyncio.get_running_loop(), get_ident()
    streams, requests = [], []

    async def downstream(request):
        assert asyncio.get_running_loop() is parent_loop
        assert get_ident() == parent_thread
        assert private_context.get() is None
        requests.append(request)
        stream = Stream()
        streams.append(stream)
        return httpx.Response(
            201, headers={"content-type": "application/json"}, stream=stream
        )

    app, credentials, private_context = application(
        monkeypatch, downstream, outer=outer
    )
    private_context.set("parent-only-context-must-not-reach-worker")
    async with app.router.lifespan_context(app):
        pool = app.state.scribe_http_pool
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            results = await asyncio.gather(
                *[client.post(PATH, content=b"{}") for _ in range(2)]
            )
        assert all(r.status_code == 200 and r.json() == {"ok": True} for r in results)
        assert len(credentials) == 2 and all(
            c.closed and c.calls == 1 for c in credentials
        )
        assert len(requests) == 2 and all(s.closed for s in streams)
        assert not pool._operations and not pool._closed
    assert pool._closed


@pytest.mark.asyncio
@pytest.mark.parametrize("outer", [False, True])
async def test_deadline_retains_worker_slot_until_parent_response_close_joins(
    monkeypatch, outer
):
    release = asyncio.Event()
    stream = Stream(read_gate=asyncio.Event(), close_gate=release)
    calls = []

    async def downstream(request):
        calls.append(request)
        result = stream if len(calls) == 1 else Stream()
        return httpx.Response(
            201, headers={"content-type": "application/json"}, stream=result
        )

    slots = BoundedSemaphore(1)
    monkeypatch.setattr(boundary, "_workers", slots)
    app, _, _ = application(monkeypatch, downstream, outer=outer, seconds=0.08)
    async with app.router.lifespan_context(app):
        pool = app.state.scribe_http_pool
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            first = await client.post(PATH, content=b"{}")
            assert first.status_code == 504
            await asyncio.wait_for(stream.close_entered.wait(), 1)
            assert not stream.closed and len(pool._operations) == 1
            second = await client.post(PATH, content=b"{}")
            assert second.status_code == 503 and len(calls) == 1
            release.set()
            expires = time.monotonic() + 1
            while not slots.acquire(blocking=False):
                assert time.monotonic() < expires
                await asyncio.sleep(0.002)
            slots.release()
            third = await client.post(PATH, content=b"{}")
            assert third.status_code == 200 and len(calls) == 2
        assert stream.closed and not pool._operations


@pytest.mark.asyncio
async def test_wrong_lifespan_loop_fails_closed_without_leaking_worker_slot(
    monkeypatch,
):
    slots = BoundedSemaphore(1)
    monkeypatch.setattr(boundary, "_workers", slots)
    app = FastAPI()
    router = APIRouter(route_class=boundary.CaptureDeadlineRoute)

    @router.post(PATH)
    async def never_reached():
        pytest.fail("route ran with a foreign transport loop")

    app.include_router(router)

    async def make_foreign():
        pool = connections.ScribeHttpPool(
            ORIGIN, transport=httpx.MockTransport(lambda r: None)
        )
        await pool.close()
        return pool

    app.state.scribe_http_pool = await asyncio.to_thread(
        lambda: asyncio.run(make_foreign())
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        for _ in range(2):
            result = await client.post(PATH, content=b"{}")
            assert result.status_code == 503
            assert slots.acquire(blocking=False)
            slots.release()
