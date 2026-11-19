"""Up/down coverage for late-Checkout refuse marker + unique indexes."""

import importlib.util
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from activemodel.session_manager import get_engine
from sqlalchemy import text

MIGRATION_PATH = (
    Path(__file__).parents[1] / "migrations/versions/"
    "2026_11_19_5708b4399768_unique_stripe_checkout_follow_up_audit.py"
)
FOLLOW_UP_REVISION = "5708b4399768"
PARENT_REVISION = "5f3c9a1e7b24"
FOLLOW_UP_INDEX = "uq_practice_access_audit_stripe_checkout_follow_up"
REFUND_INDEX = "uq_practice_access_audit_stripe_refund_needed"


def _indexes(table: str) -> set[str]:
    with get_engine().connect() as c:
        return set(
            c.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = 'public' AND tablename = :table"
                ),
                {"table": table},
            )
            .scalars()
            .all()
        )


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


def test_follow_up_unique_migration_revises_self_delete_head():
    spec = importlib.util.spec_from_file_location("fu_mig", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == FOLLOW_UP_REVISION
    assert module.down_revision == PARENT_REVISION


@pytest.mark.committed_database
def test_follow_up_unique_index_survives_roundtrip():
    from sqlalchemy.orm import close_all_sessions

    config = Config("alembic.ini")
    assert _version() == FOLLOW_UP_REVISION
    assert FOLLOW_UP_INDEX in _indexes("practice_access_audit")
    assert REFUND_INDEX in _indexes("practice_access_audit")
    assert "late_checkout_refused_at" in _cols("practice")
    try:
        close_all_sessions()
        get_engine().dispose()
        command.downgrade(config, PARENT_REVISION)
        assert _version() == PARENT_REVISION
        assert FOLLOW_UP_INDEX not in _indexes("practice_access_audit")
        assert REFUND_INDEX not in _indexes("practice_access_audit")
        assert "late_checkout_refused_at" not in _cols("practice")
    finally:
        close_all_sessions()
        get_engine().dispose()
        command.upgrade(config, "head")
    assert _version() == FOLLOW_UP_REVISION
    assert FOLLOW_UP_INDEX in _indexes("practice_access_audit")
    assert REFUND_INDEX in _indexes("practice_access_audit")
    assert "late_checkout_refused_at" in _cols("practice")
