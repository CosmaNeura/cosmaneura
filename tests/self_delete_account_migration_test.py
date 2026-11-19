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
PARENT_REVISION = "b38d5a410e49"


def _cols(table: str) -> set[str]:
    with get_engine().connect() as c:
        return set(
            c.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :table"
                ),
                {"table": table},
            )
            .scalars()
            .all()
        )


def _version() -> str:
    with get_engine().connect() as c:
        return c.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def test_self_delete_migration_revises_current_head():
    spec = importlib.util.spec_from_file_location("sd_mig", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == SELF_DELETE_REVISION
    assert module.down_revision == PARENT_REVISION


@pytest.mark.committed_database
def test_self_delete_migration_owner_reassignment_column_survives_roundtrip():
    from sqlalchemy.orm import close_all_sessions

    config = Config("alembic.ini")
    # Later additive rungs may sit above this revision; restore to head after.
    assert "owner_reassignment_needed_at" in _cols("practice")
    assert "account_deletion_status" in _cols("doctor")
    try:
        close_all_sessions()
        get_engine().dispose()
        command.downgrade(config, PARENT_REVISION)
        assert _version() == PARENT_REVISION
        assert "owner_reassignment_needed_at" not in _cols("practice")
    finally:
        close_all_sessions()
        get_engine().dispose()
        command.upgrade(config, "head")
    assert "owner_reassignment_needed_at" in _cols("practice")
    assert "account_deletion_status" in _cols("doctor")
