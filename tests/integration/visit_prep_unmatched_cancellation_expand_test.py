"""Owned predecessor expansion adds a source fence without rewriting records."""

import os

import pytest
from alembic import command
from alembic.config import Config

from app.services import visit_prep_booking

from activemodel.session_manager import get_engine
from sqlalchemy import text
from sqlmodel import Session

from tests.integration.visit_prep_completed_booking_reschedule_test import _completed
from tests.services.visit_prep_synthesis_publication_test import _publish


@pytest.mark.skipif(
    os.environ.get("VISIT_PREP_MIGRATION_TARGET") != "3a7854eb39e0",
    reason="Owned populated predecessor rehearsal only",
)
def test_unmatched_cancellation_expand_preserves_populated_predecessor(monkeypatch):
    # Populate with the predecessor creator, which had no cancellation-table
    # query. This bypass is scoped only to preparing the old-schema fixture.
    with monkeypatch.context() as predecessor:
        predecessor.setattr(
            visit_prep_booking,
            "check_booking_observation",
            lambda *_args, **_kwargs: None,
        )
        fixture, *_ = _completed(predecessor)
        _publish(fixture)
    tables = (
        "visit_prep",
        "visit_prep_schedule_generation",
        "visit_prep_credential_generation",
        "visit_prep_booking_receipt",
        "visit_prep_booking_event",
        "visit_prep_question_response",
        "visit_prep_contribution_event",
        "visit_prep_synthesis_artifact",
        "visit_prep_synthesis_build",
        "authority_audit_event",
    )

    def snapshot():
        with Session(get_engine()) as database:
            return {
                table: database.execute(
                    text(f"SELECT to_jsonb(t)::text FROM public.{table} t ORDER BY id")
                )
                .scalars()
                .all()
                for table in tables
            }

    def retained_guards():
        with Session(get_engine()) as database:
            return database.execute(
                text(
                    "SELECT p.proname, pg_get_functiondef(p.oid) FROM pg_proc p "
                    "JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = 'public' AND p.proname LIKE 'cosmas_guard_visit_prep%' "
                    "ORDER BY p.proname"
                )
            ).all()

    before, guards = snapshot(), retained_guards()
    assert (
        before["visit_prep_question_response"]
        and before["visit_prep_synthesis_artifact"]
    )
    command.upgrade(Config("alembic.ini"), "0b78b19b8d6e")
    assert snapshot() == before
    current = dict(retained_guards())
    assert all(current[name] == definition for name, definition in guards)
    assert len(current) == len(guards) + 1
    with Session(get_engine()) as database:
        assert (
            database.execute(
                text("SELECT count(*) FROM public.visit_prep_booking_cancellation")
            ).scalar_one()
            == 0
        )
