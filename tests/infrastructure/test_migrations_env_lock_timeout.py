"""Prove migrations/env.py sets session lock_timeout before run_migrations."""

from __future__ import annotations

import ast
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / "migrations" / "env.py"


def _load_run_migrations_online() -> tuple[Any, str, dict[str, Any]]:
    """Load run_migrations_online without importing app models."""
    source = ENV_PATH.read_text()
    tree = ast.parse(source)

    constant = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "MIGRATION_LOCK_TIMEOUT"
            for target in node.targets
        )
    )
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "run_migrations_online"
    )

    namespace: dict[str, Any] = {}
    exec(
        compile(
            ast.Module(body=[constant, function], type_ignores=[]),
            str(ENV_PATH),
            "exec",
        ),
        namespace,
    )
    return (
        namespace["run_migrations_online"],
        namespace["MIGRATION_LOCK_TIMEOUT"],
        namespace,
    )


def test_online_migration_sets_lock_timeout_before_run_migrations() -> None:
    run_migrations_online, lock_timeout, namespace = _load_run_migrations_online()
    assert lock_timeout == "4s"

    order: list[tuple[str, str | None]] = []

    connection = MagicMock(name="connection")
    connection.exec_driver_sql.side_effect = lambda statement: order.append(
        ("exec_driver_sql", statement)
    )
    connection.commit.side_effect = lambda: order.append(("commit", None))

    @contextmanager
    def _connect():
        yield connection

    connectable = MagicMock(name="connectable")
    connectable.connect.side_effect = lambda: _connect()

    context = MagicMock(name="context")

    @contextmanager
    def _begin_transaction():
        order.append(("begin_transaction", None))
        yield

    context.begin_transaction.side_effect = lambda: _begin_transaction()
    context.configure.side_effect = lambda **kwargs: order.append(("configure", None))
    context.run_migrations.side_effect = lambda: order.append(("run_migrations", None))

    config = MagicMock()
    config.config_ini_section = "alembic"
    config.get_section.return_value = {}

    pool = MagicMock()
    pool.NullPool = object()

    namespace.update(
        {
            "engine_from_config": MagicMock(return_value=connectable),
            "pool": pool,
            "context": context,
            "config": config,
            "target_metadata": object(),
        }
    )

    run_migrations_online()

    set_statement = f"SET lock_timeout = '{lock_timeout}'"
    assert order[0] == ("exec_driver_sql", set_statement)
    assert order[1] == ("commit", None)
    assert order.index(("exec_driver_sql", set_statement)) < order.index(
        ("run_migrations", None)
    )
    assert order.index(("commit", None)) < order.index(("configure", None))
    assert order.index(("configure", None)) < order.index(("run_migrations", None))
    connection.exec_driver_sql.assert_called_once_with(set_statement)
    connection.commit.assert_called_once_with()
