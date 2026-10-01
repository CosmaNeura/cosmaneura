"""Committed PostgreSQL races, explicit guards and populated expand rehearsal."""

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event

import pytest

from app.services.visit_prep_interviews import report_wrong_recipient

from activemodel.session_manager import get_session, global_session
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_synthesis import VisitPrepSynthesisArtifact
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import select

from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.services.visit_prep_contribution_commands_test import _correct
from tests.services.visit_prep_contribution_projection_test import _prepared
from tests.services.visit_prep_synthesis_artifact_test import _artifact


def test_committed_artifact_explicit_truncate_trigger_rejects_no_pending_fk_events(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "artifact-truncate-committed")
    row = _artifact(fixture).save()
    with get_session() as session:
        session.commit()
        with pytest.raises(DBAPIError, match="synthesis artifacts cannot be truncated"):
            session.execute(text("TRUNCATE public.visit_prep_synthesis_artifact"))
        session.rollback()
        assert session.get(VisitPrepSynthesisArtifact, row.id) is not None


def test_concurrent_same_source_insert_accepts_only_one_immutable_version(monkeypatch):
    fixture = _prepared(monkeypatch, "artifact-concurrent")
    rows = [_artifact(fixture), _artifact(fixture)]
    barrier = Barrier(2)

    def insert(row):
        with get_session() as session:
            barrier.wait(timeout=5)
            session.add(row)
            session.commit()
            return row.id

    successes, failures = [], []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(insert, row) for row in rows]
        for future in futures:
            try:
                successes.append(future.result(timeout=10))
            except DBAPIError as error:
                failures.append(error)
    assert len(successes) == len(failures) == 1
    assert "version is not next" in str(failures[0])
    with get_session() as session:
        saved = session.exec(
            select(VisitPrepSynthesisArtifact).where(
                VisitPrepSynthesisArtifact.visit_prep_id == fixture.prep_id
            )
        ).all()
        assert len(saved) == 1 and saved[0].version == 1


@pytest.mark.parametrize("change", ["correction", "quarantine"])
def test_waiting_artifact_rechecks_current_basis_after_parent_lock(monkeypatch, change):
    fixture = _prepared(monkeypatch, "artifact-waiting")
    row = _artifact(fixture)
    started = Event()

    def insert():
        with get_session() as session:
            session.execute(text("SET LOCAL application_name = 'vp_synthesis_wait'"))
            started.set()
            session.add(row)
            session.commit()

    with global_session() as lock_session:
        lock_session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id).with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(insert)
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(lock_session, "vp_synthesis_wait")
                if change == "correction":
                    _correct(fixture, answer="Synthetic correction before publication.")
                else:
                    report_wrong_recipient(access_token=fixture.access_token)
            finally:
                lock_session.commit()
            with pytest.raises(
                DBAPIError, match="basis is not current|inactive Visit Prep"
            ):
                future.result(timeout=10)


def test_populated_expand_preserves_sources_and_accepts_new_artifact(monkeypatch):
    if os.environ.get("VISIT_PREP_MIGRATION_TARGET") != "fe53eb110e32":
        pytest.skip("requires explicit owned pre-expand migration-target rehearsal")
    from alembic import command
    from alembic.config import Config

    fixture = _prepared(monkeypatch, "artifact-populated-expand")
    _correct(fixture, answer="Synthetic correction predating expand.")
    table_names = (
        "visit_prep",
        "visit_prep_schedule_generation",
        "visit_prep_credential_generation",
        "visit_prep_question_response",
        "visit_prep_contribution_event",
    )

    def snapshot():
        with get_session() as session:
            snapshots = {}
            for table_name in table_names:
                # Fixed test-owned names, never user input. Compare ciphertext,
                # keyed fingerprints and every original column byte-for-byte.
                condition = "id" if table_name == "visit_prep" else "visit_prep_id"
                snapshots[table_name] = (
                    session.execute(
                        text(
                            f"SELECT to_jsonb(t)::text FROM public.{table_name} AS t WHERE {condition} = :id ORDER BY id"
                        ),
                        {"id": fixture.prep_id.uuid},
                    )
                    .scalars()
                    .all()
                )
            return snapshots

    before = snapshot()
    command.upgrade(
        Config(str(Path(__file__).resolve().parents[2] / "alembic.ini")), "1cc41ced28aa"
    )
    assert snapshot() == before
    _artifact(fixture).save()
    assert snapshot() == before
    with get_session() as session:
        assert (
            session.exec(
                select(VisitPrepSynthesisArtifact).where(
                    VisitPrepSynthesisArtifact.visit_prep_id == fixture.prep_id
                )
            )
            .one()
            .basis_event_sequence
            == 1
        )
