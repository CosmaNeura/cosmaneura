"""Populate predecessor, expand SMS outbox, preserve all retained source rows."""

import os

import pytest
from alembic import command
from alembic.config import Config

from app.services import visit_prep_interviews as interviews
from app.services.visit_prep_sms_outbox import enqueue_sms

from activemodel.session_manager import get_engine
from sqlalchemy import text
from sqlmodel import Session

from tests.integration.visit_prep_sms_outbox_test import _ready
from tests.services.visit_prep_interviews_test import _respond
from tests.services.visit_prep_synthesis_publication_test import _publish


@pytest.mark.skipif(
    os.environ.get("VISIT_PREP_MIGRATION_TARGET") != "79601b0047dc",
    reason="Explicit owned predecessor rehearsal only",
)
def test_sms_outbox_expand_preserves_existing_records_and_guards(monkeypatch):
    fixture, _, _ = _ready(monkeypatch)
    for _ in range(5):
        state = interviews.get_interview(access_token=fixture.access_token)
        if state.current_question is None:
            break
        _respond(
            fixture,
            state.current_question.key,
            answer="My invented pre-visit story."
            if state.current_question.kind == "story"
            else None,
        )
    interviews.complete_interview(access_token=fixture.access_token)
    _publish(fixture)
    _, options, _ = _ready(monkeypatch)

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

    before, old = snapshot(), guards()
    command.upgrade(Config("alembic.ini"), "950a65bbfa96")
    after = snapshot()
    assert all(after[table] == rows for table, rows in before.items())
    assert set(after) - set(before) == {"visit_prep_sms_delivery"}
    new = guards()
    assert all(new[name] == definition for name, definition in old.items())
    assert set(new) - set(old) == {"cosmas_guard_visit_prep_sms_delivery"}
    assert enqueue_sms(**options).disposition == "queued"
