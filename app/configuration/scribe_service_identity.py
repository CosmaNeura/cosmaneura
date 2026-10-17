"""Official service-identity SDK ownership on one application lifespan loop.

Only a deployment-owned service scope crosses this adapter. Patient claims,
consent, grants, SQL and request context never enter the shared credential.
Refresh/expiry and token caching belong exclusively to the official SDK.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from concurrent.futures import Future
from contextlib import asynccontextmanager, contextmanager
from contextvars import Context, ContextVar
from dataclasses import dataclass, field
from threading import Event, get_ident
from typing import Any, Protocol

from azure.core.credentials import AccessToken
from azure.core.exceptions import AzureError

from app.configuration.scribe_workflow import ScribeWorkflowSettings
from app.configuration.service_auth import create_scribe_workload_credential


class ServiceCredential(Protocol):
    async def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken: ...

    async def close(self) -> None: ...


class CaptureBudget(Protocol):
    owner_thread: int | None

    def check(self) -> float: ...


@dataclass(eq=False)
class _Operation:
    budget: CaptureBudget
    result: Future[AccessToken] = field(default_factory=Future)
    joined: Future[None] = field(default_factory=Future)
    cancelled: Event = field(default_factory=Event)
    task: asyncio.Task[None] | None = None


@dataclass(eq=False)
class _WarmOperation:
    joined: Future[None] = field(default_factory=Future)
    cancelled: bool = False
    task: asyncio.Task[None] | None = None
    timer: asyncio.TimerHandle | None = None


WARM_ACQUISITION_SECONDS = 2.0


def _unavailable() -> AzureError:
    return AzureError("scribe_identity_unavailable")


async def _join(completion: Future[None]) -> bool:
    wrapped = asyncio.wrap_future(completion)
    cancelled = False
    while not wrapped.done():
        try:
            await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            # A private worker stays owned until its exact SDK work settles.
            cancelled = True
    wrapped.result()
    return cancelled


class ScribeServiceIdentityOwner:
    def __init__(
        self,
        settings: ScribeWorkflowSettings,
        *,
        credential_factory: Callable[[str], ServiceCredential] = (
            create_scribe_workload_credential
        ),
    ):
        if not settings.enabled or not settings.is_complete():
            raise ValueError("scribe_identity_configuration_invalid")
        self.loop = asyncio.get_running_loop()
        self._client_id, self._scope = (
            settings.managed_identity_client_id,
            settings.scope,
        )
        # Construct only. No token is acquired until a current request asks.
        self._credential = credential_factory(self._client_id)
        self._serial = asyncio.Lock()
        self._operations: set[_Operation] = set()
        self._warm: _WarmOperation | None = None
        self._closed = False
        self._closing: asyncio.Task[None] | None = None

    @property
    def closed(self) -> bool:
        return self._closed

    def request_warm(self) -> None:
        """Optional service-only work; never expose a token or accept context."""
        if (
            asyncio.get_running_loop() is not self.loop
            or self._closed
            or self._operations
            or self._warm is not None
        ):
            return
        operation = _WarmOperation()
        self._warm = operation  # Register ownership before task/timer creation.
        operation.task = self.loop.create_task(
            self._warm_service(operation), context=Context()
        )
        operation.timer = self.loop.call_later(
            WARM_ACQUISITION_SECONDS, self._cancel_warm, operation, context=Context()
        )
        operation.task.add_done_callback(
            lambda task: self._finished_warm(operation, task), context=Context()
        )

    def _cancel_warm(self, operation: _WarmOperation) -> None:
        operation.cancelled = True
        if (
            operation.task is not None
            and not operation.task.done()
            and not operation.task.cancelling()
        ):
            operation.task.cancel()

    def _finished_warm(
        self, operation: _WarmOperation, task: asyncio.Task[None]
    ) -> None:
        if operation.timer is not None:
            operation.timer.cancel()
        if not task.cancelled():
            task.exception()
        if self._warm is operation:
            self._warm = None
        # Also joins cancellation before the coroutine's first instruction.
        operation.joined.set_result(None)

    async def _warm_service(self, operation: _WarmOperation) -> None:
        try:
            async with self._serial:
                if not self._closed and not operation.cancelled:
                    # Only the SDK owns a cache. No result survives this await.
                    await self._credential.get_token(self._scope)
        except BaseException:
            # Background failure cannot change a route or log SDK values.
            pass

    def borrow(
        self, settings: ScribeWorkflowSettings, budget: CaptureBudget
    ) -> ServiceCredential:
        if (
            self._closed
            or not settings.enabled
            or not settings.is_complete()
            or settings.managed_identity_client_id != self._client_id
            or settings.scope != self._scope
            or budget.owner_thread != get_ident()
            or asyncio.get_running_loop() is self.loop
        ):
            raise _unavailable()
        budget.check()
        return _Borrowed(self, budget)

    def _submit(self, operation: _Operation) -> None:
        try:
            self.loop.call_soon_threadsafe(self._start, operation, context=Context())
        except RuntimeError:
            operation.result.set_exception(_unavailable())
            operation.joined.set_result(None)  # No SDK operation was started.

    def _start(self, operation: _Operation) -> None:
        if self._closed or operation.cancelled.is_set():
            operation.result.set_exception(_unavailable())
            operation.joined.set_result(None)
            return
        self._operations.add(operation)
        if self._warm is not None:
            self._cancel_warm(self._warm)
        operation.task = self.loop.create_task(
            self._acquire(operation), context=Context()
        )
        operation.task.add_done_callback(lambda task: self._finished(operation, task))

    def _finished(self, operation: _Operation, task: asyncio.Task[None]) -> None:
        self._operations.discard(operation)
        if not task.cancelled():
            task.exception()
        # Cancelling before the coroutine begins does not run its finally.
        if not operation.joined.done():
            operation.result.set_exception(_unavailable())
            operation.joined.set_result(None)

    def _cancel(self, operation: _Operation) -> None:
        operation.cancelled.set()

        def cancel():
            if (
                operation.task is not None
                and not operation.task.done()
                and not operation.task.cancelling()
            ):
                operation.task.cancel()

        try:
            self.loop.call_soon_threadsafe(cancel, context=Context())
        except RuntimeError:
            # Losing the loop does not prove that already-started SDK work joined.
            pass

    async def _acquire(self, operation: _Operation) -> None:
        answer = None
        try:
            warm = self._warm
            if warm is not None:
                self._cancel_warm(warm)
                await _join(warm.joined)
            # Serialize SDK refresh calls without adding our own token cache.
            async with self._serial:
                operation.budget.check()
                if operation.cancelled.is_set():
                    raise asyncio.CancelledError
                token = await self._credential.get_token(self._scope)
                operation.budget.check()
                if operation.cancelled.is_set() or not isinstance(token, AccessToken):
                    raise _unavailable()
                answer = token
        except BaseException:
            # Never move SDK exception values, credentials or request context
            # into a worker result or an unhandled parent-task log.
            pass
        finally:
            self._operations.discard(operation)
            if answer is None or operation.cancelled.is_set():
                operation.result.set_exception(_unavailable())
            else:
                operation.result.set_result(answer)
            operation.joined.set_result(None)

    async def close(self) -> None:
        if asyncio.get_running_loop() is not self.loop:
            raise RuntimeError("scribe_identity_wrong_loop")
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
        warm = self._warm
        if warm is not None:
            self._cancel_warm(warm)
        operations = tuple(self._operations)
        for operation in operations:
            self._cancel(operation)
        for operation in operations:
            await _join(operation.joined)
        if warm is not None:
            await _join(warm.joined)
        try:
            await self._credential.close()
        except BaseException:
            raise _unavailable() from None


class _Borrowed:
    def __init__(self, owner: ScribeServiceIdentityOwner, budget: CaptureBudget):
        self._owner, self._budget = owner, budget
        self._closed = False
        self._operations: set[_Operation] = set()

    async def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        if (
            self._closed
            or self._owner._closed
            or self._budget.owner_thread != get_ident()
            or scopes != (self._owner._scope,)
            or kwargs
        ):
            raise _unavailable()
        self._budget.check()
        operation = _Operation(self._budget)
        self._operations.add(operation)
        result = asyncio.wrap_future(operation.result)
        self._owner._submit(operation)
        try:
            answer = await asyncio.shield(result)
            self._budget.check()
            if self._closed or self._owner._closed:
                raise _unavailable()
            return answer
        except BaseException:
            self._owner._cancel(operation)
            await _join(operation.joined)
            if result.done() and not result.cancelled():
                result.exception()
            raise
        finally:
            self._operations.discard(operation)

    async def close(self) -> None:
        if self._budget.owner_thread != get_ident():
            raise _unavailable()
        self._closed = True
        operations = tuple(self._operations)
        for operation in operations:
            self._owner._cancel(operation)
        cancelled = False
        for operation in operations:
            cancelled = await _join(operation.joined) or cancelled
        # Closing a borrower never closes the SDK or another request.
        if cancelled:
            raise asyncio.CancelledError


_capture_identity: ContextVar[
    tuple[ScribeServiceIdentityOwner, CaptureBudget] | None
] = ContextVar("capture_scribe_service_identity", default=None)


@contextmanager
def capture_scribe_service_identity(
    owner: ScribeServiceIdentityOwner | None, budget: CaptureBudget
) -> Iterator[None]:
    token = _capture_identity.set(None if owner is None else (owner, budget))
    try:
        yield
    finally:
        _capture_identity.reset(token)


def current_scribe_service_identity(
    settings: ScribeWorkflowSettings,
) -> ServiceCredential | None:
    current = _capture_identity.get()
    return None if current is None else current[0].borrow(settings, current[1])


def request_scribe_service_identity_warm(application) -> None:
    owner = getattr(application.state, "scribe_service_identity", None)
    if isinstance(owner, ScribeServiceIdentityOwner):
        owner.request_warm()


def install_scribe_service_identity(
    application, settings: ScribeWorkflowSettings
) -> None:
    previous = application.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with previous(app) as state:
            owner = (
                ScribeServiceIdentityOwner(settings)
                if settings.identity_reuse_enabled and settings.enabled
                else None
            )
            app.state.scribe_service_identity = owner
            try:
                yield state
            finally:
                if owner is not None:
                    # Retain the closing/closed owner as a refusal fence.
                    # Clearing it would reopen the legacy per-request path.
                    # A later lifespan installs its own fresh owner above.
                    await owner.close()

    application.router.lifespan_context = lifespan
