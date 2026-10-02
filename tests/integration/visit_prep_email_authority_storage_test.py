"""Committed authority-stream races and populated, owned expand rehearsal."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event

import pytest

from app.services.visit_prep_synthesis_worker import run_one_build

from activemodel.session_manager import get_session, global_session
from app.models.practice import Practice
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_email_authority import VisitPrepEmailConsentEvent
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import select

from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.services.visit_prep_contribution_projection_test import _prepared
from tests.services.visit_prep_email_authority_model_test import _approval, _consent
from tests.services.visit_prep_email_payload_test import _stored_fixture
from tests.services.visit_prep_model_test import _change
from tests.services.visit_prep_synthesis_builds_test import _request
from tests.services.visit_prep_synthesis_publication_test import _selected
from tests.services.visit_prep_synthesis_test import _brief, _fake


@pytest.mark.parametrize("factory", [_approval, _consent])
def test_concurrent_first_append_serializes_one_current_sequence(factory):
    first, root = factory()
    second, _ = factory(root)
    barrier = Barrier(2)

    def append(row):
        with get_session() as session:
            barrier.wait(timeout=5)
            try:
                session.add(row)
                session.flush()
                session.commit()
                return "recorded"
            except DBAPIError as error:
                session.rollback()
                assert "sequence is not current" in str(error.orig)
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(append, row) for row in (first, second)]
        assert sorted(future.result(timeout=10) for future in futures) == [
            "conflict",
            "recorded",
        ]
    with get_session() as session:
        model = type(first)
        scope = (
            model.owner_practice_member_id == root.owner_practice_member_id
            if factory is _approval
            else model.visit_prep_id == root.id
        )
        stored = session.exec(select(model).where(scope)).all()
        assert len(stored) == 1 and stored[0].event_sequence == 1


def test_waiting_consent_grant_rechecks_root_revocation_after_lock():
    row, root = _consent()
    started = Event()

    def append():
        with get_session() as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_email_consent_wait'")
            )
            started.set()
            session.add(row)
            session.flush()
            session.commit()

    with global_session() as parent:
        parent.exec(
            select(VisitPrep).where(VisitPrep.id == root.id).with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(append)
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(parent, "vp_email_consent_wait")
                _change(
                    root,
                    revoked_at=datetime.now(UTC),
                    revoked_by_ref="actor:synthetic",
                    revocation_reason="wrong_recipient",
                )
            finally:
                parent.commit()
            with pytest.raises(DBAPIError, match="inactive or stale"):
                future.result(timeout=10)
    with get_session() as session:
        assert (
            session.exec(
                select(VisitPrepEmailConsentEvent).where(
                    VisitPrepEmailConsentEvent.visit_prep_id == root.id
                )
            ).all()
            == []
        )


@pytest.mark.parametrize("factory", [_approval, _consent])
def test_waiting_grant_checks_expiry_using_database_time_after_lock(factory):
    row, root = factory(expires_at=datetime.now(UTC) + timedelta(seconds=1))
    started = Event()

    def append():
        with get_session() as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_email_authority_expiry'")
            )
            started.set()
            session.add(row)
            session.flush()
            session.commit()

    with global_session() as parent:
        scope = (
            select(Practice).where(Practice.id == root.practice_id)
            if factory is _approval
            else select(VisitPrep).where(VisitPrep.id == root.id)
        )
        parent.exec(scope.with_for_update()).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(append)
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(parent, "vp_email_authority_expiry")
                # Actual owned PostgreSQL wait; no test clock, changed guard,
                # disabled trigger or fabricated past lease is used.
                parent.execute(text("SELECT pg_sleep(1.1)"))
                assert parent.execute(
                    text("SELECT clock_timestamp() >= :expiry"),
                    {"expiry": row.expires_at},
                ).scalar_one()
            finally:
                parent.commit()
            with pytest.raises(DBAPIError, match="expired|inactive or stale"):
                future.result(timeout=10)


@pytest.mark.parametrize("factory", [_approval, _consent])
def test_committed_named_truncate_guard_has_no_pending_fk_shortcut(factory):
    row, _ = factory()
    row.save()
    table = (
        "visit_prep_email_approval_event"
        if factory is _approval
        else "visit_prep_email_consent_event"
    )
    with get_session() as session:
        session.commit()
        with pytest.raises(DBAPIError, match="authority evidence cannot be truncated"):
            session.execute(text(f"TRUNCATE public.{table}"))
        session.rollback()
        assert session.get(type(row), row.id) is not None


def test_populated_authority_expand_preserves_existing_email_patient_and_synthesis(
    monkeypatch,
):
    if os.environ.get("VISIT_PREP_MIGRATION_TARGET") != "ed9dec654c8c":
        pytest.skip("requires explicit owned pre-expand migration-target rehearsal")
    from alembic import command
    from alembic.config import Config

    fixture = _prepared(monkeypatch, "email-authority-populated-expand")
    selected = _selected(fixture)
    _fake(monkeypatch, _brief(selected).model_dump_json())
    _request(fixture)
    assert run_one_build() == "ready"
    row, root, _, _ = _stored_fixture()
    tables = (
        "visit_prep",
        "visit_prep_schedule_generation",
        "visit_prep_credential_generation",
        "visit_prep_question_response",
        "visit_prep_contribution_event",
        "visit_prep_synthesis_artifact",
        "visit_prep_synthesis_build",
        "agent_model_usage_event",
        "visit_prep_email_delivery",
    )

    def snapshot():
        with get_session() as session:
            # Fixed test-owned identifiers; snapshot every column, not only IDs.
            return {
                name: session.execute(
                    text(f"SELECT to_jsonb(t)::text FROM public.{name} t ORDER BY id")
                )
                .scalars()
                .all()
                for name in tables
            }

    before = snapshot()
    command.upgrade(Config("alembic.ini"), "10b7993b911d")
    assert snapshot() == before
    approval, _ = _approval(root)
    consent, _ = _consent(root)
    approval.save()
    consent.save()
    assert _selected(fixture) == selected
    with get_session() as session:
        assert (
            session.get(type(row), row.id).payload_ciphertext == row.payload_ciphertext
        )
