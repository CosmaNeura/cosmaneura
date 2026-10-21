"""Transaction-local provider service waits; never a global DB/pool setting."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from app.configuration.authority import AUTHORITY_KERNEL_SETTINGS
from app.configuration.service_auth import SOVEREIGN_TOOL_AUTH_SETTINGS

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

P = ParamSpec("P")
T = TypeVar("T")


class ProviderDatabaseDeadlineExceeded(SQLAlchemyError):
    """Value-free failure; no phase/receipt or rollback certainty is invented."""


def install_provider_database_limits(
    *,
    session: Any,
    workload_client_id: str,
    settings: Any = AUTHORITY_KERNEL_SETTINGS,
) -> None:
    if (
        not settings.enabled
        or workload_client_id not in SOVEREIGN_TOOL_AUTH_SETTINGS.allowed_client_ids
    ):
        return
    # set_config(..., true) has SET LOCAL transaction semantics. Keep
    # any tighter caller/server limit instead of increasing it.
    for name, value in (("lock_timeout", "1000ms"), ("statement_timeout", "3000ms")):
        session.execute(
            text(
                "SELECT set_config(:name, CASE WHEN "
                "current_setting(:name)::interval = interval '0' OR "
                "current_setting(:name)::interval > CAST(CAST(:value AS text) AS interval) "
                "THEN CAST(:value AS text) ELSE current_setting(:name) END, true)"
            ),
            {"name": name, "value": value},
        )


def provider_database_deadline(function: Callable[P, T]) -> Callable[P, T]:
    """Bound dedicated-workload DB waits before any token/actor/lineage read.

    A managed-session recursive call installs these limits once a Session is
    present. Repeating SET LOCAL in nested authority calls is intentional:
    rollback never leaves an in-memory marker that can skip reinstallation.
    Each transaction restores pooled connection settings on commit/rollback.
    This cannot bound acquiring a connection or a lost network response.
    """

    @wraps(function)
    def bounded(*args: P.args, **kwargs: P.kwargs) -> T:
        arguments: dict[str, Any] = dict(kwargs)
        session = arguments.get("session")
        settings = arguments.get("settings", AUTHORITY_KERNEL_SETTINGS)
        workload = arguments.get("workload_client_id")
        if (
            session is None
            or not settings.enabled
            or workload not in SOVEREIGN_TOOL_AUTH_SETTINGS.allowed_client_ids
        ):
            return function(*args, **kwargs)
        try:
            install_provider_database_limits(
                session=session,
                workload_client_id=workload,
                settings=settings,
            )
            return function(*args, **kwargs)
        except DBAPIError as error:
            # PostgreSQL's fixed statuses, never driver messages or SQL values.
            if getattr(error.orig, "sqlstate", None) not in {"55P03", "57014"}:
                raise
            session.rollback()
            raise ProviderDatabaseDeadlineExceeded from None

    return bounded
