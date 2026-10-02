"""Actual predecessor expansion retains old answers, packages and guards."""

import os

import pytest
from alembic import command
from alembic.config import Config

from app.services import visit_prep_interviews as interviews
from app.services.visit_prep_delivery_links import issue_delivery_link

from activemodel.session_manager import get_engine
from sqlalchemy import text
from sqlmodel import Session

from tests.integration.visit_prep_delivery_links_test import _arguments, _intent
from tests.services.visit_prep_contribution_projection_test import _prepared
from tests.services.visit_prep_interviews_test import _fixture
from tests.services.visit_prep_synthesis_publication_test import _publish


@pytest.mark.skipif(
    os.environ.get("VISIT_PREP_MIGRATION_TARGET") != "85d330017454",
    reason="Explicit owned predecessor rehearsal only",
)
def test_delivery_link_expand_preserves_every_prior_row_and_guard(monkeypatch):
    completed = _prepared(monkeypatch, "alias-expand-complete", adherence="some")
    _publish(completed)
    fixture = _fixture(monkeypatch, "alias-expand-active")

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
    command.upgrade(Config("alembic.ini"), "898301cffd5c")
    after, new_guards = snapshot(), guards()
    assert all(after[table] == rows for table, rows in before.items())
    assert set(after) - set(before) == {"visit_prep_delivery_link"}
    assert all(new_guards[name] == body for name, body in old_guards.items())
    assert set(new_guards) - set(old_guards) == {
        "cosmas_guard_visit_prep_delivery_link"
    }
    receipt = issue_delivery_link(**_arguments(fixture, _intent(fixture)))
    assert (
        interviews.get_interview(access_token=receipt.access_token).status
        == "confirmation_required"
    )
    assert len(before["visit_prep_question_response"]) == 5
    assert len(before["visit_prep_synthesis_artifact"]) == 1
