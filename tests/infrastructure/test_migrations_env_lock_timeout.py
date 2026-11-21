"""Prove migrations/env.py sets session lock_timeout before run_migrations."""

from __future__ import annotations

import ast
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / "migrations" / "env.py"


def _load_env_helpers(
    *function_names: str,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """Load selected env.py helpers without importing app models."""
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
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in function_names
    ]
    assert {function.name for function in functions} == set(function_names)

    namespace: dict[str, Any] = {}
    exec(
        compile(
            ast.Module(body=[constant, *functions], type_ignores=[]),
            str(ENV_PATH),
            "exec",
        ),
        namespace,
    )
    return (
        {name: namespace[name] for name in function_names},
        namespace["MIGRATION_LOCK_TIMEOUT"],
        namespace,
    )


def test_online_migration_sets_lock_timeout_before_run_migrations() -> None:
    helpers, lock_timeout, namespace = _load_env_helpers("run_migrations_online")
    run_migrations_online = helpers["run_migrations_online"]
    assert lock_timeout == "4s"

    order: list[tuple[str, str | None]] = []
    inside_transaction = {"active": False}

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
        inside_transaction["active"] = True
        try:
            yield
        finally:
            inside_transaction["active"] = False
            order.append(("end_transaction", None))

    def _configure(**kwargs: Any) -> None:
        order.append(("configure", None))
        assert kwargs.get("connection") is connection

    def _run_migrations() -> None:
        assert inside_transaction["active"] is True
        order.append(("run_migrations", None))

    context.begin_transaction.side_effect = lambda: _begin_transaction()
    context.configure.side_effect = _configure
    context.run_migrations.side_effect = _run_migrations

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
    assert order.index(("configure", None)) < order.index(("begin_transaction", None))
    assert order.index(("begin_transaction", None)) < order.index(
        ("run_migrations", None)
    )
    assert order.index(("run_migrations", None)) < order.index(("end_transaction", None))
    connection.exec_driver_sql.assert_called_once_with(set_statement)
    connection.commit.assert_called_once_with()
    context.configure.assert_called_once()
    configure_kwargs = context.configure.call_args.kwargs
    assert configure_kwargs["connection"] is connection


def test_offline_migration_sets_lock_timeout_inside_begin_transaction() -> None:
    helpers, lock_timeout, namespace = _load_env_helpers("run_migrations_offline")
    run_migrations_offline = helpers["run_migrations_offline"]
    assert lock_timeout == "4s"

    order: list[tuple[str, str | None]] = []
    inside_transaction = {"active": False}

    context = MagicMock(name="context")

    @contextmanager
    def _begin_transaction():
        order.append(("begin_transaction", None))
        inside_transaction["active"] = True
        try:
            yield
        finally:
            inside_transaction["active"] = False
            order.append(("end_transaction", None))

    def _execute(statement: str) -> None:
        assert inside_transaction["active"] is True
        order.append(("execute", statement))

    def _run_migrations() -> None:
        assert inside_transaction["active"] is True
        order.append(("run_migrations", None))

    context.begin_transaction.side_effect = lambda: _begin_transaction()
    context.configure.side_effect = lambda **kwargs: order.append(("configure", None))
    context.execute.side_effect = _execute
    context.run_migrations.side_effect = _run_migrations

    config = MagicMock()
    config.get_main_option.return_value = "postgresql://example.invalid/db"

    namespace.update(
        {
            "context": context,
            "config": config,
            "target_metadata": object(),
        }
    )

    run_migrations_offline()

    set_statement = f"SET lock_timeout = '{lock_timeout}'"
    assert ("configure", None) in order
    assert order.index(("configure", None)) < order.index(("begin_transaction", None))
    assert order.index(("begin_transaction", None)) < order.index(
        ("execute", set_statement)
    )
    assert order.index(("execute", set_statement)) < order.index(
        ("run_migrations", None)
    )
    assert order.index(("run_migrations", None)) < order.index(("end_transaction", None))
    context.execute.assert_called_once_with(set_statement)
    context.run_migrations.assert_called_once_with()
