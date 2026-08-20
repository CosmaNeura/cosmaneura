import importlib.util
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

import sqlalchemy as sa

POSTGRES_URL = os.getenv("AUTHORITY_POSTGRES_TEST_URL")
SQLALCHEMY_POSTGRES_URL = (
    POSTGRES_URL.replace("postgresql://", "postgresql+psycopg://", 1)
    if POSTGRES_URL is not None
    else None
)
MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations/versions/2026_08_19_d4e5f6a7b8c9_add_v7_authority_audit.py"
)
FIRST_ID = "0198c002-0000-7000-8000-000000000001"
SECOND_ID = "0198c002-0000-7000-8000-000000000002"


def _migration_module():
    spec = importlib.util.spec_from_file_location(
        "v7_authority_postgres_migration",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _insert(connection: sa.Connection, identifier: str) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO authority_tenant "
            "(id, created_at, updated_at, status) "
            "VALUES (:id, now(), now(), 'active')"
        ),
        {"id": identifier},
    )


@pytest.mark.skipif(
    POSTGRES_URL is None,
    reason="AUTHORITY_POSTGRES_TEST_URL is required for the lock test",
)
def test_downgrade_guard_serializes_concurrent_inserts_and_observes_commits() -> None:
    migration = _migration_module()
    assert SQLALCHEMY_POSTGRES_URL is not None
    engine = sa.create_engine(SQLALCHEMY_POSTGRES_URL)
    with engine.begin() as connection:
        assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar() == (
            migration.revision
        )
        for table_name in migration._DURABLE_V7_TABLES:
            assert connection.execute(
                sa.text(f'SELECT count(*) FROM "{table_name}"')
            ).scalar() == 0

    writer = engine.connect()
    writer_transaction = writer.begin()
    _insert(writer, FIRST_ID)
    guard_started = Event()

    def guarded_downgrade_check() -> None:
        with engine.connect() as connection, connection.begin():
            migration.op = SimpleNamespace(
                get_context=lambda: SimpleNamespace(as_sql=False),
                get_bind=lambda: connection,
            )
            guard_started.set()
            migration._refuse_populated_or_offline_downgrade()

    with ThreadPoolExecutor(max_workers=1) as executor:
        guarded = executor.submit(guarded_downgrade_check)
        assert guard_started.wait(timeout=2)
        time.sleep(0.2)
        assert not guarded.done(), "ACCESS EXCLUSIVE must wait for the active writer"
        writer_transaction.commit()
        writer.close()
        with pytest.raises(RuntimeError, match="durable records exist"):
            guarded.result(timeout=5)

    with engine.begin() as connection:
        assert connection.execute(
            sa.text("SELECT count(*) FROM authority_tenant WHERE id = :id"),
            {"id": FIRST_ID},
        ).scalar() == 1
        connection.execute(
            sa.text("DELETE FROM authority_tenant WHERE id = :id"),
            {"id": FIRST_ID},
        )

    lock_connection = engine.connect()
    lock_transaction = lock_connection.begin()
    migration.op = SimpleNamespace(
        get_context=lambda: SimpleNamespace(as_sql=False),
        get_bind=lambda: lock_connection,
    )
    migration._refuse_populated_or_offline_downgrade()

    insert_started = Event()

    def late_insert() -> None:
        with engine.begin() as connection:
            insert_started.set()
            _insert(connection, SECOND_ID)

    with ThreadPoolExecutor(max_workers=1) as executor:
        inserted = executor.submit(late_insert)
        assert insert_started.wait(timeout=2)
        time.sleep(0.2)
        assert not inserted.done(), "writer must not pass the downgrade locks"
        lock_transaction.rollback()
        lock_connection.close()
        inserted.result(timeout=5)

    with engine.begin() as connection:
        assert connection.execute(
            sa.text("SELECT count(*) FROM authority_tenant WHERE id = :id"),
            {"id": SECOND_ID},
        ).scalar() == 1
        connection.execute(
            sa.text("DELETE FROM authority_tenant WHERE id = :id"),
            {"id": SECOND_ID},
        )
    engine.dispose()
