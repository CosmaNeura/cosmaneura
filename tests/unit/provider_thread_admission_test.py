"""Real AnyIO admission/cancellation controls, without application bootstrap."""

import ast
import asyncio
import gc
import selectors
import socket
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from anyio import CancelScope, CapacityLimiter, to_thread
from psycopg.errors import UniqueViolation
from starlette.concurrency import run_in_threadpool

import app.services.provider_database_connection as connection
import app.services.provider_thread_admission as admission

from sqlalchemy import create_engine

PROVIDER_CLIENT_ID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(
        connection,
        "SOVEREIGN_TOOL_AUTH_SETTINGS",
        replace(
            connection.SOVEREIGN_TOOL_AUTH_SETTINGS,
            sovereign_client_ids=frozenset({PROVIDER_CLIENT_ID}),
        ),
    )
    admission._after_fork()
    monkeypatch.setattr(admission, "ADMISSION_SECONDS", 0.035)
    token = connection._work.set(None)
    session_token = connection.session_manager._session_context.set(None)
    yield
    connection._work.reset(token)
    connection.session_manager._session_context.reset(session_token)
    admission._after_fork()


def handler(scope: Callable[..., Any] | None = None, thread_api: Any = to_thread):
    source = Path(connection.__file__).parents[1] / "routes/agent.py"
    node = next(
        n
        for n in ast.parse(source.read_bytes()).body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_run_agent_database"
    )

    @contextmanager
    def inert_scope(_workload, _cancelled, _settings):
        yield

    namespace: dict[str, Any] = {
        "Callable": Callable,
        "Any": Any,
        "AuthorityKernelSettings": object,
        "AUTHORITY_KERNEL_SETTINGS": SimpleNamespace(enabled=True),
        "asyncio": asyncio,
        "threading": threading,
        "run_in_threadpool": run_in_threadpool,
        "CancelScope": CancelScope,
        "CapacityLimiter": CapacityLimiter,
        "to_thread": thread_api,
        "provider_receiver_enabled": connection.provider_receiver_enabled,
        "provider_receiver_session": scope or inert_scope,
        "provider_thread_slot": admission.provider_thread_slot,
        "ProviderDatabaseDeadlineExceeded": connection.ProviderDatabaseDeadlineExceeded,
    }
    exec(
        compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"),
        namespace,
    )
    return namespace["_run_agent_database"]


async def until(predicate):
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0.001)


async def occupying(run):
    entered = [threading.Event(), threading.Event()]
    release = threading.Event()

    def body(index):
        entered[index].set()
        assert release.wait(1)
        return index

    tasks = [
        asyncio.create_task(run(body, index, receiver_workload=PROVIDER_CLIENT_ID))
        for index in range(2)
    ]
    await until(lambda: all(event.is_set() for event in entered))
    return tasks, release


def test_real_shared_default_40_saturation_does_not_block_provider():
    async def scenario():
        shared = to_thread.current_default_thread_limiter()
        assert shared.total_tokens == 40
        borrowers = [object() for _ in range(40)]
        for borrower in borrowers:
            shared.acquire_on_behalf_of_nowait(borrower)
        run = handler()
        ordinary = asyncio.create_task(
            run(lambda: "original", receiver_workload="scribe")
        )
        provider = asyncio.create_task(
            run(lambda: "provider", receiver_workload=PROVIDER_CLIENT_ID)
        )
        try:
            # Do not cancel work queued behind the saturated shared pool:
            # route cancellation retains its worker until that pool is freed.
            done, _ = await asyncio.wait({provider}, timeout=1)
            assert provider in done, "provider work waited behind the shared pool"
            assert await provider == "provider"
            assert shared.borrowed_tokens == 40 and not ordinary.done()
            shared.release_on_behalf_of(borrowers.pop())
            assert await ordinary == "original"
        finally:
            for borrower in borrowers:
                shared.release_on_behalf_of(borrower)
            await asyncio.gather(provider, ordinary, return_exceptions=True)
        assert shared.total_tokens == 40 and shared.borrowed_tokens == 0

    asyncio.run(scenario())


def test_two_active_slots_timeout_has_no_late_body_dispatch():
    async def scenario():
        run = handler()
        tasks, release = await occupying(run)
        calls = []
        started = time.monotonic()
        try:
            with pytest.raises(connection.ProviderDatabaseDeadlineExceeded):
                await run(
                    lambda: calls.append("late"), receiver_workload=PROVIDER_CLIENT_ID
                )
            assert 0.02 <= time.monotonic() - started < 0.5
            assert admission._lane().slots.statistics().tasks_waiting == 0
            assert admission._lane().slots.borrowed_tokens == 2 and not calls
        finally:
            release.set()
            assert await asyncio.gather(*tasks) == [0, 1]
        await asyncio.sleep(0.01)
        assert not calls and admission._lane().slots.borrowed_tokens == 0
        assert await run(lambda: "next", receiver_workload=PROVIDER_CLIENT_ID) == "next"

    asyncio.run(scenario())


@pytest.mark.parametrize("level_cancel", [False, True])
def test_queued_cancellation_removes_waiter_without_late_work(level_cancel):
    async def scenario():
        run = handler()
        tasks, release = await occupying(run)
        calls = []
        scopes = []

        async def queued():
            if level_cancel:
                with CancelScope() as scope:
                    scopes.append(scope)
                    await run(
                        lambda: calls.append("late"),
                        receiver_workload=PROVIDER_CLIENT_ID,
                    )
            else:
                await run(
                    lambda: calls.append("late"), receiver_workload=PROVIDER_CLIENT_ID
                )

        pending = asyncio.create_task(queued())
        try:
            await until(lambda: admission._lane().slots.statistics().tasks_waiting == 1)
            if level_cancel:
                scopes[0].cancel()
                await pending
            else:
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            assert admission._lane().slots.statistics().tasks_waiting == 0
            assert admission._lane().slots.borrowed_tokens == 2
        finally:
            release.set()
            await asyncio.gather(*tasks)
        await asyncio.sleep(0.01)
        assert calls == [] and admission._lane().slots.borrowed_tokens == 0

    asyncio.run(scenario())


def test_already_cancelled_scope_never_dispatches_or_leaks_reservation():
    async def scenario():
        calls = []
        run = handler()
        with CancelScope() as scope:
            scope.cancel()
            await run(
                lambda: calls.append("late"), receiver_workload=PROVIDER_CLIENT_ID
            )
        assert scope.cancelled_caught and not calls
        assert admission._lane().slots.borrowed_tokens == 0

    asyncio.run(scenario())


def test_cancellation_at_thread_start_vetoes_session_and_body():
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    sessions = []
    bodies = []

    async def delayed_dispatch(function, *, limiter):
        def delayed():
            entered.set()
            try:
                assert release.wait(1)
                return function()
            finally:
                finished.set()

        return await to_thread.run_sync(delayed, limiter=limiter)

    @contextmanager
    def scope(*args):
        sessions.append("opened")
        yield

    async def scenario():
        run = handler(scope, SimpleNamespace(run_sync=delayed_dispatch))
        task = asyncio.create_task(
            run(lambda: bodies.append("late"), receiver_workload=PROVIDER_CLIENT_ID)
        )
        try:
            await until(entered.is_set)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert admission._lane().slots.borrowed_tokens == 0
            assert not sessions and not bodies
        finally:
            release.set()
            await until(finished.is_set)
        assert not sessions and not bodies
        assert (
            await handler()(lambda: "new", receiver_workload=PROVIDER_CLIENT_ID)
            == "new"
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("level_cancel", [False, True])
def test_active_cancellation_holds_slot_until_session_cleanup(level_cancel):
    entered = threading.Event()
    cleaning = threading.Event()
    release = threading.Event()
    closed = threading.Event()
    cancellation = []

    @contextmanager
    def scope(_workload, event, _settings):
        cancellation.append(event)
        try:
            yield
        finally:
            cleaning.set()
            assert release.wait(1)
            closed.set()

    def body():
        entered.set()
        assert cancellation[0].wait(1)

    async def scenario():
        run = handler(scope)
        scopes = []

        async def invoke():
            if level_cancel:
                with CancelScope() as scope:
                    scopes.append(scope)
                    await run(body, receiver_workload=PROVIDER_CLIENT_ID)
            else:
                await run(body, receiver_workload=PROVIDER_CLIENT_ID)

        task = asyncio.create_task(invoke())
        try:
            await until(entered.is_set)
            if level_cancel:
                scopes[0].cancel()
            else:
                task.cancel()
            await until(cleaning.is_set)
            assert not task.done() and admission._lane().slots.borrowed_tokens == 1
            if not level_cancel:
                task.cancel()
                await asyncio.sleep(0.005)
                assert not task.done()
        finally:
            release.set()
        if level_cancel:
            await task
        else:
            with pytest.raises(asyncio.CancelledError):
                await task
        assert closed.is_set() and admission._lane().slots.borrowed_tokens == 0

    asyncio.run(scenario())


def test_active_real_connection_checkpoint_cancellation_preserves_context(monkeypatch):
    reader, writer = socket.socketpair()
    entered = threading.Event()
    closed = threading.Event()
    engine = create_engine("postgresql+psycopg://fictional@127.0.0.1/fictional")
    monkeypatch.setattr(connection.session_manager, "get_engine", lambda: engine)
    monkeypatch.setattr(connection, "_engine", None)

    def body():
        entered.set()

        def stalled():
            try:
                while True:
                    yield selectors.EVENT_READ
            finally:
                closed.set()

        value: Any = SimpleNamespace(
            pgconn=SimpleNamespace(socket=reader.fileno(), finish=reader.close)
        )
        value._discard = lambda: connection.ProviderConnection._discard(value)
        connection.ProviderConnection.wait(value, stalled())

    async def scenario():
        original: Any = SimpleNamespace(
            in_transaction=lambda: False, new=(), dirty=(), deleted=()
        )
        token = connection.session_manager._session_context.set(original)
        try:
            task = asyncio.create_task(
                handler(connection.provider_receiver_session)(
                    body, receiver_workload=PROVIDER_CLIENT_ID
                )
            )
            await until(entered.is_set)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert reader.fileno() == -1 and closed.is_set()
            assert connection.session_manager._session_context.get() is original
            assert connection._work.get() is None
            assert admission._lane().slots.borrowed_tokens == 0
        finally:
            connection.session_manager._session_context.reset(token)

    try:
        asyncio.run(scenario())
    finally:
        reader.close()
        writer.close()


@pytest.mark.parametrize(
    "error",
    [TimeoutError("synthetic"), UniqueViolation("synthetic"), ValueError("synthetic")],
)
def test_work_exception_is_preserved_and_not_classified_as_queue_timeout(error):
    def body():
        raise error

    async def scenario():
        with pytest.raises(type(error)) as raised:
            await handler()(body, receiver_workload=PROVIDER_CLIENT_ID)
        assert raised.value is error and admission._lane().slots.borrowed_tokens == 0

    asyncio.run(scenario())


def test_active_work_longer_than_admission_timeout_is_not_abandoned():
    async def scenario():
        async def release_after(event):
            await asyncio.sleep(admission.ADMISSION_SECONDS * 2)
            event.set()

        release = threading.Event()
        timer = asyncio.create_task(release_after(release))
        try:
            assert await handler()(
                lambda: release.wait(1), receiver_workload=PROVIDER_CLIENT_ID
            )
            assert admission._lane().slots.borrowed_tokens == 0
        finally:
            await timer

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "workload,enabled", [(None, True), ("scribe", True), (PROVIDER_CLIENT_ID, False)]
)
def test_nonprovider_or_disabled_lane_uses_original_thread_admission(
    monkeypatch, workload, enabled
):
    monkeypatch.setattr(
        admission, "_lane", lambda: pytest.fail("provider admission must not run")
    )

    async def scenario():
        assert (
            await handler()(
                lambda: "original",
                receiver_workload=workload,
                receiver_settings=SimpleNamespace(enabled=enabled),
            )
            == "original"
        )

    asyncio.run(scenario())


def test_loop_lifecycle_and_fork_reset_do_not_reuse_reservations():
    lanes = []

    async def scenario():
        async with admission.provider_thread_slot():
            lane = admission._lane()
            assert lane.slots.borrowed_tokens == 1
            lanes.append(lane)
        assert lane.slots.borrowed_tokens == 0

    for _ in range(2):
        asyncio.run(scenario())
    assert lanes[0] is not lanes[1]
    gc.collect()
    assert len(admission._lanes) == 0

    async def fork_scenario():
        async with admission.provider_thread_slot():
            old = admission._lane()
            admission._after_fork()
            fresh = admission._lane()
            assert fresh is not old and fresh.slots.borrowed_tokens == 0
            assert old.slots.borrowed_tokens == 1
        assert old.slots.borrowed_tokens == 0

    asyncio.run(fork_scenario())
