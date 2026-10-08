"""Per-statement correspondence for two independently checked SQL budgets."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import get_ident


@dataclass(eq=False)
class RecordTimeoutScope:
    connection: object = field(repr=False)
    limits: Callable[[], tuple[int, int]] = field(repr=False)
    thread: int = field(default_factory=get_ident, repr=False)
    active: bool = field(default=True, repr=False)


_scopes: ContextVar[tuple[RecordTimeoutScope, ...]] = ContextVar(
    "record_timeout_scopes", default=()
)
_ATTRIBUTE = "_cosmaneura_record_timeout_scope"


@contextmanager
def record_timeout_scope(
    connection: object, limits: Callable[[], tuple[int, int]]
) -> Iterator[RecordTimeoutScope]:
    scope = RecordTimeoutScope(connection, limits)
    token = _scopes.set((*_scopes.get(), scope))
    try:
        yield scope
    finally:
        scope.active = False
        _scopes.reset(token)


def current_record_timeout_scope(connection: object) -> RecordTimeoutScope | None:
    # Nested record guards keep their original independent setter ordering.
    # Correspondence never survives scope exit, thread changes or another
    # connection. Neither budget nor a settings value is cached between SQL.
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
