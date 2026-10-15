"""Dormant, staging-only numeric observations of the native Core request.

No request metadata, credentials, SQL text/parameters, provider URLs, or exception
values enter this ledger. Nested phase totals overlap; they are not additive.
Cursor timings exclude pool acquisition, pre-ping, raw deadline SETs and commit.
"""

import json
import math
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from time import perf_counter
from typing import Literal

from sqlalchemy import Engine, event

Phase = Literal[
    "workload_agent",
    "workload_capture",
    "clerk_auth",
    "entra_jwks",
    "policy_lookup",
    "policy_exchange",
    "policy_mount",
    "policy_binding",
    "operation",
    "downstream",
    "consent_admission",
    "session_admission",
    "preparation_metadata",
]
PHASES = (
    "workload_agent",
    "workload_capture",
    "clerk_auth",
    "entra_jwks",
    "policy_lookup",
    "policy_exchange",
    "policy_mount",
    "policy_binding",
    "operation",
    "downstream",
    "consent_admission",
    "session_admission",
    "preparation_metadata",
)
MAX_MS = 600_000.0
MAX_COUNT = 10_000
REQUEST_KINDS = frozenset(
    {
        "mount",
        "operation",
        "preparation_context",
        "prepared_window",
        "prepared_admission",
        "prepared_native_read",
        "prepared_source",
    }
)
_SQL_ATTRIBUTE = "_cosmaneura_native_timing"
_emit_lock = Lock()


def enabled() -> bool:
    # An HTTP header, test token, or permissive truthy spelling cannot enable it.
    return (
        os.environ.get("PYTHON_ENV") == "staging"
        and os.environ.get("SCRIBE_NATIVE_REQUEST_TIMING") == "true"
    )


def _elapsed(started: float) -> float:
    value = (perf_counter() - started) * 1000
    return min(MAX_MS, max(0.0, value)) if math.isfinite(value) else 0.0


class Ledger:
    def __init__(self):
        self.lock = Lock()
        self.closed = False
        self.started = perf_counter()
        self.counts = dict.fromkeys(PHASES, 0)
        self.milliseconds = dict.fromkeys(PHASES, 0.0)
        self.failures = dict.fromkeys(PHASES, 0)
        self.sql_count = 0
        self.sql_ms = 0.0
        self.sql_failures = 0
        self.request_kind = "unparsed"

    def describe_request(self, kind: str) -> None:
        # Only a closed validated action, never URL/body/identifiers or headers.
        if kind not in REQUEST_KINDS:
            return
        with self.lock:
            if not self.closed:
                self.request_kind = kind

    def record(self, name: Phase, started: float, failed: bool) -> None:
        value = _elapsed(started)
        with self.lock:
            if self.closed or name not in PHASES:
                return
            self.counts[name] = min(MAX_COUNT, self.counts[name] + 1)
            self.milliseconds[name] = min(MAX_MS, self.milliseconds[name] + value)
            self.failures[name] = min(MAX_COUNT, self.failures[name] + int(failed))

    def record_sql(self, started: float, failed: bool) -> None:
        value = _elapsed(started)
        with self.lock:
            if self.closed:
                return
            self.sql_count = min(MAX_COUNT, self.sql_count + 1)
            self.sql_ms = min(MAX_MS, self.sql_ms + value)
            self.sql_failures = min(MAX_COUNT, self.sql_failures + int(failed))

    def close(self) -> dict[str, object]:
        value = _elapsed(self.started)
        with self.lock:
            self.closed = True
            return {
                "event": "native_core_request_timing",
                "request_kind": self.request_kind,
                "total_ms": round(value, 2),
                "phase_ms": {p: round(self.milliseconds[p], 2) for p in PHASES},
                "phase_count": dict(self.counts),
                "phase_failures": dict(self.failures),
                "sql_cursor_ms": round(self.sql_ms, 2),
                "sql_cursor_count": self.sql_count,
                "sql_cursor_failures": self.sql_failures,
            }


_current: ContextVar[Ledger | None] = ContextVar("native_request_timing", default=None)


def active() -> bool:
    return _current.get() is not None


def current_ledger() -> Ledger | None:
    return _current.get()


@contextmanager
def bind_ledger(ledger: Ledger) -> Iterator[None]:
    # The capture worker intentionally clears every inherited ContextVar. Rebind
    # only this numeric observer; never propagate identity/session/deadline data.
    token = _current.set(ledger)
    try:
        yield
    finally:
        _current.reset(token)


def _emit(snapshot: dict[str, object]) -> None:
    # Avoid application logging processors that append account/request context.
    # One closed JSON line only; logging failure must not change the response.
    with _emit_lock:
        sys.stdout.write(
            json.dumps(snapshot, allow_nan=False, separators=(",", ":")) + "\n"
        )
        sys.stdout.flush()


@contextmanager
def request_timing() -> Iterator[Ledger | None]:
    ledger = None
    if enabled() and not active():
        try:
            ledger = Ledger()
        except Exception:
            pass
    if ledger is None:
        yield None
        return
    token = _current.set(ledger)
    try:
        yield ledger
    finally:
        try:
            snapshot = ledger.close()
        except Exception:
            # Closing is a fence even if an unexpected clock failure occurred.
            with ledger.lock:
                ledger.closed = True
            snapshot = None
        finally:
            _current.reset(token)
        if snapshot is not None:
            try:
                _emit(snapshot)
            except Exception:
                pass


@contextmanager
def phase(name: Phase) -> Iterator[None]:
    ledger = _current.get()
    started = None
    if ledger is not None:
        try:
            started = perf_counter()
        except Exception:
            pass
    failed = True
    try:
        yield
        failed = False
    finally:
        if ledger is not None and started is not None:
            try:
                ledger.record(name, started, failed)
            except Exception:
                pass


@event.listens_for(Engine, "before_cursor_execute")
def _before_cursor(_connection, _cursor, _statement, _parameters, context, _many):
    ledger = _current.get()
    if ledger is not None:
        try:
            setattr(context, _SQL_ATTRIBUTE, (ledger, perf_counter()))
        except Exception:
            pass


def _finish_cursor(context, failed: bool) -> None:
    try:
        entry = getattr(context, _SQL_ATTRIBUTE, None)
        if entry is not None:
            delattr(context, _SQL_ATTRIBUTE)
            entry[0].record_sql(entry[1], failed)
    except Exception:
        pass


@event.listens_for(Engine, "after_cursor_execute")
def _after_cursor(_connection, _cursor, _statement, _parameters, context, _many):
    _finish_cursor(context, False)


@event.listens_for(Engine, "handle_error")
def _cursor_error(exception_context):
    _finish_cursor(exception_context.execution_context, True)
