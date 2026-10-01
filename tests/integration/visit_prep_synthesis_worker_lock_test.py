"""Independent PostgreSQL connections plus local synthetic model transport."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier, Event

import pytest

import app.services.visit_prep_synthesis_worker as worker
from app.services.visit_prep_synthesis_builds import (
    VisitPrepBuildDiscarded,
    fail_visit_prep_synthesis_claim,
)
from app.services.visit_prep_synthesis_worker import (
    publish_claimed_synthesis,
    run_one_build,
)

from activemodel.session_manager import get_session
from sqlalchemy import text

from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.services.visit_prep_contribution_commands_test import _correct
from tests.services.visit_prep_contribution_projection_test import _prepared
from tests.services.visit_prep_synthesis_builds_test import _build, _request
from tests.services.visit_prep_synthesis_publication_test import (
    _audits,
    _rows,
    _selected,
)
from tests.services.visit_prep_synthesis_test import _brief, _fake
from tests.services.visit_prep_synthesis_usage_test import _events
from tests.services.visit_prep_synthesis_worker_test import _accounted


def test_model_work_holds_no_owner_or_patient_locks_and_later_sources_are_requeued(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "worker-free-locks")
    selected = _selected(fixture)
    _request(fixture)
    _fake(monkeypatch, _brief(selected).model_dump_json())
    started, release = Event(), Event()
    original = worker.generate_patient_brief

    def paused_model(selection, **kwargs):
        # Observe intent from another connection before the first SDK call.
        events = _events(fixture)
        assert len(events) == 1 and events[0].batch_phase == "intent"
        started.set()
        assert release.wait(timeout=5)
        return original(selection, **kwargs)

    monkeypatch.setattr(worker, "generate_patient_brief", paused_model)
    with ThreadPoolExecutor(max_workers=2) as executor:
        generating = executor.submit(run_one_build)
        try:
            assert started.wait(timeout=5)
            correcting = executor.submit(
                _correct,
                fixture,
                basis=selected.basis,
                answer="Synthetic correction during model work.",
            )
            # It must finish before releasing the model. A transaction held
            # across the model call would instead block this real write.
            correcting.result(timeout=3)
            release.set()
            assert generating.result(timeout=10) == "superseded"
        finally:
            release.set()
    assert len(_events(fixture)) == 2 and not _rows(fixture)
    assert _build(fixture).state == "pending" and _build(fixture).epoch == 2
    # End only this synthetic job; later queue polls must not adopt it.
    from app.services.visit_prep_synthesis_builds import claim_visit_prep_synthesis

    claim = claim_visit_prep_synthesis()
    assert claim.visit_prep_id == fixture.prep_id
    assert fail_visit_prep_synthesis_claim(claim, code="scope_unavailable")


def test_two_workers_on_one_claim_publish_one_artifact_and_one_ready_transition(
    monkeypatch,
):
    fixture, claim, selected, intent_id = _accounted(monkeypatch, "worker-competing")
    barrier = Barrier(2)

    def publish():
        barrier.wait(timeout=5)
        try:
            return publish_claimed_synthesis(
                claim, selected, _brief(selected), intent_id=intent_id
            )
        except VisitPrepBuildDiscarded:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(publish) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert sum(result is not None for result in results) == 1
    assert len(_rows(fixture)) == 1 and len(_audits(fixture)) == 1
    assert _build(fixture).state == "ready" and len(_events(fixture)) == 2


def test_atomic_publication_blocks_correction_until_artifact_job_and_audit_commit(
    monkeypatch,
):
    fixture, claim, selected, intent_id = _accounted(monkeypatch, "worker-atomic-lock")
    staged, release = Event(), Event()
    original = worker._publish_locked

    def paused_publication(*args):
        result = original(*args)
        staged.set()
        assert release.wait(timeout=5)
        return result

    monkeypatch.setattr(worker, "_publish_locked", paused_publication)
    from app.services import visit_prep_interviews as interviews

    original_session = interviews.get_session

    @contextmanager
    def tagged():
        with original_session() as session:
            session.execute(text("SET LOCAL application_name = 'vp_worker_correction'"))
            yield session

    monkeypatch.setattr(interviews, "get_session", tagged)
    with ThreadPoolExecutor(max_workers=2) as executor:
        publishing = executor.submit(
            publish_claimed_synthesis,
            claim,
            selected,
            _brief(selected),
            intent_id=intent_id,
        )
        try:
            assert staged.wait(timeout=5)
            correcting = executor.submit(
                _correct,
                fixture,
                basis=selected.basis,
                answer="Synthetic correction after atomic publication.",
            )
            with get_session() as observer:
                _observe_waiting_lock(observer, "vp_worker_correction")
                # Other connections cannot see the staged artifact or ready job.
                assert not _rows(fixture) and _build(fixture).state == "running"
            assert not correcting.done()
            release.set()
            assert publishing.result(timeout=10).disposition == "published"
            correcting.result(timeout=10)
        finally:
            release.set()
    assert len(_rows(fixture)) == 1 and len(_audits(fixture)) == 1
    assert _build(fixture).state == "ready"
    assert _selected(fixture).source_version != selected.source_version
    # The ready artifact is stale after the later correction, not rebased.
    from tests.services.visit_prep_synthesis_publication_test import _view

    assert _view(fixture).status == "stale"


def test_lost_publication_acknowledgment_cannot_generate_or_publish_again(monkeypatch):
    fixture = _prepared(monkeypatch, "worker-lost-ack")
    selected = _selected(fixture)
    _fake(monkeypatch, _brief(selected).model_dump_json())
    _request(fixture)
    original = worker._commit

    def commit_then_lose_ack(session):
        original(session)
        raise RuntimeError("Synthetic commit acknowledgment lost")

    monkeypatch.setattr(worker, "_commit", commit_then_lose_ack)
    with pytest.raises(RuntimeError, match="acknowledgment lost"):
        run_one_build()
    assert _build(fixture).state == "ready"
    assert len(_rows(fixture)) == len(_audits(fixture)) == 1
    assert len(_events(fixture)) == 2
    monkeypatch.setattr(worker, "_commit", original)
    assert run_one_build() == "idle" and _request(fixture).state == "ready"
    assert len(_events(fixture)) == 2 and len(_rows(fixture)) == 1
