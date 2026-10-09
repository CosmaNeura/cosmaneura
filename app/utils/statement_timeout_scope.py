"""Per-statement correspondence for two independently checked SQL budgets."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import get_ident

import psycopg
from psycopg.pq import TransactionStatus

from sqlalchemy import Connection, event
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
    setting: tuple[object, object, object, int, int] | None = field(
        default=None, repr=False
    )


_scopes: ContextVar[tuple[RecordTimeoutScope, ...]] = ContextVar(
    "record_timeout_scopes", default=()
)
_ATTRIBUTE = "_cosmaneura_record_timeout_scope"


@contextmanager
def record_timeout_scope(
    connection: object, limits: Callable[[], tuple[int, int]]
) -> Iterator[RecordTimeoutScope]:
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


def _setting_transaction(connection, cursor, context, scope):
    if (
        scope is None
        or current_record_timeout_scope(connection) is not scope
        or not isinstance(connection, Connection)
        or not isinstance(cursor, psycopg.Cursor)
        or cursor.connection is not connection.connection.driver_connection
        or cursor.connection.info.transaction_status != TransactionStatus.INTRANS
    ):
        return None
    compiled = getattr(context, "compiled", None)
    statement = getattr(compiled, "statement", None)
    # Only generated relational reads can retain an existing setting. Text,
    # mutations and functions may change transaction-local policy, so always
    # refresh for them and invalidate the prior setting. No SQL is parsed.
    if (
        not isinstance(statement, Select)
        # This conservative column-derived subset avoids constructing an ORM
        # compile state on every budget callback. An explicit FROM without
        # relational selected columns takes the original setter instead.
        or not statement.columns_clause_froms
        or any(
            isinstance(node, (TextClause, FunctionElement, UpdateBase))
            or isinstance(node, ColumnClause)
            and node.is_literal
            for node in visitors.iterate(statement)
        )
    ):
        return None
    transaction = connection.get_transaction()
    if transaction is None or not transaction.is_active:
        return None
    return transaction, connection.get_nested_transaction()


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
