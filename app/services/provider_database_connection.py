"""Owned connection lane for authenticated Sovereign receiver work only.

No global engine/pool setting changes. A timeout discards the owned connection;
it is not evidence that PostgreSQL cancelled or rolled back a remote commit.
"""

from __future__ import annotations

import contextvars
import ipaddress
import os
import selectors
import socket
import threading
import time
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import pq
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.configuration.authority import AUTHORITY_KERNEL_SETTINGS
from app.configuration.service_auth import SOVEREIGN_TOOL_AUTH_SETTINGS
from app.services.provider_database_deadline import ProviderDatabaseDeadlineExceeded

from activemodel import session_manager
from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlmodel import Session

POOL_SECONDS = 1.0
CONNECT_SECONDS = 3.0
SOCKET_SECONDS = 3.0
RECEIVER_SECONDS = 20.0
DNS_SECONDS = 1.0
POLL_SECONDS = 0.05


class ProviderConnectionUnavailable(psycopg.OperationalError):
    """Fixed failure only; never conninfo, SQL, credentials or vendor text."""


@dataclass
class ReceiverWork:
    cancelled: threading.Event
    deadline: float


_work: contextvars.ContextVar[ReceiverWork | None] = contextvars.ContextVar(
    "provider_receiver_work", default=None
)
_engine: tuple[int, Engine, Engine] | None = None
_engine_lock = threading.Lock()
_dns_slots = threading.BoundedSemaphore(2)


def _remaining(deadline: float) -> float:
    state = _work.get()
    if state is not None:
        deadline = min(deadline, state.deadline)
        if state.cancelled.is_set():
            raise ProviderConnectionUnavailable("provider_database_unavailable")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ProviderConnectionUnavailable("provider_database_unavailable")
    return remaining


def _address(host: str, port: str, deadline: float) -> str:
    """At most two DNS threads; an OS-stuck lookup cannot spawn replacements.

    Only a hostname/port reaches these daemon threads. The caller is bounded,
    but this does not claim to terminate an OS resolver that never returns.
    """
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    if not _dns_slots.acquire(blocking=False):
        raise ProviderConnectionUnavailable("provider_database_unavailable")
    done = threading.Event()
    addresses: list[str] = []

    def lookup() -> None:
        try:
            rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            if rows:
                addresses.append(str(ipaddress.ip_address(rows[0][4][0])))
        except Exception:
            pass
        finally:
            _dns_slots.release()
            done.set()

    try:
        threading.Thread(
            target=lookup, daemon=True, name="provider-database-dns"
        ).start()
    except Exception:
        _dns_slots.release()
        raise ProviderConnectionUnavailable("provider_database_unavailable") from None
    deadline = min(deadline, time.monotonic() + DNS_SECONDS)
    while not done.wait(min(POLL_SECONDS, _remaining(deadline))):
        pass
    _remaining(deadline)
    if not addresses:
        raise ProviderConnectionUnavailable("provider_database_unavailable")
    return addresses[0]


def _wait(gen: Generator[Any, int, Any], descriptor: int, deadline: float) -> Any:
    """Drive Psycopg's real nonblocking query/commit/rollback generator."""
    try:
        mask = next(gen)
        with selectors.DefaultSelector() as selector:
            while True:
                remaining = _remaining(deadline)
                selector.register(descriptor, int(mask))
                try:
                    ready = selector.select(min(POLL_SECONDS, remaining))
                finally:
                    selector.unregister(descriptor)
                _remaining(deadline)
                mask = gen.send(ready[0][1] if ready else 0)
    except StopIteration as completed:
        return completed.value
    finally:
        gen.close()


class ProviderConnection(psycopg.Connection):
    @classmethod
    def connect(cls, conninfo: str = "", **kwargs: Any) -> ProviderConnection:
        deadline = time.monotonic() + CONNECT_SECONDS
        pgconn = None
        try:
            params = conninfo_to_dict(conninfo, **kwargs)
            host = params.get("host", "")
            # No ambient PGHOST/PGSERVICE discovery or unbounded multi-host retries.
            if (
                not host
                or "," in host
                or params.get("service")
                or os.environ.get("PGSERVICE")
            ):
                raise ProviderConnectionUnavailable("provider_database_unavailable")
            if not host.startswith("/"):
                address = params.get("hostaddr") or _address(
                    host, params.get("port", "5432"), deadline
                )
                params["hostaddr"] = str(ipaddress.ip_address(address))
            params["connect_timeout"] = str(
                max(1, min(3, int(params.get("connect_timeout", "3"))))
            )
            _remaining(deadline)
            # hostaddr avoids a second libpq DNS lookup; retain host for TLS identity.
            pgconn = pq.PGconn.connect_start(make_conninfo("", **params).encode())
            with selectors.DefaultSelector() as selector:
                while True:
                    _remaining(deadline)
                    status = pgconn.connect_poll()
                    if status == pq.PollingStatus.OK:
                        pgconn.nonblocking = 1
                        _remaining(deadline)
                        return cls(pgconn)
                    if status not in (
                        pq.PollingStatus.READING,
                        pq.PollingStatus.WRITING,
                    ):
                        raise ProviderConnectionUnavailable(
                            "provider_database_unavailable"
                        )
                    mask = (
                        selectors.EVENT_READ
                        if status == pq.PollingStatus.READING
                        else selectors.EVENT_WRITE
                    )
                    selector.register(pgconn.socket, mask)
                    try:
                        selector.select(min(POLL_SECONDS, _remaining(deadline)))
                    finally:
                        selector.unregister(pgconn.socket)
        except Exception:
            if pgconn is not None:
                pgconn.finish()
            raise ProviderConnectionUnavailable(
                "provider_database_unavailable"
            ) from None
        except BaseException:
            if pgconn is not None:
                pgconn.finish()
            raise

    def wait(self, gen: Generator[Any, int, Any], interval: float | None = None) -> Any:
        try:
            return _wait(gen, self.pgconn.socket, time.monotonic() + SOCKET_SECONDS)
        except psycopg.Error as error:
            # Ordinary PostgreSQL errors must retain the original rollback and
            # integrity-conflict/replay path. Only uncertain transport is discarded.
            if error.sqlstate is not None:
                raise
            self._discard()
            raise ProviderConnectionUnavailable(
                "provider_database_unavailable"
            ) from None
        except (OSError, ValueError):
            self._discard()
            raise ProviderConnectionUnavailable(
                "provider_database_unavailable"
            ) from None
        except BaseException:
            # No blocking cancellation request or recycling an uncertain connection.
            self._discard()
            raise

    def _discard(self) -> None:
        try:
            self._closed = True
            self.pgconn.finish()
        except Exception:
            # Preserve the original failure and never expose a driver close error.
            pass


def provider_engine(source: Engine) -> Engine:
    global _engine
    if not _engine_lock.acquire(timeout=POOL_SECONDS):
        raise ProviderDatabaseDeadlineExceeded
    try:
        if _engine is None or _engine[:2] != (os.getpid(), source):
            if (
                source.dialect.name != "postgresql"
                or source.dialect.driver != "psycopg"
            ):
                raise ProviderDatabaseDeadlineExceeded
            args, kwargs = source.dialect.create_connect_args(source.url)
            owned = create_engine(
                source.url,
                creator=lambda: ProviderConnection.connect(*args, **kwargs),
                pool_size=2,
                max_overflow=0,
                pool_timeout=POOL_SECONDS,
                pool_pre_ping=True,
                echo=False,
                hide_parameters=True,
                json_serializer=source.dialect._json_serializer,
            )
            _engine = (os.getpid(), source, owned)
        return _engine[2]
    finally:
        _engine_lock.release()


@contextmanager
def provider_receiver_session(
    workload_client_id: str | None,
    cancelled: threading.Event,
    settings: Any = AUTHORITY_KERNEL_SETTINGS,
) -> Iterator[None]:
    if (
        not settings.enabled
        or workload_client_id not in SOVEREIGN_TOOL_AUTH_SETTINGS.allowed_client_ids
    ):
        yield
        return
    prior = session_manager._session_context.get()
    # Never move already accepted/locked work into a different transaction.
    if prior is not None and (
        prior.in_transaction() or prior.new or prior.dirty or prior.deleted
    ):
        raise ProviderDatabaseDeadlineExceeded
    state = ReceiverWork(cancelled, time.monotonic() + RECEIVER_SECONDS)
    work_token = _work.set(state)
    session = None
    session_token = None
    try:
        session = Session(provider_engine(session_manager.get_engine()))
        session_token = session_manager._session_context.set(session)
        _remaining(state.deadline)
        yield
    except (PoolTimeout, ProviderConnectionUnavailable):
        if session is not None:
            session.invalidate()
        raise ProviderDatabaseDeadlineExceeded from None
    except SQLAlchemyError as error:
        if isinstance(getattr(error, "orig", None), ProviderConnectionUnavailable):
            if session is not None:
                session.invalidate()
            raise ProviderDatabaseDeadlineExceeded from None
        raise
    finally:
        try:
            if session is not None:
                if cancelled.is_set() or time.monotonic() >= state.deadline:
                    session.invalidate()
                session.close()
        finally:
            if session_token is not None:
                session_manager._session_context.reset(session_token)
            _work.reset(work_token)


def _after_fork() -> None:
    global _engine, _engine_lock, _dns_slots
    # Never reuse an inherited pool, thread lock or resolver permit state.
    if _engine is not None:
        _, source, owned = _engine
        owned.dispose(close=False)
        _engine = (os.getpid(), source, owned)
    _work.set(None)
    _engine_lock = threading.Lock()
    _dns_slots = threading.BoundedSemaphore(2)


os.register_at_fork(after_in_child=_after_fork)
