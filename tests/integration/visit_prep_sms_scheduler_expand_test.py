"""Actual populated predecessor expansion; no prior clinical record is changed."""

import os

import pytest
from alembic import command
from alembic.config import Config

from activemodel.session_manager import get_engine
from sqlalchemy import text
from sqlmodel import Session

from tests.integration.visit_prep_delivery_links_expand_test import (
    _arguments,
    _intent,
    _publish,
    issue_delivery_link,
)
from tests.integration.visit_prep_sms_scheduler_test import _ready, _run, _stored
from tests.services.visit_prep_contribution_projection_test import _prepared


@pytest.mark.skipif(
    os.environ.get("VISIT_PREP_MIGRATION_TARGET") != "898301cffd5c",
    reason="Explicit owned predecessor rehearsal only",
)
def test_scheduler_expand_preserves_all_prior_records_and_guard_definitions(
    monkeypatch,
):
    completed = _prepared(monkeypatch, "scan-expand-complete", adherence="some")
    _publish(completed)
    fixture, options, source = _ready(monkeypatch)
    alias = issue_delivery_link(**_arguments(fixture, _intent(fixture)))
    assert alias.disposition == "issued"

    def snapshot():
        with Session(get_engine()) as session:
            tables = (
                session.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version' ORDER BY tablename"
                    )
                )
                .scalars()
                .all()
            )
            return {
                table: session.execute(
                    text(
                        f"SELECT to_jsonb(t)::text FROM public.{table} t ORDER BY to_jsonb(t)::text"
                    )
                )
                .scalars()
                .all()
                for table in tables
            }

    def guards():
        with Session(get_engine()) as session:
            return dict(
                session.execute(
                    text(
                        "SELECT p.proname, pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname LIKE 'cosmas_guard_visit_prep%' ORDER BY p.proname"
                    )
                ).all()
            )

    before, old_guards = snapshot(), guards()
    command.upgrade(Config("alembic.ini"), "34f75ffd5135")
    after = snapshot()
    assert all(after[table] == rows for table, rows in before.items())
    assert set(after) - set(before) == {"visit_prep_sms_scan"}
    assert guards() == old_guards
    assert len(before["visit_prep_question_response"]) == 5
    assert len(before["visit_prep_synthesis_artifact"]) == 1
    assert len(before["visit_prep_delivery_link"]) == 1
    assert _run(options, source)["queued"] == 1
    assert len(_stored(fixture)[0]) == 1 and len(_stored(fixture)[1]) == 2
