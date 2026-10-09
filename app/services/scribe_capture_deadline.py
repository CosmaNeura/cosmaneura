"""A capture request's private cancellation and publication admission fence."""

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event, Lock, get_ident

from app.utils.native_request_timing import phase
from app.utils.statement_timeout_scope import (
    acknowledge_record_timeout,
    capture_timeout_scopes,
    consume_record_timeout,
    current_capture_timeout_scope,
    current_record_timeout_scope,
    remember_timeout_setting,
    reusable_timeout_limits,
    timeout_setting_in_force,
)

from sqlalchemy import Engine, event
from sqlalchemy.orm import Session


class CaptureDeadlineExceeded(TimeoutError):
    pass


class CaptureBudget:
    def __init__(self, seconds: float):
        if not 0 < seconds <= 90:
            raise ValueError("invalid_capture_deadline")
        self.expires_at = time.monotonic() + seconds
        self.cancelled = Event()
        self._lock = Lock()
        self._cancel_work: Callable[[], None] | None = None
        self.owner_thread: int | None = None

    def check(self) -> float:
        remaining = self.expires_at - time.monotonic()
        if self.cancelled.is_set() or remaining <= 0:
            raise CaptureDeadlineExceeded("capture_request_unconfirmed")
        return remaining

    def cancel(self) -> None:
        with self._lock:
            if self.cancelled.is_set():
                return
            self.cancelled.set()
            cancel = self._cancel_work
        if cancel is not None:
            cancel()

    def bind(self, cancel: Callable[[], None] | None) -> None:
        with self._lock:
            self._cancel_work = cancel
        if cancel is not None and self.cancelled.is_set():
            cancel()


_budget: ContextVar[CaptureBudget | None] = ContextVar("capture_deadline", default=None)


@contextmanager
def capture_budget(budget: CaptureBudget) -> Iterator[None]:
    budget.owner_thread = get_ident()
    token = _budget.set(budget)
    try:

        def limits():
            return max(1, int(budget.check() * 1000)), max(
                1, min(2000, int(budget.check() * 1000))
            )

        with capture_timeout_scopes(budget, limits):
            budget.check()
            yield
    finally:
        _budget.reset(token)


def check_capture_deadline() -> float | None:
    """Calls outside the private capture routes retain their existing behavior."""
    budget = _budget.get()
    return None if budget is None else budget.check()


def check_async_capture_deadline() -> None:
    budget = _budget.get()
    if budget is None:
        return
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        # A dependency may suppress cancellation from its shorter phase timer.
        # It must not turn that uncertain phase into permission for another one.
        budget.cancel()
    budget.check()


async def capture_call[T](operation: Callable[[], Awaitable[T]]) -> T:
    check_async_capture_deadline()
    with phase("downstream"):
        result = await operation()
    check_async_capture_deadline()
    return result


def _sql_budget() -> CaptureBudget | None:
    budget = _budget.get()
    if budget is not None:
        budget.check()
        if budget.owner_thread != get_ident():
            raise CaptureDeadlineExceeded("capture_session_not_owned")
    return budget


@event.listens_for(Engine, "before_cursor_execute")
def before_capture_statement(
    connection, cursor, _statement, _parameters, context, _many
):
    budget = _sql_budget()
    if budget is None:
        return
    if connection.dialect.name != "postgresql":
        raise CaptureDeadlineExceeded("capture_database_unavailable")
    # Raw cursor calls avoid recursion. Only this request's current transaction
    # gets these settings; pool acquisition/pre-ping may settle before this hook.
    statement_ms = max(1, int(budget.check() * 1000))
    lock_ms = max(1, min(2000, int(budget.check() * 1000)))
    record = current_record_timeout_scope(connection) if context is not None else None
    setting_scope = record or (
        current_capture_timeout_scope(connection, budget)
        if context is not None
        else None
    )
    if record is not None:
        record_statement_ms, record_lock_ms = record.limits()
        if consume_record_timeout(context, record, budget, "record"):
            budget.check()
            return
        statement_ms = min(statement_ms, record_statement_ms)
        lock_ms = min(lock_ms, record_lock_ms)
    # One parameterized round trip sets both transaction-local values. Every
    # statement still refreshes the remaining budget and checks cancellation
    # after the settings return, before its actual SQL can be dispatched.
    limits = statement_ms, lock_ms
    if not timeout_setting_in_force(
        connection, cursor, context, setting_scope, budget, limits
    ):
        limits = reusable_timeout_limits(
            connection, cursor, context, setting_scope, limits
        )
        cursor.execute(
            "SELECT set_config('statement_timeout', %s, true), "
            "set_config('lock_timeout', %s, true)",
            tuple(map(str, limits)),
        )
    budget.check()
    if record is not None:
        record.limits()
        acknowledge_record_timeout(context, record, budget, "capture")
    if setting_scope is not None:
        remember_timeout_setting(
            connection, cursor, context, setting_scope, budget, limits
        )


@event.listens_for(Session, "before_flush")
def before_capture_flush(_session, _context, _instances):
    _sql_budget()


@event.listens_for(Session, "before_commit")
def before_capture_commit(_session):
    _sql_budget()


@event.listens_for(Engine, "commit")
def before_capture_connection_commit(_connection):
    # Session.before_commit precedes its flush. Recheck at the actual DBAPI
    # commit boundary so late flush completion cannot admit a new publication.
    _sql_budget()


class DeadlineScribeClient:
    """Fence every injected or real Scribe phase, including late test doubles."""

    def __init__(self, client):
        self.client = client

    async def create_configured_session(self, *args, **kwargs):
        return await capture_call(
            lambda: self.client.create_configured_session(*args, **kwargs)
        )

    async def capture_operation(self, *args, **kwargs):
        return await capture_call(
            lambda: self.client.capture_operation(*args, **kwargs)
        )

    async def create_consent(self, *args, **kwargs):
        return await capture_call(lambda: self.client.create_consent(*args, **kwargs))

    async def create_session(self, *args, **kwargs):
        return await capture_call(lambda: self.client.create_session(*args, **kwargs))

    async def create_external_transcript_session(self, *args, **kwargs):
        return await capture_call(
            lambda: self.client.create_external_transcript_session(*args, **kwargs)
        )

    async def get_capture_status(self, *args, **kwargs):
        return await capture_call(
            lambda: self.client.get_capture_status(*args, **kwargs)
        )

    async def get_capture_transcript(self, *args, **kwargs):
        return await capture_call(
            lambda: self.client.get_capture_transcript(*args, **kwargs)
        )

    async def close(self):
        # Cleanup stays in the worker and retains its admission slot until done.
        # It grants no further SQL/remote/application permission after cancellation.
        await self.client.close()
