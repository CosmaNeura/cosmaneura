"""Cross-loop transport ownership under the isolated, synthetic app bootstrap."""

import asyncio
import gzip
import random
import string
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from threading import Event, get_ident
from uuid import uuid4

import httpcore
import httpx
import pytest
from azure.core.credentials import AccessToken
from fastapi import FastAPI
from pydantic import BaseModel

from app.services import scribe_http_pool as pool_module
from app.services.scribe_capture_deadline import CaptureBudget
from app.services.scribe_workflow import (
    HttpScribeWorkflowClient,
    ScribeWorkflowError,
    ScribeWorkflowRetryableError,
)

ORIGIN = "https://scribe.internal.example"


class Expired(Exception):
    pass


class Budget(CaptureBudget):
    def __init__(self):
        super().__init__(30)
        self.owner_thread: int | None = get_ident()

    def check(self):
        if self.cancelled.is_set():
            raise Expired("synthetic budget expired")
        return 10


def in_worker(pool, operation):
    async def run():
        budget = Budget()
        with pool_module.capture_scribe_connections(pool, budget):
            return await operation(budget)

    return asyncio.run(run())


class Stream(httpx.AsyncByteStream):
    def __init__(self, payload=b'{"ok":true}', *, read_gate=None, close_gate=None):
        self.payload = payload
        self.read_gate, self.close_gate = read_gate, close_gate
        self.close_entered, self.closed = asyncio.Event(), False

    async def __aiter__(self):
        if self.read_gate is not None:
            await self.read_gate.wait()
        yield self.payload

    async def aclose(self):
        self.close_entered.set()
        if self.close_gate is not None:
            await self.close_gate.wait()
        self.closed = True


class Transport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self.handler, self.closed, self.requests = handler, False, []

    async def handle_async_request(self, request):
        self.requests.append(request)
        return await self.handler(request)

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_distinct_worker_loops_share_only_transport_and_close_separately():
    parent_loop, parent_thread = asyncio.get_running_loop(), get_ident()
    streams = []
    private_context = ContextVar[str | None]("synthetic_private_sql", default=None)

    async def handler(request):
        assert asyncio.get_running_loop() is parent_loop
        assert get_ident() == parent_thread
        assert private_context.get() is None
        stream = Stream()
        streams.append(stream)
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "set-cookie": "synthetic=discarded",
            },
            stream=stream,
            extensions={"network_stream": object(), "http_version": b"HTTP/1.1"},
        )

    transport = Transport(handler)
    pool = pool_module.ScribeHttpPool(ORIGIN, transport=transport)

    async def request(budget, tenant):
        assert get_ident() != parent_thread
        private_context.set("worker-owned synthetic SQL context")
        async with httpx.AsyncClient(
            transport=pool_module.current_scribe_transport(ORIGIN)
        ) as client:
            result = await client.post(
                ORIGIN + "/v1/fixed",
                headers={
                    "Authorization": "Bearer synthetic-" + tenant,
                    "X-Tenant-ID": tenant,
                },
            )
            assert result.json() == {"ok": True}
            assert "network_stream" not in result.extensions
        assert not transport.closed

    await asyncio.gather(
        *[
            asyncio.to_thread(in_worker, pool, lambda b, t=tenant: request(b, t))
            for tenant in ("tenant-a", "tenant-b")
        ]
    )
    assert {r.headers["authorization"] for r in transport.requests} == {
        "Bearer synthetic-tenant-a",
        "Bearer synthetic-tenant-b",
    }
    assert all("cookie" not in r.headers for r in transport.requests)
    assert all(s.closed for s in streams)
    assert not pool._operations
    await pool.close()
    assert transport.closed


@pytest.mark.asyncio
async def test_actual_workflow_gets_fresh_token_for_every_call():
    async def handler(request):
        return httpx.Response(
            201, headers={"content-type": "application/json"}, stream=Stream()
        )

    transport = Transport(handler)
    pool = pool_module.ScribeHttpPool(ORIGIN, transport=transport)

    class Receipt(BaseModel):
        ok: bool

    class Credential:
        def __init__(self):
            self.calls, self.closed = 0, False

        async def get_token(self, *scopes: str, **kwargs):
            self.calls += 1
            return AccessToken(
                "synthetic-fresh-" + str(self.calls), int(time.time()) + 60
            )

        async def close(self):
            self.closed = True

    async def request(budget):
        credential = Credential()
        client = HttpScribeWorkflowClient(
            base_url=ORIGIN,
            scope="synthetic-scope",
            timeout_seconds=5,
            credential=credential,
            transport=pool_module.current_scribe_transport(ORIGIN),
        )
        tenant = uuid4()
        try:
            for key in ("synthetic-key-one", "synthetic-key-two"):
                result = await client._post(
                    "/v1/fixed", tenant, Receipt(ok=True), key, Receipt
                )
                assert result.ok
        finally:
            await client.close()
        assert credential.calls == 2 and credential.closed

    await asyncio.to_thread(in_worker, pool, request)
    assert [r.headers["authorization"] for r in transport.requests] == [
        "Bearer synthetic-fresh-1",
        "Bearer synthetic-fresh-2",
    ]
    assert [r.headers["idempotency-key"] for r in transport.requests] == [
        "synthetic-key-one",
        "synthetic-key-two",
    ]
    assert not transport.closed
    await pool.close()


@pytest.mark.asyncio
async def test_cancellation_joins_resistant_response_close_before_worker_exit():
    parent = asyncio.get_running_loop()
    reading, release = asyncio.Event(), asyncio.Event()
    stream = Stream(read_gate=asyncio.Event(), close_gate=release)

    async def handler(request):
        reading.set()
        return httpx.Response(200, stream=stream)

    pool = pool_module.ScribeHttpPool(ORIGIN, transport=Transport(handler))
    worker_control = parent.create_future()
    worker_exited = Event()

    async def request(budget):
        parent.call_soon_threadsafe(
            worker_control.set_result,
            (asyncio.get_running_loop(), asyncio.current_task()),
        )
        try:
            async with httpx.AsyncClient(
                transport=pool_module.current_scribe_transport(ORIGIN)
            ) as client:
                await client.get(ORIGIN + "/v1/fixed")
        except asyncio.CancelledError:
            return "cancelled"
        finally:
            worker_exited.set()

    worker = asyncio.create_task(asyncio.to_thread(in_worker, pool, request))
    await reading.wait()
    loop, task = await worker_control
    loop.call_soon_threadsafe(task.cancel)
    await asyncio.wait_for(stream.close_entered.wait(), 1)
    loop.call_soon_threadsafe(task.cancel)  # Repeated cancellation cannot skip join.
    await asyncio.sleep(0.02)
    assert not worker_exited.is_set() and not stream.closed
    assert len(pool._operations) == 1
    release.set()
    assert await asyncio.wait_for(worker, 1) == "cancelled"
    assert stream.closed and not pool._operations
    await pool.close()


@pytest.mark.asyncio
async def test_cancel_before_network_task_begins_has_definitive_join():
    async def handler(request):
        pytest.fail("cancelled before first instruction")

    pool = pool_module.ScribeHttpPool(ORIGIN, transport=Transport(handler))
    operation = pool_module._Operation(
        httpx.Request("GET", ORIGIN + "/v1/fixed"), Budget()
    )
    pool._start(operation)
    assert operation.task is not None
    operation.task.cancel()
    await asyncio.wait_for(asyncio.wrap_future(operation.joined), 1)
    with pytest.raises(httpx.TransportError):
        operation.result.result()
    assert not pool._operations
    await pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_origin", ["http://scribe.internal.example", "https://other.example"]
)
async def test_wrong_origin_is_rejected_before_http(bad_origin):
    transport = Transport(lambda request: None)
    pool = pool_module.ScribeHttpPool(ORIGIN, transport=transport)

    async def request(budget):
        with pytest.raises(httpx.TransportError):
            pool.borrow(bad_origin, budget)

    await asyncio.to_thread(in_worker, pool, request)
    assert not transport.requests
    await pool.close()


@pytest.mark.asyncio
async def test_parent_loop_cannot_claim_private_worker_transport():
    pool = pool_module.ScribeHttpPool(ORIGIN, transport=Transport(lambda request: None))
    with pytest.raises(httpx.TransportError):
        pool.borrow(ORIGIN, Budget())
    await pool.close()


@pytest.mark.asyncio
async def test_closed_pool_does_not_fall_back_to_a_new_connection():
    transport = Transport(lambda request: None)
    pool = pool_module.ScribeHttpPool(ORIGIN, transport=transport)
    await pool.close()

    async def request(budget):
        with pytest.raises(httpx.TransportError):
            pool_module.current_scribe_transport(ORIGIN)

    await asyncio.to_thread(in_worker, pool, request)
    assert not transport.requests


@pytest.mark.asyncio
async def test_budget_expiry_discards_response_and_joins_close():
    stream = Stream()

    async def handler(request):
        assert budget is not None
        budget.cancelled.set()
        return httpx.Response(200, stream=stream)

    pool = pool_module.ScribeHttpPool(ORIGIN, transport=Transport(handler))
    budget = None

    async def request(value):
        nonlocal budget
        budget = value
        async with httpx.AsyncClient(
            transport=pool_module.current_scribe_transport(ORIGIN)
        ) as client:
            with pytest.raises(httpx.TransportError):
                await client.get(ORIGIN + "/v1/fixed")

    await asyncio.to_thread(in_worker, pool, request)
    assert stream.closed
    assert not pool._operations
    await pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("encoded", [False, True])
async def test_complete_body_is_validated_on_worker_with_original_response_bounds(
    encoded,
):
    content = b'{"ok":true}'

    async def handler(request):
        headers = {"content-type": "application/json"}
        if encoded:
            headers["content-encoding"] = "gzip"
        return httpx.Response(
            200,
            headers=headers,
            stream=Stream(gzip.compress(content) if encoded else content),
        )

    pool = pool_module.ScribeHttpPool(ORIGIN, transport=Transport(handler))

    class Receipt(BaseModel):
        ok: bool

    class Credential:
        async def get_token(self, *scopes: str, **kwargs):
            return AccessToken("synthetic-fresh", int(time.time()) + 60)

        async def close(self):
            pass

    async def request(budget):
        client = HttpScribeWorkflowClient(
            base_url=ORIGIN,
            scope="synthetic-scope",
            timeout_seconds=5,
            credential=Credential(),
            transport=pool_module.current_scribe_transport(ORIGIN),
        )
        try:
            with pytest.raises(
                ScribeWorkflowRetryableError, match="scribe_response_too_large"
            ):
                await client._post(
                    "/v1/fixed",
                    uuid4(),
                    Receipt(ok=True),
                    "synthetic-key",
                    Receipt,
                    max_bytes=2,
                )
        finally:
            await client.close()

    await asyncio.to_thread(in_worker, pool, request)
    assert not pool._operations
    await pool.close()


@pytest.mark.asyncio
async def test_compressed_buffer_does_not_decode_whole_envelope_before_existing_bound(
    monkeypatch,
):
    randomizer = random.Random(0)
    content = (
        '{"ok":true,"padding":"'
        + "".join(randomizer.choices(string.ascii_letters + string.digits, k=262_144))
        + '"}'
    ).encode()
    compressed = gzip.compress(content)
    assert len(compressed) > 2 * 65_536
    raw_chunks = []
    original = pool_module._BufferedBody.__aiter__

    async def observed(body):
        async for chunk in original(body):
            raw_chunks.append(len(chunk))
            yield chunk

    monkeypatch.setattr(pool_module._BufferedBody, "__aiter__", observed)

    async def handler(request):
        return httpx.Response(
            200,
            headers={"content-type": "application/json", "content-encoding": "gzip"},
            stream=Stream(compressed),
        )

    pool = pool_module.ScribeHttpPool(ORIGIN, transport=Transport(handler))

    class Receipt(BaseModel):
        ok: bool
        padding: str

    class Credential:
        async def get_token(self, *scopes: str, **kwargs):
            return AccessToken("synthetic-fresh", int(time.time()) + 60)

        async def close(self):
            pass

    async def request(budget):
        client = HttpScribeWorkflowClient(
            base_url=ORIGIN,
            scope="synthetic-scope",
            timeout_seconds=5,
            credential=Credential(),
            transport=pool_module.current_scribe_transport(ORIGIN),
        )
        try:
            with pytest.raises(
                ScribeWorkflowRetryableError, match="scribe_response_too_large"
            ):
                await client._post(
                    "/v1/fixed",
                    uuid4(),
                    Receipt(ok=True, padding=""),
                    "synthetic-key",
                    Receipt,
                    max_bytes=16_384,
                )
        finally:
            await client.close()

    await asyncio.to_thread(in_worker, pool, request)
    # The actual HTTPX decoder and workflow bound stop after the first normal
    # raw chunk, before the remaining compressed envelope is decompressed.
    assert raw_chunks == [65_536]
    await pool.close()


@pytest.mark.asyncio
async def test_pooled_timeout_keeps_existing_workflow_classification_without_exception_text():
    async def handler(request):
        raise httpx.ReadTimeout("synthetic transport detail must not escape")

    pool = pool_module.ScribeHttpPool(ORIGIN, transport=Transport(handler))

    class Receipt(BaseModel):
        ok: bool

    class Credential:
        async def get_token(self, *scopes: str, **kwargs):
            return AccessToken("synthetic-fresh", int(time.time()) + 60)

        async def close(self):
            pass

    async def request(budget):
        client = HttpScribeWorkflowClient(
            base_url=ORIGIN,
            scope="synthetic-scope",
            timeout_seconds=5,
            credential=Credential(),
            transport=pool_module.current_scribe_transport(ORIGIN),
        )
        try:
            with pytest.raises(ScribeWorkflowRetryableError) as caught:
                await client._post(
                    "/v1/fixed", uuid4(), Receipt(ok=True), "synthetic-key", Receipt
                )
            assert str(caught.value) == "scribe_request_timeout"
            assert "synthetic transport detail" not in str(caught.value)
        finally:
            await client.close()

    await asyncio.to_thread(in_worker, pool, request)
    assert not pool._operations
    await pool.close()


def test_unbound_non_capture_work_keeps_original_client_path():
    assert pool_module.current_scribe_transport(ORIGIN) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,status,media_type,upload,payload,read_error,close_error",
    [
        ("POST", 409, "application/json", False, b"", True, False),
        ("GET", 409, "application/json", False, b"", True, False),
        ("POST", 403, "application/json", False, b"", True, False),
        ("POST", 307, "application/json", False, b"", True, False),
        ("POST", 408, "application/json", False, b"", True, False),
        ("GET", 429, "application/json", False, b"", True, False),
        ("POST", 500, "application/json", False, b"", True, False),
        ("GET", 201, "application/json", False, b"", True, False),
        ("POST", 200, "text/plain", False, b"", True, False),
        ("GET", 200, "text/plain", False, b"", True, False),
        ("POST", 409, "Application/JSON", True, b"", True, False),
        ("POST", 409, " application/json", True, b"", True, False),
        (
            "POST",
            409,
            "application/json",
            True,
            b'{"detail":"Upload authorization expired; use a new Idempotency-Key."}',
            False,
            False,
        ),
        ("POST", 409, "application/json", True, b"x" * 5000, False, False),
        ("POST", 409, "application/json", True, b"", True, False),
        ("POST", 200, "application/json", False, b"", True, False),
        ("POST", 403, "application/json", False, b"", True, True),
        ("POST", 200, "application/json", False, b'{"ok":true}', False, True),
        ("POST", 200, "application/json", False, b'{"ok":true}', False, False),
    ],
)
@pytest.mark.parametrize("encoded", [False, True])
async def test_pooled_and_original_header_body_close_errors_match(
    method, status, media_type, upload, payload, read_error, close_error, encoded
):
    class Receipt(BaseModel):
        ok: bool

    class Credential:
        async def get_token(self, *scopes: str, **kwargs):
            return AccessToken("synthetic-fresh", int(time.time()) + 60)

        async def close(self):
            pass

    class ContractStream(Stream):
        reads = 0

        async def __aiter__(self):
            self.reads += 1
            if read_error:
                raise httpx.ReadTimeout("synthetic-private read details")
            yield gzip.compress(payload) if encoded else payload

        async def aclose(self):
            self.closed = True
            if close_error:
                raise httpx.ReadTimeout("synthetic-private close details")

    streams = []

    async def handler(request):
        assert pool_module.COLLECTION_EXTENSION not in request.extensions
        stream = ContractStream()
        streams.append(stream)
        return httpx.Response(
            status,
            headers={
                "content-type": media_type,
                **({"content-encoding": "gzip"} if encoded else {}),
            },
            stream=stream,
        )

    async def call(client):
        try:
            if method == "POST":
                result = await client._post(
                    "/v1/fixed",
                    uuid4(),
                    Receipt(ok=True),
                    "synthetic-key",
                    Receipt,
                    upload_authorization=upload,
                )
            else:
                result = await client._get_resource(
                    uuid4(), "/v1/fixed", Receipt, max_bytes=16_384
                )
            return ("accepted", result.model_dump())
        except ScribeWorkflowError as error:
            return type(error).__name__, str(error)

    transport = Transport(handler)
    async with httpx.AsyncClient(base_url=ORIGIN, transport=transport) as original:
        client = HttpScribeWorkflowClient(
            base_url=ORIGIN,
            scope="synthetic-scope",
            timeout_seconds=5,
            credential=Credential(),
            client=original,
        )
        try:
            before = await call(client)
        finally:
            await client.close()
    assert streams[0].closed
    pool = pool_module.ScribeHttpPool(ORIGIN, transport=Transport(handler))

    async def request(budget):
        client = HttpScribeWorkflowClient(
            base_url=ORIGIN,
            scope="synthetic-scope",
            timeout_seconds=5,
            credential=Credential(),
            transport=pool_module.current_scribe_transport(ORIGIN),
        )
        try:
            return await call(client)
        finally:
            await client.close()

    after = await asyncio.to_thread(in_worker, pool, request)
    assert after == before
    assert streams[1].closed and streams[1].reads == streams[0].reads
    assert "synthetic-private" not in str(after)
    assert not pool._operations
    await pool.close()


@pytest.mark.parametrize(
    "statuses,limit,upload",
    [
        ((200.0,), 16_384, False),
        ([200], 16_384, False),
        ((True,), 16_384, False),
        ((200,), True, False),
        ((200,), 0, False),
        ((200,), 16_384, 1),
        ((200,), 16_384, True),
        ((200, 202), 16_384, False),
        ((200,), pool_module.MAX_RESPONSE_BYTES + 1, False),
    ],
)
def test_response_collection_is_immutable_closed_numeric_metadata(
    statuses, limit, upload
):
    with pytest.raises(ValueError, match="scribe_response_collection_invalid"):
        pool_module.ResponseCollection(statuses, limit, upload)


@pytest.mark.asyncio
async def test_actual_httpcore_connection_is_reused_without_a_socket():
    class Backend(httpcore.AsyncMockBackend):
        def __init__(self):
            super().__init__(
                [
                    b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}",
                    b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}",
                ]
            )
            self.connections = 0

        async def connect_tcp(self, *args, **kwargs):
            self.connections += 1
            return await super().connect_tcp(*args, **kwargs)

    backend = Backend()
    pool = pool_module.ScribeHttpPool(ORIGIN)
    # httpcore's existing fake network backend exercises its real HTTP/1.1
    # keepalive lifecycle. It creates no socket, TLS handshake or external I/O.
    assert isinstance(pool._transport, httpx.AsyncHTTPTransport)
    pool._transport._pool._network_backend = backend

    async def request(budget):
        async with httpx.AsyncClient(
            transport=pool_module.current_scribe_transport(ORIGIN)
        ) as client:
            assert (await client.get(ORIGIN + "/v1/fixed")).json() == {}

    await asyncio.to_thread(in_worker, pool, request)
    await asyncio.to_thread(in_worker, pool, request)
    assert backend.connections == 1
    await pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", [False, True])
async def test_cancellation_at_end_of_body_still_joins_stream_close(planned):
    release = asyncio.Event()
    stream = Stream(close_gate=release)

    async def handler(request):
        return httpx.Response(
            200, headers={"content-type": "application/json"}, stream=stream
        )

    parent = asyncio.get_running_loop()
    worker_control = parent.create_future()
    exited = Event()
    pool = pool_module.ScribeHttpPool(ORIGIN, transport=Transport(handler))

    async def request(budget):
        parent.call_soon_threadsafe(
            worker_control.set_result,
            (asyncio.get_running_loop(), asyncio.current_task()),
        )
        try:
            async with httpx.AsyncClient(
                transport=pool_module.current_scribe_transport(ORIGIN)
            ) as client:
                await client.get(
                    ORIGIN + "/v1/fixed",
                    extensions={
                        pool_module.COLLECTION_EXTENSION: pool_module.ResponseCollection(
                            (200,), 16_384
                        )
                    }
                    if planned
                    else None,
                )
        except asyncio.CancelledError:
            return "cancelled"
        finally:
            exited.set()

    worker = asyncio.create_task(asyncio.to_thread(in_worker, pool, request))
    await stream.close_entered.wait()
    loop, task = await worker_control
    loop.call_soon_threadsafe(task.cancel)
    await asyncio.sleep(0.02)
    assert not exited.is_set() and not stream.closed
    release.set()
    assert await asyncio.wait_for(worker, 1) == "cancelled"
    assert stream.closed and not pool._operations
    await pool.close()


@pytest.mark.asyncio
async def test_shutdown_cancellation_joins_and_closes_transport_once():
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowClose(Transport):
        closes = 0

        async def aclose(self):
            self.closes += 1
            entered.set()
            await release.wait()
            self.closed = True

    transport = SlowClose(lambda request: None)
    pool = pool_module.ScribeHttpPool(ORIGIN, transport=transport)
    first = asyncio.create_task(pool.close())
    await entered.wait()
    first.cancel()
    second = asyncio.create_task(pool.close())
    await asyncio.sleep(0.02)
    assert not first.done() and not second.done() and not transport.closed
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await second
    assert transport.closed and transport.closes == 1


@pytest.mark.asyncio
async def test_shutdown_joins_active_response_before_closing_shared_connections():
    release, entered = asyncio.Event(), asyncio.Event()
    stream = Stream(read_gate=asyncio.Event(), close_gate=release)

    async def handler(request):
        entered.set()
        return httpx.Response(200, stream=stream)

    transport = Transport(handler)
    pool = pool_module.ScribeHttpPool(ORIGIN, transport=transport)

    async def request(budget):
        async with httpx.AsyncClient(
            transport=pool_module.current_scribe_transport(ORIGIN)
        ) as client:
            with pytest.raises(httpx.TransportError):
                await client.get(ORIGIN + "/v1/fixed")

    worker = asyncio.create_task(asyncio.to_thread(in_worker, pool, request))
    await entered.wait()
    closing = asyncio.create_task(pool.close())
    await stream.close_entered.wait()
    await asyncio.sleep(0.02)
    assert not closing.done() and not worker.done() and not transport.closed
    release.set()
    await asyncio.wait_for(asyncio.gather(closing, worker), 1)
    assert stream.closed and transport.closed and not pool._operations


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin",
    [
        "http://scribe.internal.example",
        "https://scribe.internal.example:444",
        "https://user:secret@scribe.internal.example",
        ORIGIN + "/path",
        ORIGIN + "?query=value",
        ORIGIN + "#fragment",
        ORIGIN + " ",
    ],
)
async def test_pool_requires_same_closed_https_configuration(origin):
    with pytest.raises(ValueError, match="scribe_connection_origin_invalid"):
        pool_module.ScribeHttpPool(origin, transport=Transport(lambda request: None))


@pytest.mark.asyncio
async def test_raw_response_cap_joins_cleanup_and_never_returns_partial_bytes(
    monkeypatch,
):
    monkeypatch.setattr(pool_module, "MAX_RESPONSE_BYTES", 8)
    stream = Stream(payload=b"123456789")

    async def handler(request):
        return httpx.Response(200, stream=stream)

    pool = pool_module.ScribeHttpPool(ORIGIN, transport=Transport(handler))

    async def request(budget):
        async with httpx.AsyncClient(
            transport=pool_module.current_scribe_transport(ORIGIN)
        ) as client:
            with pytest.raises(httpx.TransportError):
                await client.get(ORIGIN + "/v1/fixed")

    await asyncio.to_thread(in_worker, pool, request)
    assert stream.closed and not pool._operations
    await pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("method,extra_bytes", [("GET", 16_384), ("POST", 32_768)])
async def test_existing_metadata_and_prepared_response_allowances_reach_transport(
    method, extra_bytes
):
    from app.services.scribe_creator_review_contract import MAX_CREATOR_REVIEW_BYTES

    stream = Stream()

    async def handler(request):
        return httpx.Response(
            200, headers={"content-type": "application/json"}, stream=stream
        )

    transport = Transport(handler)
    pool = pool_module.ScribeHttpPool(ORIGIN, transport=transport)

    class Receipt(BaseModel):
        ok: bool

    class Credential:
        async def get_token(self, *scopes: str, **kwargs) -> AccessToken:
            return AccessToken("synthetic-fresh", int(time.time()) + 60)

        async def close(self):
            pass

    async def request(budget):
        client = HttpScribeWorkflowClient(
            base_url=ORIGIN,
            scope="synthetic-scope",
            timeout_seconds=5,
            credential=Credential(),
            transport=pool_module.current_scribe_transport(ORIGIN),
        )
        try:
            limit = MAX_CREATOR_REVIEW_BYTES + extra_bytes
            if method == "GET":
                result = await client._get_resource(
                    uuid4(), "/v1/fixed", Receipt, max_bytes=limit
                )
            else:
                result = await client._post(
                    "/v1/fixed",
                    uuid4(),
                    Receipt(ok=True),
                    "synthetic-key",
                    Receipt,
                    max_bytes=limit,
                )
            assert result == Receipt(ok=True)
        finally:
            await client.close()

    await asyncio.to_thread(in_worker, pool, request)
    assert len(transport.requests) == 1 and stream.closed and not pool._operations
    await pool.close()


@pytest.mark.asyncio
async def test_request_bound_stays_independent_of_larger_response_bound(monkeypatch):
    monkeypatch.setattr(pool_module, "MAX_BODY_BYTES", 8)
    monkeypatch.setattr(pool_module, "MAX_RESPONSE_BYTES", 16)
    stream = Stream(payload=b"123456789012")

    async def handler(request):
        return httpx.Response(200, stream=stream)

    transport = Transport(handler)
    pool = pool_module.ScribeHttpPool(ORIGIN, transport=transport)

    async def request(budget):
        async with httpx.AsyncClient(
            transport=pool_module.current_scribe_transport(ORIGIN)
        ) as client:
            result = await client.get(ORIGIN + "/v1/fixed")
            assert result.content == b"123456789012"
            with pytest.raises(httpx.TransportError, match="scribe_request_too_large"):
                await client.post(ORIGIN + "/v1/fixed", content=b"123456789")

    await asyncio.to_thread(in_worker, pool, request)
    assert len(transport.requests) == 1 and stream.closed and not pool._operations
    await pool.close()


@pytest.mark.asyncio
async def test_lifespan_preserves_existing_callbacks_and_owns_pool(monkeypatch):
    order = []

    @asynccontextmanager
    async def previous(app):
        order.append("original-start")
        yield {"synthetic": True}
        assert app.state.scribe_http_pool is None
        order.append("original-stop")

    created = []
    original_pool = pool_module.ScribeHttpPool

    def create(origin):
        result = original_pool(origin, transport=Transport(lambda request: None))
        created.append(result)
        return result

    monkeypatch.setattr(pool_module, "ScribeHttpPool", create)
    app = FastAPI(lifespan=previous)
    pool_module.install_scribe_connections(app, ORIGIN)
    async with app.router.lifespan_context(app) as state:
        assert state == {"synthetic": True}
        assert app.state.scribe_http_pool is created[0]
        assert created[0].loop is asyncio.get_running_loop()
        assert not created[0]._transport.closed
    assert created[0]._transport.closed
    assert order == ["original-start", "original-stop"]
