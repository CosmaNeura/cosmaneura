"""Run with --confcutdir=tests/billing_header; never invoke the global Clerk cleanup hook."""

import ast
import os
import socket
import sys
import types
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[2]
ISOLATED = "app" not in sys.modules
if ISOLATED:
    # Reuse the existing isolated authority test environment as data only.
    tree = ast.parse((ROOT / "tests/authority_kernel/conftest.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_TEST_ENVIRONMENT"
            for t in node.targets
        ):
            os.environ.update(ast.literal_eval(node.value))
    os.environ["BILLING_SOURCE_POLICY_JSON"] = ""
    import decouple
    import structlog

    decouple.config = decouple.Config(decouple.RepositoryEmpty())
    package = types.ModuleType("app")
    package.__path__ = [str(ROOT / "app")]
    vars(package).update(root=ROOT, log=structlog.get_logger())
    sys.modules["app"] = package
    import activemodel

    activemodel.init("postgresql+psycopg://synthetic@127.0.0.1/core_billing_unused")


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    original = socket.socket.connect
    dsn = os.environ.get("CORE_BILLING_TEST_DATABASE_URL", "")
    target = urlsplit(dsn)

    def bounded(sock, address):
        if (
            isinstance(address, tuple)
            and target.hostname == "127.0.0.1"
            and address[:2] == (target.hostname, target.port)
        ):
            return original(sock, address)
        raise AssertionError("external network forbidden in billing tests")

    monkeypatch.setattr(socket.socket, "connect", bounded)


@pytest.fixture
def billing_database():
    """Dedicated disposable local DB only; create prerequisite models then the real expand rung."""
    dsn = os.environ.get("CORE_BILLING_TEST_DATABASE_URL", "")
    target = urlsplit(dsn)
    if not dsn:
        pytest.skip("explicit owned local PostgreSQL required")
    assert (
        target.hostname == "127.0.0.1"
        and target.path.startswith("/core_billing_")
        and target.port
    )
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from activemodel.session_manager import SessionManager
    from sqlmodel import SQLModel, create_engine

    engine = create_engine(dsn)
    operation_table = SQLModel.metadata.tables["billing_header_prepared_operation"]

    action_table = SQLModel.metadata.tables["billing_header_action"]
    continuation_table = SQLModel.metadata.tables[
        "billing_header_generation_continuation"
    ]
    # Local source-model prerequisites only; no configured-environment migration.
    with engine.begin() as connection:
        SQLModel.metadata.create_all(
            connection,
            tables=[
                t
                for t in SQLModel.metadata.sorted_tables
                if t not in (operation_table, action_table, continuation_table)
            ],
        )
        migrations = [
            (
                operation_table,
                "2026_10_02_f5efcbb27acd_add_billing_header_prepared_operation.py",
            ),
            (action_table, "2026_10_02_211aefdf709b_add_billing_header_action.py"),
        ]
        for table, filename in migrations:
            path = ROOT / "migrations/versions" / filename
            spec = importlib.util.spec_from_file_location("billing_test_expand", path)
            assert spec is not None
            assert spec.loader is not None
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            if not connection.dialect.has_table(connection, table.name):
                with Operations.context(MigrationContext.configure(connection)):
                    migration.upgrade()
        if not any(
            column["name"] == "generation_id"
            for column in connection.dialect.get_columns(connection, action_table.name)
        ):
            path = (
                ROOT
                / "migrations/versions/2026_10_07_4c5b17148cff_add_async_billing_action.py"
            )
            spec = importlib.util.spec_from_file_location(
                "billing_test_async_expand", path
            )
            assert spec is not None
            assert spec.loader is not None
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
        if not connection.dialect.has_table(connection, continuation_table.name):
            path = (
                ROOT
                / "migrations/versions/2026_10_07_56789df3c14c_add_billing_generation_continuation.py"
            )
            spec = importlib.util.spec_from_file_location(
                "billing_test_continue_expand", path
            )
            assert spec is not None and spec.loader is not None
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
    manager = SessionManager.get_instance()
    original_engine = manager._engine
    manager._engine = engine
    try:
        yield engine
    finally:
        manager._engine = original_engine
        engine.dispose()


def pytest_configure(config):
    config.option.enable_pretty_traceback = False
    config.option.enable_pretty_traceback_local_stack_only = True
