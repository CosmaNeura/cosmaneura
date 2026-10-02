"""Forward merge preserves populated Core billing and Visit Prep records."""

import os
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config

from activemodel.session_manager import get_session
from sqlalchemy import text

from tests.services.visit_preps_test import _create, _scope


@pytest.mark.skipif(
    os.environ.get("VISIT_PREP_MIGRATION_TARGET") != "b3f91f1acb40",
    reason="Owned populated Visit Prep predecessor rehearsal only",
)
def test_populated_billing_merge_preserves_both_branches(monkeypatch):
    now = datetime.now(UTC) + timedelta(seconds=2)
    scope = _scope("core-billing-merge", now=now)
    _create(scope, now=now, scheduled_at=now + timedelta(days=7))
    tables = (
        "visit_prep",
        "visit_prep_schedule_generation",
        "visit_prep_credential_generation",
    )

    def snapshot(names):
        with get_session() as session:
            return {
                name: session.execute(
                    text(f"SELECT to_jsonb(t)::text FROM public.{name} t ORDER BY id")
                )
                .scalars()
                .all()
                for name in names
            }

    before = snapshot(tables)
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "f5efcbb27acd")
    # Populate the real upstream billing operation using its existing fixture.
    from app.services.billing_header_operations import prepare_operation

    from activemodel.session_manager import get_engine

    from tests.billing_header.operations_test import case, command_for

    billing = case.__wrapped__(get_engine(), monkeypatch)
    prepare_operation(billing.actor, billing.note_id, command_for(billing))
    both = snapshot((*tables, "billing_header_prepared_operation"))
    assert {name: both[name] for name in tables} == before and all(both.values())
    command.upgrade(cfg, "e47551a9d77e")
    assert snapshot(tuple(both)) == both
