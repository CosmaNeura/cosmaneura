"""Up/down coverage for the additive self-delete schema rung."""

import importlib.util
from pathlib import Path

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
PARENT_REVISION = "69bad2e072e2"


def _migration_module():
    spec = importlib.util.spec_from_file_location(
        "self_delete_account_migration", MIGRATION_PATH
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


def test_self_delete_migration_revises_current_head():
    migration = _migration_module()
    assert migration.revision == SELF_DELETE_REVISION
    assert migration.down_revision == PARENT_REVISION


@pytest.mark.committed_database
def test_self_delete_migration_owner_reassignment_column_survives_roundtrip():
    from sqlalchemy.orm import close_all_sessions

    config = Config("alembic.ini")
    assert _alembic_version() == SELF_DELETE_REVISION
    assert "owner_reassignment_needed_at" in _column_names("practice")
    assert "account_deletion_status" in _column_names("doctor")
    assert "doctor_ck_doctor_account_deletion_status_check" in _doctor_check_names()
    try:
        close_all_sessions()
        get_engine().dispose()
        command.downgrade(config, PARENT_REVISION)
        assert _alembic_version() == PARENT_REVISION
        assert "owner_reassignment_needed_at" not in _column_names("practice")
        assert "account_deletion_status" not in _column_names("doctor")
    finally:
        close_all_sessions()
        get_engine().dispose()
        command.upgrade(config, SELF_DELETE_REVISION)
    assert _alembic_version() == SELF_DELETE_REVISION
    assert "owner_reassignment_needed_at" in _column_names("practice")
    assert "account_deletion_status" in _column_names("doctor")
    assert "doctor_ck_doctor_account_deletion_status_check" in _doctor_check_names()
