"""Independent PostgreSQL connections qualify durable claim and source fences."""

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier, Event

import pytest

import app.services.visit_prep_packages as packages
import app.services.visit_prep_synthesis_builds as builds
from app.services.visit_prep_synthesis_builds import VisitPrepBuildDiscarded

from activemodel.session_manager import get_session, global_session
from app.models.visit_prep_synthesis_build import VisitPrepSynthesisBuild
from sqlalchemy import text
from sqlmodel import select

from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.services.visit_prep_contribution_commands_test import _correct
from tests.services.visit_prep_contribution_projection_test import _prepared
from tests.services.visit_prep_synthesis_builds_test import _audits, _build, _request
from tests.services.visit_prep_synthesis_publication_test import _publish, _selected


def test_concurrent_requests_and_claims_create_one_job_and_one_live_lease(monkeypatch):
    fixture = _prepared(monkeypatch, "build-race")
    selected = _selected(fixture)
    barrier = Barrier(2)

    def request():
        barrier.wait(timeout=5)
        return _request(fixture, selected.source_version)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(request) for _ in range(2)]
        receipts = [future.result(timeout=10) for future in futures]
    assert receipts[0] == receipts[1]
    barrier = Barrier(2)

    def claim():
        barrier.wait(timeout=5)
        return builds.claim_visit_prep_synthesis()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(claim) for _ in range(2)]
        claims = [future.result(timeout=10) for future in futures]
    assert sum(claim is not None for claim in claims) == 1
    assert _build(fixture).attempts == 1 and _build(fixture).state == "running"
    live = next(claim for claim in claims if claim is not None)
    assert builds.fail_visit_prep_synthesis_claim(live, code="scope_unavailable")


@pytest.mark.parametrize("change", ["correction", "baa"])
def test_claimed_source_read_rechecks_after_real_owner_lock_wait(monkeypatch, change):
    fixture = _prepared(monkeypatch, "build-owner-wait")
    _request(fixture)
    claim = builds.claim_visit_prep_synthesis()
    started = Event()
    original = builds._visit_prep_session

    @contextmanager
    def tagged():
        with original() as session:
            session.execute(text("SET LOCAL application_name = 'vp_build_owner_wait'"))
            started.set()
            yield session

    monkeypatch.setattr(builds, "_visit_prep_session", tagged)
    with global_session() as session:
        packages._owner(
            session,
            owner_practice_member_id=fixture.owner.id,
            owner_doctor_id=fixture.doctor.id,
            practice_id=fixture.practice.id,
            expected_clerk_subject=fixture.doctor.clerk_id,
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(builds.load_claimed_synthesis_sources, claim)
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(session, "vp_build_owner_wait")
                if change == "correction":
                    _correct(fixture, answer="Synthetic revision during lock wait.")
                else:
                    fixture.practice.negotiated_baa_on_file = False
                    fixture.practice.save()
            finally:
                session.commit()
            with pytest.raises(VisitPrepBuildDiscarded):
                future.result(timeout=10)
    assert _build(fixture).state == ("pending" if change == "correction" else "failed")
    assert len(_audits(fixture)) == (2 if change == "correction" else 1)
    if change == "correction":
        # This integration runner intentionally preserves committed synthetic
        # rows. End only this test's requeued job before the next global poll.
        recovered = builds.claim_visit_prep_synthesis()
        assert recovered.visit_prep_id == fixture.prep_id
        assert builds.fail_visit_prep_synthesis_claim(
            recovered, code="scope_unavailable"
        )


def test_claim_skips_locked_job_without_waiting_or_incrementing_attempts(monkeypatch):
    fixture = _prepared(monkeypatch, "build-skip-locked")
    receipt = _request(fixture)
    with global_session() as session:
        session.exec(
            select(VisitPrepSynthesisBuild)
            .where(VisitPrepSynthesisBuild.id == receipt.build_id)
            .with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert (
                executor.submit(builds.claim_visit_prep_synthesis).result(timeout=5)
                is None
            )
    assert _build(fixture).attempts == 0
    claim = builds.claim_visit_prep_synthesis()
    assert claim.attempt == 1 and claim.visit_prep_id == fixture.prep_id
    assert builds.fail_visit_prep_synthesis_claim(claim, code="scope_unavailable")


def test_populated_build_expand_preserves_all_sources_and_immutable_artifact(
    monkeypatch,
):
    if os.environ.get("VISIT_PREP_MIGRATION_TARGET") != "1cc41ced28aa":
        pytest.skip("requires explicit owned pre-build migration-target rehearsal")
    from alembic import command
    from alembic.config import Config

    fixture = _prepared(monkeypatch, "build-populated-expand")
    _correct(fixture, answer="Synthetic correction predating build expand.")
    _publish(fixture)
    table_names = (
        "visit_prep",
        "visit_prep_schedule_generation",
        "visit_prep_credential_generation",
        "visit_prep_question_response",
        "visit_prep_contribution_event",
        "visit_prep_synthesis_artifact",
    )

    def snapshot():
        with get_session() as session:
            snapshots = {}
            for name in table_names:
                # Fixed test-owned table names. Compare every column, ciphertext
                # and keyed fingerprint; no export or shared database is used.
                column = "id" if name == "visit_prep" else "visit_prep_id"
                snapshots[name] = (
                    session.execute(
                        text(
                            f"SELECT to_jsonb(t)::text FROM public.{name} AS t WHERE {column} = :id ORDER BY id"
                        ),
                        {"id": fixture.prep_id.uuid},
                    )
                    .scalars()
                    .all()
                )
            return snapshots

    before = snapshot()
    command.upgrade(
        Config(str(Path(__file__).resolve().parents[2] / "alembic.ini")), "2f87498bb9fa"
    )
    assert snapshot() == before
    assert _request(fixture).state == "ready"
    assert snapshot() == before
