"""Before-dependency capture deadline with bounded, privately owned workers."""

import asyncio
import time
from concurrent.futures import Future
from contextvars import Context
from threading import BoundedSemaphore, Lock, Thread
from typing import ClassVar

from anyio import create_task_group
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute, iter_route_contexts
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.routes.authority_errors import agent_problem_response
from app.services.scribe_capture_deadline import (
    CaptureBudget,
    CaptureDeadlineExceeded,
    capture_budget,
)

CAPTURE_REQUEST_SECONDS = 90.0
ERROR_SEND_SECONDS = 0.25
# Largest existing source envelope (24 MiB), plus the facade's closed metadata.
# This outer cap does not replace any smaller operation/model/request validator.
MAX_CAPTURE_RESPONSE_BYTES = 24 * 1024 * 1024 + 8192
_workers = BoundedSemaphore(4)
_error_workers = BoundedSemaphore(4)


def problem(status: int) -> JSONResponse:
    response = agent_problem_response(
        status_code=status,
        code="CAPTURE_REQUEST_UNCONFIRMED",
        title="Capture unavailable",
        detail="The request was not confirmed. Retained work requires explicit recovery.",
        retryable=True,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Cosmaneura-Capture-Version"] = "1"
    return response


class _Ownership:
    def __init__(self, workers: BoundedSemaphore):
        self.workers = workers
        self.lock = Lock()
        self.remaining = 2  # Private worker and public handler.

    def retain(self) -> None:
        with self.lock:
            self.remaining += 1

    def release(self) -> None:
        with self.lock:
            self.remaining -= 1
            if self.remaining == 0:
                self.workers.release()


class _CaptureBoundary:
    """Isolate the complete selected application, including response middleware."""

    app: ASGIApp

    _settling: ClassVar[set[asyncio.Future]] = set()

    async def _dispatch(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.app(scope, receive, send)

    @classmethod
    def _abandon(cls, task: asyncio.Future) -> None:
        cls._settling.add(task)
        task.cancel()

        def settled(result):
            cls._settling.discard(result)
            if not result.cancelled():
                result.exception()

        task.add_done_callback(settled)

    @classmethod
    async def _send_until(
        cls,
        message: Message,
        send: Send,
        expires_at: float,
        ownership: _Ownership | None = None,
    ) -> None:
        if expires_at <= time.monotonic():
            raise CaptureDeadlineExceeded
        task = asyncio.ensure_future(send(message))
        if ownership is not None:
            ownership.retain()
            task.add_done_callback(lambda _: ownership.release())
        try:
            done, _ = await asyncio.wait(
                {task}, timeout=max(0, expires_at - time.monotonic())
            )
            if not done:
                cls._abandon(task)
                raise CaptureDeadlineExceeded
            task.result()
        except asyncio.CancelledError:
            cls._abandon(task)
            raise

    async def _fail(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        status: int,
        ownership: _Ownership | None = None,
    ) -> None:
        error_slot = ownership is None
        if error_slot:
            error_workers = _error_workers
            if not error_workers.acquire(blocking=False):
                return  # No additional sender is admitted when error delivery is full.
            ownership = _Ownership(error_workers)
            ownership.release()  # This failure has no private application worker.
        expires_at = time.monotonic() + ERROR_SEND_SECONDS

        async def bounded_send(message):
            await self._send_until(message, send, expires_at, ownership)

        try:
            await problem(status)(scope, receive, bounded_send)
        except CaptureDeadlineExceeded:
            return
        finally:
            if error_slot:
                assert ownership is not None
                ownership.release()

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        budget = CaptureBudget(CAPTURE_REQUEST_SECONDS)
        workers = _workers
        if not workers.acquire(blocking=False):
            await self._fail(scope, receive, send, 503)
            return
        ownership = _Ownership(workers)
        parent_loop = asyncio.get_running_loop()
        outcome: Future[tuple[int, list[Message]]] = Future()
        private_scope = {**scope, "state": dict(scope.get("state", {}))}
        if "fastapi" in scope:
            # Included-route metadata is request-local. Retain its effective
            # dependency context without sharing the parent's mutable mapping.
            private_scope["fastapi"] = dict(scope["fastapi"])
        body_finished = asyncio.Event()
        disconnected = asyncio.Event()
        receive_lock = asyncio.Lock()

        async def receive_main():
            ownership.retain()
            try:
                async with receive_lock:
                    budget.check()
                    message = await receive()
                    budget.check()
                    if message["type"] == "http.disconnect":
                        disconnected.set()
                        body_finished.set()
                        budget.cancel()
                    elif message["type"] == "http.request" and not message.get(
                        "more_body", False
                    ):
                        body_finished.set()
                    return message
            finally:
                ownership.release()

        async def watch_disconnect():
            # Do not consume a byte before the authenticated route asks for it.
            # Once its final body packet has arrived, own the remaining receive
            # side so a caller disconnect also fences already-running phases.
            await body_finished.wait()
            if not disconnected.is_set():
                await receive_main()
            if not disconnected.is_set():
                raise ValueError("invalid_capture_receive")

        async def serve():
            loop = asyncio.get_running_loop()
            task = asyncio.current_task()
            assert task is not None

            def cancel_work():
                try:
                    loop.call_soon_threadsafe(task.cancel)
                except RuntimeError:
                    pass  # The private loop already completed its own cleanup.

            budget.bind(cancel_work)
            messages: list[Message] = []
            length = 0

            async def receive_private():
                budget.check()
                future = asyncio.run_coroutine_threadsafe(receive_main(), parent_loop)
                message = await asyncio.wrap_future(future)
                budget.check()
                return message

            async def buffered(message: Message):
                nonlocal length
                budget.check()
                if message["type"] == "http.response.body":
                    length += len(message.get("body", b""))
                if length > MAX_CAPTURE_RESPONSE_BYTES or len(messages) >= 4:
                    raise ValueError("capture_response_too_large")
                messages.append(message)

            try:
                budget.check()
                await self._dispatch(private_scope, receive_private, buffered)
                budget.check()
                return 200, messages
            finally:
                budget.bind(None)

        def run():
            try:
                with capture_budget(budget):
                    result = asyncio.run(serve())
            except (CaptureDeadlineExceeded, asyncio.CancelledError):
                result = (504, [])
            except BaseException:  # noqa: BLE001 -- no private worker exception escapes
                result = (503, [])
            finally:
                # Includes async-generator, dependency and executor cleanup.
                # A resistant worker retains this slot; later requests fail closed.
                ownership.release()
            outcome.set_result(result)

        try:
            thread = Thread(
                target=lambda: Context().run(run),
                daemon=True,
                name="core-capture-request",
            )
            thread.start()
        except BaseException:  # noqa: BLE001 -- release ownership on thread-start failure
            ownership.release()
            ownership.release()
            budget.cancel()
            await self._fail(scope, receive, send, 503)
            return
        waiting = asyncio.wrap_future(outcome)
        ownership.retain()
        watching = asyncio.create_task(watch_disconnect())
        watching.add_done_callback(lambda _: ownership.release())
        try:
            done, _ = await asyncio.wait(
                {waiting, watching},
                timeout=max(0, budget.expires_at - time.monotonic()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if watching in done:
                budget.cancel()
                if disconnected.is_set():
                    return
                # A failed receive grants no further work or private response.
                try:
                    watching.result()
                except CaptureDeadlineExceeded:
                    await self._fail(scope, receive, send, 504, ownership)
                except Exception:  # noqa: BLE001 -- receive errors grant no disclosure
                    await self._fail(scope, receive, send, 503, ownership)
                return
            if not done:
                budget.cancel()
                await self._fail(scope, receive, send, 504, ownership)
                return
            status, messages = waiting.result()
            try:
                budget.check()
            except CaptureDeadlineExceeded:
                status, messages = 504, []
            if status != 200:
                budget.cancel()
                await self._fail(scope, receive, send, status, ownership)
                return
            for message in messages:
                try:
                    budget.check()
                    await self._send_until(message, send, budget.expires_at, ownership)
                except CaptureDeadlineExceeded:
                    # Public headers may have been admitted. Never append an
                    # alternate status or another body chunk to partial output.
                    return
        finally:
            budget.cancel()
            if not watching.done():
                self._abandon(watching)
            ownership.release()


# An HTTP caller cannot construct this identity marker. It is copied only into
# the private ASGI scope owned by an outer boundary, preventing nested workers.
_SCOPE_KEY = "cosmaneura.capture_boundary"
_SCOPE_OWNER = object()
CAPTURE_PATHS = frozenset(
    "/service/v1/agent/scribe/" + suffix
    for suffix in (
        "capture-bindings/resolve",
        "capture-admissions",
        "capture-operations",
        "capture-notes",
        "encounter-context",
    )
)


class CaptureDeadlineRoute(APIRoute, _CaptureBoundary):
    """Standalone fallback; the main app uses the outer middleware owner."""

    async def _dispatch(self, scope: Scope, receive: Receive, send: Send) -> None:
        # FastAPI resolves included-router dependencies in handle(). Calling
        # the original self.app would bypass that effective route context.
        # Keep AnyIO sync dependencies owned after cancellation, as the main
        # middleware's task group does. The slot outlives every child task.
        try:
            async with create_task_group() as tasks:
                tasks.start_soon(APIRoute.handle, self, scope, receive, send)
        except BaseExceptionGroup as errors:
            if len(errors.exceptions) != 1:
                raise
            # Preserve the route exception's deadline/error classification.
            error = errors.exceptions[0]
            context = None if error.__suppress_context__ else error.__context__
            raise error from error.__cause__ or context

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            self.methods is not None and scope["method"] not in self.methods
        ) or scope.get(_SCOPE_KEY) is _SCOPE_OWNER:
            await super().handle(scope, receive, send)
            return
        await _CaptureBoundary.handle(self, scope, receive, send)


class CaptureBoundary(_CaptureBoundary):
    """Pure ASGI wrapper outside Core's response-transforming middleware."""

    def __init__(self, app: ASGIApp, *, paths: frozenset[str]):
        self.app, self.paths = app, paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"] not in self.paths
            or scope.get(_SCOPE_KEY) is _SCOPE_OWNER
        ):
            await self.app(scope, receive, send)
            return
        private_scope = {**scope, _SCOPE_KEY: _SCOPE_OWNER}
        await self.handle(private_scope, receive, send)


def install_capture_boundaries(app: FastAPI) -> None:
    """Install last, after all response middleware and the independent reader."""
    if any(item.cls is CaptureBoundary for item in app.user_middleware):
        raise ValueError("capture_boundary_already_installed")
    routes = [
        route
        for route in iter_route_contexts(app.routes)
        if isinstance(route.original_route, CaptureDeadlineRoute)
    ]
    if (
        len(routes) != len(CAPTURE_PATHS)
        or {route.path for route in routes} != CAPTURE_PATHS
        or any(route.methods != {"POST"} for route in routes)
    ):
        raise ValueError("capture_route_inventory")
    app.add_middleware(CaptureBoundary, paths=CAPTURE_PATHS)
    app.state.capture_deadline_paths = tuple(sorted(CAPTURE_PATHS))
