"""Up/down coverage for the additive self-delete schema rung."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config

from activemodel.session_manager import get_engine
from sqlalchemy import text

MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "migrations/versions/2026_11_02_c764298c7590_add_self_delete_account_fields.py"
)

SELF_DELETE_REVISION = "c764298c7590"
PARENT_REVISION = "312b217ebba4"


def _migration_module():
    spec = importlib.util.spec_from_file_location(
        "self_delete_account_migration",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _column_names(table: str) -> set[str]:
    with get_engine().connect() as connection:
        return set(
            connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :table"
                ),
                {"table": table},
            )
            .scalars()
            .all()
        )


def _doctor_check_names() -> set[str]:
    with get_engine().connect() as connection:
        return set(
            connection.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'doctor'::regclass AND contype = 'c'"
                )
            )
            .scalars()
            .all()
        )


def _alembic_version() -> str:
    with get_engine().connect() as connection:
        return connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()


def test_self_delete_migration_upgrade_adds_columns_and_check():
    migration = _migration_module()
    added: list[tuple] = []
    constraints: list[tuple] = []

    def add_column(table, column):
        added.append((table, column.name, str(column.type)))

    def create_check_constraint(name, table, condition):
        constraints.append((name, table, condition))

    setattr(
        migration,
        "op",
        SimpleNamespace(
            add_column=add_column,
            create_check_constraint=create_check_constraint,
        ),
    )

    migration.upgrade()

    assert any(
        table == "doctor" and name == "account_deletion_status"
        for table, name, _ in added
    )
    assert any(
        table == "practice" and name == "owner_reassignment_needed_at"
        for table, name, _ in added
    )
    assert constraints == [
        (
            "ck_doctor_account_deletion_status",
            "doctor",
            "account_deletion_status IS NULL OR "
            "account_deletion_status IN ('deletion_pending', 'deleted')",
        )
    ]


def test_self_delete_migration_downgrade_drops_additive_columns():
    migration = _migration_module()
    dropped: list[tuple] = []

    def drop_column(table, column):
        dropped.append(("column", table, column))

    def drop_constraint(name, table, type_):
        dropped.append(("constraint", table, name, type_))

    setattr(
        migration,
        "op",
        SimpleNamespace(drop_column=drop_column, drop_constraint=drop_constraint),
    )

    migration.downgrade()

    assert dropped == [
        ("column", "practice", "owner_reassignment_needed_at"),
        ("constraint", "doctor", "ck_doctor_account_deletion_status", "check"),
        ("column", "doctor", "account_deletion_status"),
    ]


def test_self_delete_migration_revises_current_head():
    migration = _migration_module()
    assert migration.revision == SELF_DELETE_REVISION
    assert migration.down_revision == PARENT_REVISION


def _has_account_deletion_status_check() -> bool:
    # SQLModel's Postgres naming convention expands the migration's short
    # constraint id into this durable database name.
    return "doctor_ck_doctor_account_deletion_status_check" in _doctor_check_names()


@pytest.mark.committed_database
def test_self_delete_migration_owner_reassignment_column_survives_roundtrip():
    """Live alembic downgrade/upgrade must drop and restore the practice flag."""

    from sqlalchemy.orm import close_all_sessions

    config = Config("alembic.ini")
    assert _alembic_version() == SELF_DELETE_REVISION
    assert "owner_reassignment_needed_at" in _column_names("practice")
    assert "account_deletion_status" in _column_names("doctor")
    assert _has_account_deletion_status_check()

    try:
        close_all_sessions()
        get_engine().dispose()
        command.downgrade(config, PARENT_REVISION)
        assert _alembic_version() == PARENT_REVISION
        assert "owner_reassignment_needed_at" not in _column_names("practice")
        assert "account_deletion_status" not in _column_names("doctor")
        assert not _has_account_deletion_status_check()
    finally:
        close_all_sessions()
        get_engine().dispose()
        command.upgrade(config, SELF_DELETE_REVISION)

    assert _alembic_version() == SELF_DELETE_REVISION
    assert "owner_reassignment_needed_at" in _column_names("practice")
    assert "account_deletion_status" in _column_names("doctor")
    assert _has_account_deletion_status_check()
