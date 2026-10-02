"""Populated forward guard expansion retains old proposals and accepted words."""

import os

import pytest
from alembic import command
from alembic.config import Config

from activemodel.session_manager import get_session
from sqlalchemy import text

from tests.integration.visit_prep_clarification_decisions_test import _submit
from tests.services.visit_prep_clarification_evidence_test import _proposal


@pytest.mark.skipif(
    os.environ.get("VISIT_PREP_MIGRATION_TARGET") != "dd93e7a689f1",
    reason="Owned populated predecessor rehearsal only",
)
def test_populated_guard_expand_retains_every_original_and_old_decision(monkeypatch):
    fixture, proposal, row = _proposal(monkeypatch)
    row.save()
    _submit(fixture, proposal, action="confirm")
    tables = (
        "visit_prep_question_response",
        "visit_prep_contribution_event",
        "visit_prep_clarification_proposal",
        "visit_prep_clarification_decision",
    )

    def snapshot():
        with get_session() as session:
            return {
                table: session.execute(
                    text(f"SELECT to_jsonb(t)::text FROM public.{table} t ORDER BY id")
                )
                .scalars()
                .all()
                for table in tables
            }

    before = snapshot()
    command.upgrade(Config("alembic.ini"), "208271feaef7")
    assert snapshot() == before and all(before.values())
