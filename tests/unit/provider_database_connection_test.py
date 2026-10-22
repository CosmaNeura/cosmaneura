"""Secretless owned-socket/pool tests; no PostgreSQL server or database writes."""

import ast
import asyncio
import selectors
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from anyio import CancelScope
from starlette.concurrency import run_in_threadpool

import app.services.provider_database_connection as bounds

from sqlalchemy import create_engine
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.pool import QueuePool


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(bounds, "_engine", None)
    monkeypatch.setattr(bounds, "_dns_slots", threading.BoundedSemaphore(2))
    monkeypatch.setattr(bounds, "POOL_SECONDS", 0.05)
    monkeypatch.setattr(bounds, "CONNECT_SECONDS", 0.08)
    monkeypatch.setattr(bounds, "SOCKET_SECONDS", 0.04)
    monkeypatch.setattr(bounds, "DNS_SECONDS", 0.03)
    monkeypatch.setattr(bounds, "POLL_SECONDS", 0.005)
    monkeypatch.setattr(
        bounds.session_manager.SessionManager,
        "_instance",
        bounds.session_manager.SessionManager(
            "postgresql+psycopg://fictional@127.0.0.1/fictional"
        ),
    )
    token = bounds._work.set(None)
    context = bounds.session_manager._session_context.set(None)
    yield
    bounds._work.reset(token)
    bounds.session_manager._session_context.reset(context)


def test_real_pool_exhaustion_and_release():
    class Connection:
        def rollback(self):
            pass

        def close(self):
            pass

    pool = QueuePool(Connection, pool_size=2, max_overflow=0, timeout=0.03)
    first, second = pool.connect(), pool.connect()
    started = time.monotonic()
    with pytest.raises(PoolTimeout):
        pool.connect()
    assert 0.02 <= time.monotonic() - started < 0.3
    first.close()
    replacement = pool.connect()
    replacement.close()
    second.close()
    assert pool.checkedout() == 0


def test_owned_engine_preserves_global_pool_and_serializer():
    def serializer(value):
        return "synthetic"

    source = create_engine(
        "postgresql+psycopg://fictional@127.0.0.1/fictional", json_serializer=serializer
    )
    owned = bounds.provider_engine(source)
    assert owned is not source and owned.pool is not source.pool
    assert owned.pool.size() == 2 and owned.pool._max_overflow == 0
    assert owned.pool._timeout == bounds.POOL_SECONDS
    assert source.pool._timeout == 30 and source.pool._max_overflow == 10
    assert owned.dialect._json_serializer is serializer
    assert owned.hide_parameters and not owned.echo
    assert bounds.provider_engine(source) is owned


def test_engine_lock_wait_is_bounded():
    bounds._engine_lock.acquire()
    try:
        started = time.monotonic()
        with pytest.raises(bounds.ProviderDatabaseDeadlineExceeded):
            bounds.provider_engine(None)
        assert time.monotonic() - started < 0.3
    finally:
        bounds._engine_lock.release()


def stalled_socket(cancel=False):
    reader, writer = socket.socketpair()
    stopped = []

    def stalled():
        try:
            while True:
                yield selectors.EVENT_READ
        finally:
            stopped.append(True)

    pgconn = SimpleNamespace(socket=reader.fileno(), finish=reader.close)
    connection = SimpleNamespace(pgconn=pgconn, _closed=False)
    connection._discard = lambda: bounds.ProviderConnection._discard(connection)
    event = threading.Event()
    token = bounds._work.set(bounds.ReceiverWork(event, time.monotonic() + 1))
    timer = threading.Timer(0.01, event.set) if cancel else None
    if timer:
        timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(
            bounds.ProviderConnectionUnavailable,
            match="^provider_database_unavailable$",
        ):
            bounds.ProviderConnection.wait(connection, stalled())
        assert time.monotonic() - started < 0.3
        assert reader.fileno() == -1 and connection._closed and stopped == [True]
    finally:
        if timer:
            timer.join()
        bounds._work.reset(token)
        reader.close()
        writer.close()


def test_stalled_query_commit_rollback_socket_discard():
    stalled_socket()


def test_cancelled_wait_discards_socket_and_closes_generator():
    stalled_socket(cancel=True)


def test_ready_socket_is_successful_and_not_closed():
    reader, writer = socket.socketpair()
    writer.send(b"x")

    def receive():
        assert (yield selectors.EVENT_READ) == selectors.EVENT_READ
        assert reader.recv(1) == b"x"
        return "ready"

    try:
        connection = SimpleNamespace(
            pgconn=SimpleNamespace(socket=reader.fileno(), finish=reader.close)
        )
        assert bounds.ProviderConnection.wait(connection, receive()) == "ready"
        assert reader.fileno() >= 0
    finally:
        reader.close()
        writer.close()


def test_postgres_integrity_error_preserves_rollback_connection():
    reader, writer = socket.socketpair()
    writer.send(b"x")

    def conflict():
        yield selectors.EVENT_READ
        raise bounds.psycopg.errors.UniqueViolation("synthetic")

    connection = SimpleNamespace(
        pgconn=SimpleNamespace(socket=reader.fileno(), finish=reader.close)
    )
    connection._discard = lambda: bounds.ProviderConnection._discard(connection)
    try:
        with pytest.raises(bounds.psycopg.errors.UniqueViolation):
            bounds.ProviderConnection.wait(connection, conflict())
        assert reader.fileno() >= 0
    finally:
        reader.close()
        writer.close()


def test_receiver_deadline_does_not_reset_between_waits():
    event = threading.Event()
    token = bounds._work.set(bounds.ReceiverWork(event, time.monotonic() - 1))
    try:
        with pytest.raises(bounds.ProviderConnectionUnavailable):
            bounds._remaining(time.monotonic() + 100)
    finally:
        bounds._work.reset(token)


@pytest.mark.parametrize("cancelled", [False, True])
def test_cancelled_or_expired_wait_never_advances_command_generator(cancelled):
    event = threading.Event()
    if cancelled:
        event.set()
    token = bounds._work.set(
        bounds.ReceiverWork(event, time.monotonic() + (1 if cancelled else -1))
    )
    operations = []

    class Command:
        def __next__(self):
            operations.append("sent")
            return selectors.EVENT_READ

        def close(self):
            operations.append("closed")

    try:
        with pytest.raises(bounds.ProviderConnectionUnavailable):
            bounds._wait(Command(), -1, time.monotonic() + 1)
        assert operations == ["closed"]
    finally:
        bounds._work.reset(token)


@pytest.mark.parametrize("configured,polled", [("2", False), ("3", True)])
def test_nonblocking_connect_respects_tighter_configured_timeout(
    monkeypatch, configured, polled
):
    operations = []
    clock = iter([100.0, 101.9, 102.1])
    monkeypatch.setattr(bounds.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(bounds, "CONNECT_SECONDS", 3.0)

    class Connection:
        def connect_poll(self):
            operations.append("polled")
            return bounds.psycopg.pq.PollingStatus.FAILED

        def finish(self):
            operations.append("closed")

    def start(*args):
        operations.append("started")
        return Connection()

    monkeypatch.setattr(
        bounds,
        "pq",
        SimpleNamespace(
            PGconn=SimpleNamespace(connect_start=start),
            PollingStatus=bounds.psycopg.pq.PollingStatus,
        ),
    )
    with pytest.raises(bounds.ProviderConnectionUnavailable):
        bounds.ProviderConnection.connect(host="127.0.0.1", connect_timeout=configured)
    assert operations == (
        ["started", "polled", "closed"] if polled else ["started", "closed"]
    )


def test_actual_libpq_handshake_stall_has_bounded_wait(monkeypatch):
    # Owned inert TCP listener: it never becomes PostgreSQL or receives SQL.
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(1)
    port = listener.getsockname()[1]
    stop = threading.Event()
    closed = threading.Event()
    received = threading.Event()
    errors = []
    sent_parameters = []
    monkeypatch.setattr(bounds, "CONNECT_SECONDS", 0.25)
    original_conninfo = bounds.make_conninfo

    def conninfo(value, **parameters):
        sent_parameters.append(parameters.copy())
        return original_conninfo(value, **parameters)

    monkeypatch.setattr(bounds, "make_conninfo", conninfo)
    monkeypatch.setattr(
        bounds.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", port))
        ],
    )

    def server():
        try:
            peer, _ = listener.accept()
            with peer:
                peer.settimeout(1)
                # Only the literal eight-byte SSL handshake is received.
                assert peer.recv(8) == b"\x00\x00\x00\x08\x04\xd2\x16\x2f"
                received.set()
                stop.wait(1)
        except Exception as error:
            errors.append(type(error).__name__)
        finally:
            closed.set()

    thread = threading.Thread(target=server)
    thread.start()
    started = time.monotonic()
    try:
        with pytest.raises(bounds.ProviderConnectionUnavailable):
            bounds.ProviderConnection.connect(
                host="fictional.invalid",
                port=str(port),
                dbname="fictional",
                user="fictional",
                sslmode="require",
                gssencmode="disable",
            )
        assert 0.15 <= time.monotonic() - started < 0.7
    finally:
        stop.set()
        thread.join(2)
        listener.close()
    assert (
        closed.is_set() and received.is_set() and not thread.is_alive() and not errors
    )
    assert sent_parameters[0]["host"] == "fictional.invalid"
    assert sent_parameters[0]["hostaddr"] == "127.0.0.1"


def test_stalled_dns_is_capped_and_permits_are_released(monkeypatch):
    release = threading.Event()
    calls = []

    def lookup(*args, **kwargs):
        calls.append(True)
        release.wait(1)
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 5432))]

    monkeypatch.setattr(bounds.socket, "getaddrinfo", lookup)
    try:
        for _ in range(3):
            with pytest.raises(bounds.ProviderConnectionUnavailable):
                bounds._address("fictional.invalid", "5432", time.monotonic() + 1)
        assert len(calls) == 2
    finally:
        release.set()
    deadline = time.monotonic() + 1
    while len(threading.enumerate()) > 1 and time.monotonic() < deadline:
        if bounds._dns_slots.acquire(blocking=False):
            bounds._dns_slots.release()
            break
        time.sleep(0.005)
    assert bounds._address("127.0.0.1", "5432", time.monotonic() + 1) == "127.0.0.1"


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"host": "a,b"},
        {"host": "a", "service": "untrusted"},
        {"host": "a", "hostaddr": "127.0.0.1,127.0.0.2"},
    ],
)
def test_ambient_or_multi_host_connections_fail_closed(kwargs):
    with pytest.raises(
        bounds.ProviderConnectionUnavailable, match="^provider_database_unavailable$"
    ):
        bounds.ProviderConnection.connect(**kwargs)


def test_ambient_service_is_rejected_before_libpq_or_dns(monkeypatch):
    monkeypatch.setenv("PGSERVICE", "synthetic-untrusted-service")
    monkeypatch.setattr(
        bounds, "_address", lambda *args: pytest.fail("DNS must not run")
    )
    monkeypatch.setattr(
        bounds,
        "pq",
        SimpleNamespace(
            PGconn=SimpleNamespace(
                connect_start=lambda *args: pytest.fail("libpq must not run")
            )
        ),
    )
    with pytest.raises(
        bounds.ProviderConnectionUnavailable, match="^provider_database_unavailable$"
    ):
        bounds.ProviderConnection.connect(host="fictional.invalid")


def test_nonprovider_and_disabled_work_use_original_context(monkeypatch):
    monkeypatch.setattr(
        bounds.session_manager,
        "get_engine",
        lambda: pytest.fail("global engine should not be read"),
    )
    for workload, enabled in [("scribe", True), (None, True), ("provider", False)]:
        with bounds.provider_receiver_session(
            workload, threading.Event(), SimpleNamespace(enabled=enabled)
        ):
            assert bounds._work.get() is None


def test_owned_session_is_shared_by_nested_calls_and_restored(monkeypatch):
    source = create_engine("postgresql+psycopg://fictional@127.0.0.1/fictional")
    monkeypatch.setattr(bounds.session_manager, "get_engine", lambda: source)
    prior = bounds.Session(source)
    token = bounds.session_manager._session_context.set(prior)
    try:
        with bounds.provider_receiver_session(
            "provider", threading.Event(), SimpleNamespace(enabled=True)
        ):
            current = bounds.session_manager._session_context.get()
            assert current is not prior
            with bounds.session_manager.get_session() as nested:
                assert nested is current
        assert bounds.session_manager._session_context.get() is prior
        assert bounds._work.get() is None
    finally:
        bounds.session_manager._session_context.reset(token)
        prior.close()


def test_session_setup_failure_and_pool_timeout_restore_context(monkeypatch):
    monkeypatch.setattr(bounds.session_manager, "get_engine", lambda: None)
    monkeypatch.setattr(
        bounds, "provider_engine", lambda _source: (_ for _ in ()).throw(PoolTimeout())
    )
    with pytest.raises(bounds.ProviderDatabaseDeadlineExceeded):
        with bounds.provider_receiver_session(
            "provider", threading.Event(), SimpleNamespace(enabled=True)
        ):
            pytest.fail("work should not run")
    assert (
        bounds._work.get() is None
        and bounds.session_manager._session_context.get() is None
    )


def test_already_active_session_is_not_retargeted():
    prior = SimpleNamespace(in_transaction=lambda: True)
    token = bounds.session_manager._session_context.set(prior)
    try:
        with pytest.raises(bounds.ProviderDatabaseDeadlineExceeded):
            with bounds.provider_receiver_session(
                "provider", threading.Event(), SimpleNamespace(enabled=True)
            ):
                pytest.fail("transaction must not move")
        assert bounds.session_manager._session_context.get() is prior
    finally:
        bounds.session_manager._session_context.reset(token)


@pytest.mark.parametrize("actual_scope", [False, True])
def test_real_retained_route_worker_receives_cancellation(monkeypatch, actual_scope):
    source = Path(bounds.__file__).parents[1] / "routes/agent.py"
    tree = ast.parse(source.read_bytes())
    node = next(
        n
        for n in tree.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_run_agent_database"
    )
    cancelled = threading.Event()
    done = threading.Event()
    entered = threading.Event()
    reader, writer = socket.socketpair()
    engine = create_engine("postgresql+psycopg://fictional@127.0.0.1/fictional")
    monkeypatch.setattr(bounds.session_manager, "get_engine", lambda: engine)

    def scoped(_workload, event, _settings):
        from contextlib import contextmanager

        @contextmanager
        def scope():
            assert event is not None
            try:
                yield
            finally:
                cancelled.set() if event.is_set() else None

        return scope()

    ns = {
        "Callable": __import__("collections.abc", fromlist=["Callable"]).Callable,
        "Any": __import__("typing").Any,
        "AuthorityKernelSettings": object,
        "AUTHORITY_KERNEL_SETTINGS": SimpleNamespace(enabled=True),
        "asyncio": asyncio,
        "threading": threading,
        "run_in_threadpool": run_in_threadpool,
        "CancelScope": CancelScope,
        "provider_receiver_session": bounds.provider_receiver_session
        if actual_scope
        else scoped,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), ns)

    def work():
        entered.set()
        try:
            if actual_scope:

                def wait_for_response():
                    while True:
                        yield selectors.EVENT_READ

                connection = SimpleNamespace(
                    pgconn=SimpleNamespace(socket=reader.fileno(), finish=reader.close)
                )
                connection._discard = lambda: bounds.ProviderConnection._discard(
                    connection
                )
                bounds.ProviderConnection.wait(connection, wait_for_response())
            else:
                time.sleep(0.03)
        finally:
            done.set()

    async def scenario():
        task = asyncio.create_task(
            ns["_run_agent_database"](work, receiver_workload="provider")
        )
        deadline = time.monotonic() + 1
        while not entered.is_set():
            assert time.monotonic() < deadline
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert done.is_set()
        if actual_scope:
            assert reader.fileno() == -1
        else:
            assert cancelled.is_set()

    try:
        asyncio.run(scenario())
    finally:
        reader.close()
        writer.close()


def test_fork_replaces_only_owned_pool_without_closing_parent_connections(monkeypatch):
    source = object()
    calls = []
    owned = SimpleNamespace(dispose=lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(bounds, "_engine", (0, source, owned))
    bounds._work.set(bounds.ReceiverWork(threading.Event(), time.monotonic() + 20))
    bounds._after_fork()
    assert calls == [{"close": False}]
    assert bounds._engine[1:] == (source, owned) and bounds._work.get() is None
