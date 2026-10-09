"""Per-statement correspondence for two independently checked SQL budgets."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import get_ident

import psycopg
from psycopg.pq import TransactionStatus

from sqlalchemy import Connection, event
from sqlalchemy.engine.default import DefaultExecutionContext
from sqlalchemy.sql import Select, visitors
from sqlalchemy.sql.dml import UpdateBase
from sqlalchemy.sql.elements import ColumnClause, TextClause
from sqlalchemy.sql.functions import FunctionElement


@dataclass(eq=False)
class RecordTimeoutScope:
    connection: object = field(repr=False)
    limits: Callable[[], tuple[int, int]] = field(repr=False)
    thread: int = field(default_factory=get_ident, repr=False)
    active: bool = field(default=True, repr=False)
    capture_owner: object | None = field(default=None, repr=False)
    setting: tuple[object, object, object, int, int] | None = field(
        default=None, repr=False
    )

    def invalidate(self, *_) -> None:
        self.setting = None


_scopes: ContextVar[tuple[RecordTimeoutScope, ...]] = ContextVar(
    "record_timeout_scopes", default=()
)
_ATTRIBUTE = "_cosmaneura_record_timeout_scope"
_STATEMENT_ATTRIBUTE = "_cosmaneura_relational_timeout_statement"


@dataclass(eq=False)
class CaptureTimeoutScopes:
    active: bool = field(default=True, init=False, repr=False)
    budget: object = field(repr=False)
    limits: Callable[[], tuple[int, int]] = field(repr=False)
    thread: int = field(default_factory=get_ident, repr=False)
    connections: dict[Connection, RecordTimeoutScope] = field(
        default_factory=dict, repr=False
    )


_capture_scopes: ContextVar[tuple[CaptureTimeoutScopes, ...]] = ContextVar(
    "capture_timeout_scopes", default=()
)
_MAX_CAPTURE_CONNECTIONS = 64
_TRANSACTION_EVENTS = (
    "begin",
    "commit",
    "rollback",
    "savepoint",
    "rollback_savepoint",
    "release_savepoint",
)


def _invalidate_capture_scopes() -> None:
    for frame in _capture_scopes.get():
        for scope in frame.connections.values():
            scope.setting = None


@contextmanager
def capture_timeout_scopes(
    budget: object, limits: Callable[[], tuple[int, int]]
) -> Iterator[None]:
    """Own only successful SQL settings within one private capture request."""
    _invalidate_capture_scopes()
    frame = CaptureTimeoutScopes(budget, limits)
    token = _capture_scopes.set((*_capture_scopes.get(), frame))
    try:
        yield
    finally:
        frame.active = False
        for connection, scope in frame.connections.items():
            scope.active = False
            scope.setting = None
            for name in _TRANSACTION_EVENTS:
                event.remove(connection, name, scope.invalidate)
        frame.connections.clear()
        _capture_scopes.reset(token)
        _invalidate_capture_scopes()


def current_capture_timeout_scope(
    connection: object, budget: object
) -> RecordTimeoutScope | None:
    # Record guards keep their independently checked ownership and nested order.
    # Never substitute a capture scope for a mismatched/nested record guard.
    frames = _capture_scopes.get()
    if _scopes.get() or len(frames) != 1 or not isinstance(connection, Connection):
        return None
    frame = frames[0]
    if not frame.active or frame.budget is not budget or frame.thread != get_ident():
        return None
    scope = frame.connections.get(connection)
    if scope is None:
        if len(frame.connections) >= _MAX_CAPTURE_CONNECTIONS:
            return None
        scope = RecordTimeoutScope(connection, frame.limits, capture_owner=budget)
        for name in _TRANSACTION_EVENTS:
            event.listen(connection, name, scope.invalidate)
        frame.connections[connection] = scope
    return scope


def _scope_is_current(connection: object, scope: RecordTimeoutScope) -> bool:
    if scope.capture_owner is None:
        return current_record_timeout_scope(connection) is scope
    frames = _capture_scopes.get()
    return (
        not _scopes.get()
        and len(frames) == 1
        and frames[0].active
        and frames[0].budget is scope.capture_owner
        and frames[0].thread == get_ident() == scope.thread
        and isinstance(connection, Connection)
        and frames[0].connections.get(connection) is scope
        and scope.active
        and scope.connection is connection
    )


def tighter_reusable_limits(limits: tuple[int, int]) -> tuple[int, int]:
    """Tighten by at most 99ms; an existing setting never exceeds fresh limits.

    The real cancellation/deadline checks still run for every statement. Close
    to expiry retain the exact positive limit; never use PostgreSQL's zero
    (unbounded) setting. This is SQL setup reuse, never authority reuse.
    """
    return (
        limits[0] if limits[0] < 100 else limits[0] // 100 * 100,
        limits[1] if limits[1] < 100 else limits[1] // 100 * 100,
    )


@contextmanager
def record_timeout_scope(
    connection: object, limits: Callable[[], tuple[int, int]]
) -> Iterator[RecordTimeoutScope]:
    _invalidate_capture_scopes()
    scope = RecordTimeoutScope(connection, limits)
    for previous in _scopes.get():
        previous.setting = None
    token = _scopes.set((*_scopes.get(), scope))

    def invalidate(*_):
        scope.setting = None

    changes = (
        (
            "begin",
            "commit",
            "rollback",
            "savepoint",
            "rollback_savepoint",
            "release_savepoint",
        )
        if isinstance(connection, Connection)
        else ()
    )
    for name in changes:
        event.listen(connection, name, invalidate)
    try:
        yield scope
    finally:
        scope.active = False
        scope.setting = None
        for name in changes:
            event.remove(connection, name, invalidate)
        _scopes.reset(token)
        _invalidate_capture_scopes()
        for previous in _scopes.get():
            previous.setting = None


def current_record_timeout_scope(connection: object) -> RecordTimeoutScope | None:
    # Nested record guards keep their original independent setter ordering.
    # Correspondence never survives scope exit, thread changes or another
    # connection. Every callback still calculates both current budget limits.
    scopes = _scopes.get()
    if len(scopes) == 1:
        scope = scopes[0]
        if (
            scope.active
            and scope.thread == get_ident()
            and scope.connection is connection
        ):
            return scope
    return None


def _relational_timeout_read(context) -> bool:
    compiled = getattr(context, "compiled", None)
    statement = getattr(compiled, "statement", None)
    # A real execution context belongs to one SQL dispatch. Repeated timeout
    # callbacks may inspect its same immutable compiled statement once; this
    # never survives into another execution or stores transaction/budget proof.
    previous = (
        getattr(context, _STATEMENT_ATTRIBUTE, None)
        if isinstance(context, DefaultExecutionContext)
        else None
    )
    if previous is not None and previous[0] is compiled and previous[1] is statement:
        return previous[2]
    relational = bool(
        isinstance(statement, Select)
        and statement.columns_clause_froms
        and not any(
            isinstance(node, (TextClause, FunctionElement, UpdateBase))
            or isinstance(node, ColumnClause)
            and node.is_literal
            for node in visitors.iterate(statement)
        )
    )
    if isinstance(context, DefaultExecutionContext):
        setattr(context, _STATEMENT_ATTRIBUTE, (compiled, statement, relational))
    return relational


def _setting_transaction(connection, cursor, context, scope):
    if (
        scope is None
        or not _scope_is_current(connection, scope)
        or not isinstance(connection, Connection)
        or not isinstance(cursor, psycopg.Cursor)
        or cursor.connection is not connection.connection.driver_connection
        or cursor.connection.info.transaction_status != TransactionStatus.INTRANS
    ):
        return None
    # Only generated relational reads can retain an existing setting. Text,
    # mutations and functions may change transaction-local policy, so always
    # refresh for them and invalidate the prior setting. No SQL is parsed.
    if not _relational_timeout_read(context):
        return None
    transaction = connection.get_transaction()
    if transaction is None or not transaction.is_active:
        return None
    return transaction, connection.get_nested_transaction()


def reusable_timeout_limits(connection, cursor, context, scope, limits):
    # Tightening applies only to a proven generated relational read. Mutations,
    # functions, nested/mismatched guards and adapters retain exact fresh values.
    return (
        tighter_reusable_limits(limits)
        if _setting_transaction(connection, cursor, context, scope) is not None
        else limits
    )


def timeout_setting_in_force(connection, cursor, context, scope, budget, limits):
    """Skip only an equal/tighter setting in this exact uninterrupted scope.

    Callers still calculate fresh limits and check both cancellation budgets
    before and after this function. This retains database settings, not access.
    Every uncertain statement or transaction selects the original setter.
    """
    current = _setting_transaction(connection, cursor, context, scope)
    if scope is None:
        return False
    if current is None or scope.setting is None:
        scope.setting = None
        return False
    previous_budget, transaction, nested, statement_ms, lock_ms = scope.setting
    if (
        previous_budget is budget
        and transaction is current[0]
        and nested is current[1]
        and statement_ms <= limits[0]
        and lock_ms <= limits[1]
    ):
        return True
    scope.setting = None
    return False


def remember_timeout_setting(connection, cursor, context, scope, budget, limits):
    """Record only a successful setting after the fresh cancellation fences."""
    current = _setting_transaction(connection, cursor, context, scope)
    if scope is not None:
        if scope.setting is not None and current is not None:
            previous_budget, transaction, nested, statement_ms, lock_ms = scope.setting
            if (
                previous_budget is budget
                and transaction is current[0]
                and nested is current[1]
                and statement_ms <= limits[0]
                and lock_ms <= limits[1]
            ):
                return
        scope.setting = (budget, *current, *limits) if current is not None else None


def acknowledge_record_timeout(
    context: object, scope: RecordTimeoutScope, capture_budget: object, origin: str
) -> None:
    if context is not None:
        setattr(context, _ATTRIBUTE, (scope, capture_budget, origin))


def consume_record_timeout(
    context: object, scope: RecordTimeoutScope, capture_budget: object, origin: str
) -> bool:
    if (
        context is not None
        and getattr(context, _ATTRIBUTE, None) == (scope, capture_budget, origin)
        and current_record_timeout_scope(scope.connection) is scope
    ):
        delattr(context, _ATTRIBUTE)
        return True
    return False
