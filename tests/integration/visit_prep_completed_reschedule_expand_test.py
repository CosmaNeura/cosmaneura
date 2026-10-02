"""Populated predecessor upgrade changes guards, never retained patient evidence."""

import os
from datetime import timedelta

import pytest
from alembic import command
from alembic.config import Config

from activemodel.session_manager import get_engine
from sqlalchemy import text
from sqlmodel import Session

from tests.integration.visit_prep_booking_lifecycle_ingress_test import _current
from tests.integration.visit_prep_completed_booking_reschedule_test import _completed
from tests.services.visit_prep_model_test import _insert_rejected, _schedule
from tests.services.visit_prep_synthesis_publication_test import _publish


@pytest.mark.skipif(
    os.environ.get("VISIT_PREP_MIGRATION_TARGET") != "ac4d76856637",
    reason="Owned populated predecessor rehearsal only",
)
def test_completed_reschedule_upgrade_preserves_rows_and_prior_guard_remainder(
    monkeypatch,
):
    fixture, scope, _, _, _, _ = _completed(monkeypatch)
    _publish(fixture)
    prep, schedule, _, _ = _current(scope)
    _insert_rejected(
        _schedule(prep, schedule.scheduled_at + timedelta(days=1), 2),
        "inactive Visit Prep cannot be rescheduled",
    )
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
    functions = (
        "cosmas_guard_visit_prep_schedule",
        "cosmas_guard_visit_prep_credential",
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

    def guards():
        with Session(get_engine()) as database:
            return {
                name: database.execute(
                    text("SELECT pg_get_functiondef(CAST(:name AS regprocedure))"),
                    {"name": f"public.{name}()"},
                ).scalar_one()
                for name in functions
            }

    before, prior = snapshot(), guards()
    assert before["visit_prep_question_response"]
    assert before["visit_prep_synthesis_artifact"]
    command.upgrade(Config("alembic.ini"), "3a7854eb39e0")
    assert snapshot() == before
    for name, definition in guards().items():
        prefix, suffix = prior[name].split(
            "OR root_state IN ('completed', 'misdirected')"
        )
        assert definition.startswith(prefix) and definition.endswith(suffix)
    _schedule(prep, schedule.scheduled_at + timedelta(days=1), 2).save()
