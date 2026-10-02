"""Owned populated predecessor rehearsal; retain all existing evidence."""

import os

import pytest
from alembic import command
from alembic.config import Config

from activemodel.session_manager import get_engine
from sqlalchemy import text
from sqlmodel import Session

from tests.integration.visit_prep_completed_booking_reschedule_test import _completed
from tests.services.visit_prep_synthesis_publication_test import _publish


@pytest.mark.skipif(
    os.environ.get("VISIT_PREP_MIGRATION_TARGET") != "0b78b19b8d6e",
    reason="Explicit owned predecessor rehearsal only",
)
def test_sms_consent_expand_preserves_all_retained_patient_and_booking_evidence(
    monkeypatch,
):
    fixture, *_ = _completed(monkeypatch)
    _publish(fixture)
    tables = (
        "visit_prep",
        "visit_prep_schedule_generation",
        "visit_prep_credential_generation",
        "visit_prep_booking_receipt",
        "visit_prep_booking_event",
        "visit_prep_booking_cancellation",
        "visit_prep_question_response",
        "visit_prep_contribution_event",
        "visit_prep_synthesis_artifact",
        "visit_prep_synthesis_build",
        "authority_audit_event",
        "visit_prep_email_consent_event",
        "visit_prep_email_approval_event",
        "visit_prep_email_delivery",
    )

    def snapshot():
        with Session(get_engine()) as session:
            return {
                table: session.execute(
                    text(f"SELECT to_jsonb(t)::text FROM public.{table} t ORDER BY id")
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
    assert (
        before["visit_prep_question_response"]
        and before["visit_prep_synthesis_artifact"]
    )
    command.upgrade(Config("alembic.ini"), "5789bd0d277e")
    assert snapshot() == before
    after = guards()
    assert all(after[name] == definition for name, definition in old_guards.items())
    assert len(after) == len(old_guards) + 1
    with Session(get_engine()) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM public.visit_prep_sms_consent_event")
            ).scalar_one()
            == 0
        )
