"""ASGI-loop HTTP connections, with private capture-worker request ownership.

This pool shares only TCP/TLS resources. Cookies, request headers, patient
authorization and SQL remain request-local. Service-identity SDK ownership,
when enabled, belongs to its separate configuration adapter. No live response stream crosses
event loops; a cancelled worker joins its exact network operation's cleanup.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from concurrent.futures import Future
from contextlib import asynccontextmanager, contextmanager
from contextvars import Context, ContextVar
from dataclasses import dataclass, field
from threading import Event, get_ident
from typing import Protocol

import httpx

MAX_BODY_BYTES = 32 * 1024 * 1024 + 8192
# Existing prepared-session responses include the creator-review envelope.
# Keep the request bound independent of these per-operation response allowances.
MAX_RESPONSE_BYTES = 32 * 1024 * 1024 + 32_768
COLLECTION_EXTENSION = "cosmaneura_scribe_response_collection"


@dataclass(frozen=True)
class ResponseCollection:
    success_statuses: tuple[int, ...]
    max_bytes: int
    upload_expiry: bool = False

    def __post_init__(self):
        if (
            type(self.success_statuses) is not tuple
            or any(type(status) is not int for status in self.success_statuses)
            or self.success_statuses not in ((200,), (200, 201))
            or not isinstance(self.max_bytes, int)
            or isinstance(self.max_bytes, bool)
            or not 0 < self.max_bytes <= MAX_RESPONSE_BYTES
            or not isinstance(self.upload_expiry, bool)
            or (self.upload_expiry and self.success_statuses != (200, 201))
        ):
            raise ValueError("scribe_response_collection_invalid")

    def body_limit(self, response: httpx.Response) -> int | None:
        media_type = response.headers.get("content-type", "").split(";", 1)[0]
        if response.status_code == 409:
            # The existing upload-expiry path uses this exact comparison;
            # successful JSON responses use normalized media types instead.
            return (
                4096
                if self.upload_expiry and media_type == "application/json"
                else None
            )
        return (
            self.max_bytes
            if response.status_code in self.success_statuses
            and media_type.strip().lower() == "application/json"
            else None
        )


class CaptureBudget(Protocol):
    owner_thread: int | None

    def check(self) -> float: ...


@dataclass(eq=False)
class _Operation:
    request: httpx.Request
    budget: CaptureBudget
    collection: ResponseCollection | None = None
    result: Future[httpx.Response] = field(default_factory=Future)
    joined: Future[None] = field(default_factory=Future)
    cancelled: Event = field(default_factory=Event)
    task: asyncio.Task[None] | None = None


class _BufferedBody(httpx.AsyncByteStream):
    """Immutable bytes only; no socket, callback or loop-owned stream state."""

    def __init__(self, body: bytes):
        self._body = body

    async def __aiter__(self):
        # Preserve normal network-sized input to HTTPX's content decoder.
        # Feeding an entire compressed envelope at once could inflate all of
        # it before the existing worker's decoded response bound is checked.
        for offset in range(0, len(self._body), 65_536):
            yield self._body[offset : offset + 65_536]

    async def aclose(self) -> None:
        self._body = b""


class _CollectingBody(httpx.AsyncByteStream):
    """Parent-only decoder input; actual network closure belongs to _send."""

    def __init__(
        self, stream: httpx.AsyncByteStream, operation: _Operation, body: bytearray
    ):
        self._stream, self._operation, self._body = stream, operation, body

    async def __aiter__(self):
        async for raw in self._stream:
            for offset in range(0, len(raw), 65_536):
                self._operation.budget.check()
                if self._operation.cancelled.is_set():
                    raise asyncio.CancelledError
                chunk = raw[offset : offset + 65_536]
                if len(self._body) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise httpx.TransportError("scribe_response_too_large")
                self._body.extend(chunk)
                yield chunk

    async def aclose(self) -> None:
        # HTTPX's decoded view closes at EOF. It must not mark the actual
        # network response closed before its shielded cleanup is joined.
        pass


class ScribeHttpPool:
    def __init__(
        self, origin: str, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        url = httpx.URL(origin)
        if (
            url.scheme != "https"
            or not url.host
            or url.port not in (None, 443)
            or url.userinfo
            or url.path != "/"
            or url.query
            or url.fragment
            or any(character.isspace() for character in origin)
        ):
            raise ValueError("scribe_connection_origin_invalid")
        self.loop = asyncio.get_running_loop()
        self._origin = (url.scheme, url.host, url.port)
        self._closed = False
        self._closing: asyncio.Task[None] | None = None
        self._operations: set[_Operation] = set()
        self._transport = transport or httpx.AsyncHTTPTransport(
            trust_env=False,
            retries=0,
            limits=httpx.Limits(
                max_connections=8, max_keepalive_connections=4, keepalive_expiry=30
            ),
        )

    def borrow(self, origin: str, budget: CaptureBudget) -> httpx.AsyncBaseTransport:
        url = httpx.URL(origin)
        if (
            self._closed
            or (url.scheme, url.host, url.port) != self._origin
            or budget.owner_thread != get_ident()
            or asyncio.get_running_loop() is self.loop
        ):
            raise httpx.TransportError("scribe_connections_unavailable")
        budget.check()
        return _Borrowed(self, budget)

    def _submit(self, operation: _Operation) -> None:
        try:
            self.loop.call_soon_threadsafe(self._start, operation, context=Context())
        except RuntimeError:
            operation.result.set_exception(
                httpx.TransportError("scribe_connections_unavailable")
            )
            operation.joined.set_result(None)

    def _start(self, operation: _Operation) -> None:
        if self._closed or operation.cancelled.is_set():
            operation.result.set_exception(
                httpx.TransportError("scribe_connections_unavailable")
            )
            operation.joined.set_result(None)
            return
        self._operations.add(operation)
        operation.task = self.loop.create_task(self._send(operation))
        operation.task.add_done_callback(lambda task: self._finished(operation, task))

    def _finished(self, operation: _Operation, task: asyncio.Task[None]) -> None:
        # Cancellation before a coroutine's first instruction cannot run its
        # finally block. A completed task has no remaining I/O to join.
        self._operations.discard(operation)
        if not task.cancelled():
            task.exception()
        if not operation.joined.done():
            operation.result.set_exception(
                httpx.TransportError("scribe_request_unconfirmed")
            )
            operation.joined.set_result(None)

    def _cancel(self, operation: _Operation) -> None:
        operation.cancelled.set()

        def cancel():
            if operation.task is not None and not operation.task.done():
                operation.task.cancel()

        try:
            self.loop.call_soon_threadsafe(cancel)
        except RuntimeError:
            # Never certify cleanup or release a waiting worker just because
            # its transport owner disappeared. Normal shutdown joins first.
            pass

    async def _send(self, operation: _Operation) -> None:
        response = None
        answer = None
        error = None
        try:
            operation.budget.check()
            if operation.cancelled.is_set():
                raise asyncio.CancelledError
            response = await self._transport.handle_async_request(operation.request)
            body = bytearray()
            if not isinstance(response.stream, httpx.AsyncByteStream):
                raise httpx.TransportError("scribe_response_stream_invalid")
            collecting = _CollectingBody(response.stream, operation, body)
            if operation.collection is None:
                async for _ in collecting:
                    pass
            else:
                limit = operation.collection.body_limit(response)
                if limit is not None:
                    # Reuse HTTPX's public decoder with a non-closing view.
                    # The original worker still classifies headers, limits,
                    # expiry literals and models. An over-limit prefix is
                    # handed back solely so that original limit rejects it.
                    decoded = httpx.Response(
                        response.status_code,
                        headers=response.headers.raw,
                        stream=collecting,
                    )
                    length = 0
                    async for chunk in decoded.aiter_bytes():
                        operation.budget.check()
                        if operation.cancelled.is_set():
                            raise asyncio.CancelledError
                        length += len(chunk)
                        if length > limit:
                            break
                    await decoded.aclose()
            operation.budget.check()
            if operation.cancelled.is_set():
                raise asyncio.CancelledError
            # Do not return connection/socket extensions or a live async stream.
            answer = httpx.Response(
                response.status_code,
                headers=response.headers.raw,
                stream=_BufferedBody(bytes(body)),
                extensions={
                    name: response.extensions[name]
                    for name in ("http_version", "reason_phrase")
                    if name in response.extensions
                },
            )
        except BaseException as exc:
            error = exc
        finally:
            if response is not None:
                try:
                    closing = self.loop.create_task(response.aclose())
                    while not closing.done():
                        try:
                            await asyncio.shield(closing)
                        except asyncio.CancelledError:
                            operation.cancelled.set()
                    closing.result()
                except BaseException as exc:
                    error = exc
            self._operations.discard(operation)
            if error is not None or operation.cancelled.is_set():
                operation.result.set_exception(
                    httpx.TimeoutException("scribe_request_timeout")
                    if isinstance(error, httpx.TimeoutException)
                    and not operation.cancelled.is_set()
                    else httpx.TransportError("scribe_request_unconfirmed")
                )
            else:
                assert answer is not None
                operation.result.set_result(answer)
            operation.joined.set_result(None)

    async def close(self) -> None:
        if asyncio.get_running_loop() is not self.loop:
            raise RuntimeError("scribe_connections_wrong_loop")
        self._closed = True
        if self._closing is None:
            self._closing = self.loop.create_task(self._close(), context=Context())
        cancelled = False
        while not self._closing.done():
            try:
                await asyncio.shield(self._closing)
            except asyncio.CancelledError:
                cancelled = True
        self._closing.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _close(self) -> None:
        operations = tuple(self._operations)
        for operation in operations:
            self._cancel(operation)
        for operation in operations:
            await _join(operation.joined)
        await self._transport.aclose()


async def _join(completion: Future[None]) -> None:
    # Cancellation cannot release a private worker's slot while its network
    # operation/response close is still running, even if cleanup resists it.
    wrapped = asyncio.wrap_future(completion)
    while not wrapped.done():
        try:
            await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            continue
    wrapped.result()


class _Borrowed(httpx.AsyncBaseTransport):
    def __init__(self, pool: ScribeHttpPool, budget: CaptureBudget):
        self._pool, self._budget = pool, budget
        self._closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._closed or self._budget.owner_thread != get_ident():
            raise httpx.TransportError("scribe_connections_unavailable")
        self._budget.check()
        url = request.url
        if url.userinfo or (url.scheme, url.host, url.port) != self._pool._origin:
            raise httpx.TransportError("scribe_connection_origin_changed")
        body = await request.aread()
        if len(body) > MAX_BODY_BYTES:
            raise httpx.TransportError("scribe_request_too_large")
        self._budget.check()
        collection = request.extensions.get(COLLECTION_EXTENSION)
        if collection is not None and not isinstance(collection, ResponseCollection):
            raise httpx.TransportError("scribe_response_collection_invalid")
        operation = _Operation(
            httpx.Request(
                request.method,
                url,
                headers=request.headers.raw,
                content=body,
                extensions={"timeout": dict(request.extensions.get("timeout", {}))},
            ),
            self._budget,
            collection,
        )
        result = asyncio.wrap_future(operation.result)
        self._pool._submit(operation)
        try:
            answer = await asyncio.shield(result)
            self._budget.check()
            return answer
        except BaseException:
            self._pool._cancel(operation)
            await _join(operation.joined)
            # Consume a rejected network result after cancellation, without
            # displaying its exception or allowing it to become an unhandled log.
            if result.done() and not result.cancelled():
                result.exception()
            raise

    async def aclose(self) -> None:
        # The request already joins its own operation. Never close a shared pool.
        self._closed = True


_capture_pool: ContextVar[tuple[ScribeHttpPool, CaptureBudget] | None] = ContextVar(
    "capture_scribe_connections", default=None
)


@contextmanager
def capture_scribe_connections(
    pool: ScribeHttpPool | None, budget: CaptureBudget
) -> Iterator[None]:
    token = _capture_pool.set(None if pool is None else (pool, budget))
    try:
        yield
    finally:
        _capture_pool.reset(token)


def current_scribe_transport(origin: str) -> httpx.AsyncBaseTransport | None:
    current = _capture_pool.get()
    return None if current is None else current[0].borrow(origin, current[1])


def install_scribe_connections(application, origin: str | None) -> None:
    previous = application.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with previous(app) as state:
            pool = None if origin is None else ScribeHttpPool(origin)
            app.state.scribe_http_pool = pool
            try:
                yield state
            finally:
                app.state.scribe_http_pool = None
                if pool is not None:
                    await pool.close()

    application.router.lifespan_context = lifespan
