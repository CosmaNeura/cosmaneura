"""Committed outbox races and populated synthetic expand rehearsal."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event

import pytest

from app.services.visit_prep_synthesis_worker import run_one_build

from activemodel.session_manager import get_session, global_session
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_delivery import VisitPrepEmailDelivery
from sqlalchemy import text, update
from sqlalchemy.exc import DBAPIError
from sqlmodel import select

from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.services.visit_prep_contribution_projection_test import _prepared
from tests.services.visit_prep_delivery_model_test import _delivery, _update
from tests.services.visit_prep_model_test import _change
from tests.services.visit_prep_synthesis_builds_test import _request
from tests.services.visit_prep_synthesis_publication_test import _selected
from tests.services.visit_prep_synthesis_test import _brief, _fake


def test_committed_outbox_named_truncate_guard_rejects_without_pending_fks():
    row, _, _, _ = _delivery()
    row.save()
    with get_session() as session:
        session.commit()
        with pytest.raises(DBAPIError, match="outbox cannot be truncated"):
            session.execute(text("TRUNCATE public.visit_prep_email_delivery"))
        session.rollback()
        assert session.get(VisitPrepEmailDelivery, row.id) is not None


def test_concurrent_submission_state_transition_allows_one_claim_only():
    row, _, _, _ = _delivery()
    row.save()
    barrier = Barrier(2)

    def claim():
        now = datetime.now(UTC)
        with get_session() as session:
            barrier.wait(timeout=5)
            claimed = session.execute(
                update(VisitPrepEmailDelivery)
                .where(
                    VisitPrepEmailDelivery.id == row.id,
                    VisitPrepEmailDelivery.state == "queued",
                )
                .values(
                    state="submitting",
                    submitted_at=now,
                    lease_until=now + timedelta(seconds=90),
                )
                .returning(VisitPrepEmailDelivery.id)
            ).scalar_one_or_none()
            session.commit()
            return claimed

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(claim) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert results.count(None) == 1
    assert next(result for result in results if result is not None) == row.id
    _update(
        row,
        state="uncertain",
        outcome_code="submission_uncertain",
        payload_ciphertext=None,
        lease_until=None,
        finalized_at=datetime.now(UTC),
    )


def test_waiting_submission_rechecks_revocation_after_parent_lock():
    row, root, _, _ = _delivery()
    row.save()
    started = Event()

    def claim():
        now = datetime.now(UTC)
        with get_session() as session:
            session.execute(text("SET LOCAL application_name = 'vp_email_wait'"))
            started.set()
            session.execute(
                update(VisitPrepEmailDelivery)
                .where(VisitPrepEmailDelivery.id == row.id)
                .values(
                    state="submitting",
                    submitted_at=now,
                    lease_until=now + timedelta(seconds=90),
                )
            )
            session.commit()

    with global_session() as parent:
        parent.exec(
            select(VisitPrep).where(VisitPrep.id == root.id).with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(claim)
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(parent, "vp_email_wait")
                _change(
                    root,
                    revoked_at=datetime.now(UTC),
                    revoked_by_ref="actor:synthetic",
                    revocation_reason="physician_revoked",
                )
            finally:
                parent.commit()
            with pytest.raises(DBAPIError, match="preparation is inactive"):
                future.result(timeout=10)
    with get_session() as session:
        assert session.get(VisitPrepEmailDelivery, row.id).state == "queued"


def test_populated_email_expand_keeps_patient_synthesis_and_central_usage(monkeypatch):
    if os.environ.get("VISIT_PREP_MIGRATION_TARGET") != "54bedaad381a":
        pytest.skip("requires explicit owned pre-expand migration-target rehearsal")
    from alembic import command
    from alembic.config import Config

    fixture = _prepared(monkeypatch, "email-populated-expand")
    selected = _selected(fixture)
    _fake(monkeypatch, _brief(selected).model_dump_json())
    _request(fixture)
    assert run_one_build() == "ready"
    tables = (
        "visit_prep",
        "visit_prep_schedule_generation",
        "visit_prep_credential_generation",
        "visit_prep_question_response",
        "visit_prep_contribution_event",
        "visit_prep_synthesis_artifact",
        "visit_prep_synthesis_build",
        "agent_model_usage_event",
    )

    def snapshot():
        with get_session() as session:
            result = {}
            for table_name in tables:
                # These are fixed test-owned table names, never request input.
                result[table_name] = (
                    session.execute(
                        text(
                            f"SELECT to_jsonb(t)::text FROM public.{table_name} t ORDER BY id"
                        )
                    )
                    .scalars()
                    .all()
                )
            return result

    before = snapshot()
    command.upgrade(
        Config(str(Path(__file__).resolve().parents[2] / "alembic.ini")), "ed9dec654c8c"
    )
    assert snapshot() == before
    row, _, _, _ = _delivery()
    row.save()
    with get_session() as session:
        assert session.get(VisitPrepEmailDelivery, row.id).state == "queued"
    assert _selected(fixture) == selected
