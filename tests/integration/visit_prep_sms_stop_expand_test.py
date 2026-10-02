"""Rehearse additive STOP storage over populated patient and consent evidence."""

import os

import pytest
from alembic import command
from alembic.config import Config

from app.configuration import visit_prep_sms
from app.configuration.visit_prep_sms import VisitPrepSMSRouting
from app.services import visit_prep_interviews as interviews

from activemodel.session_manager import get_engine
from sqlalchemy import text
from sqlmodel import Session

from tests.integration.visit_prep_sms_consent_test import (
    _choose,
    _payload,
    _ready,
    _view,
)
from tests.integration.visit_prep_sms_suppression_test import (
    _event,
    _record,
    _suppressed,
)
from tests.services.visit_prep_interviews_test import _respond
from tests.services.visit_prep_synthesis_publication_test import _publish


@pytest.mark.skipif(
    os.environ.get("VISIT_PREP_MIGRATION_TARGET") != "5789bd0d277e",
    reason="Explicit owned predecessor rehearsal only",
)
def test_sms_stop_expand_preserves_patient_package_consent_and_existing_guards(
    monkeypatch, owned_rate_limit_redis
):
    monkeypatch.setattr(visit_prep_sms, "ROUTING", VisitPrepSMSRouting())
    fixture, client = _ready(monkeypatch, owned_rate_limit_redis)
    assert _choose(client, _payload(_view(client).json())).status_code == 200
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
    tables = (
        "visit_prep",
        "visit_prep_schedule_generation",
        "visit_prep_credential_generation",
        "visit_prep_question_response",
        "visit_prep_contribution_event",
        "visit_prep_synthesis_artifact",
        "visit_prep_synthesis_build",
        "authority_audit_event",
        "visit_prep_sms_consent_event",
        "visit_prep_email_consent_event",
        "visit_prep_email_approval_event",
        "visit_prep_email_delivery",
        "visit_prep_booking_receipt",
        "visit_prep_booking_event",
        "visit_prep_booking_cancellation",
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
        before["visit_prep_sms_consent_event"]
        and before["visit_prep_synthesis_artifact"]
    )
    command.upgrade(Config("alembic.ini"), "79601b0047dc")
    assert snapshot() == before
    after = guards()
    assert all(after[name] == value for name, value in old_guards.items())
    assert len(after) == len(old_guards) + 1
    event = _event(VisitPrepSMSRouting("synthetic", "expand-source", "+12025550100"))
    assert _record(event).disposition == "recorded" and _suppressed(event)
