"""Real independent connection fences for owner publication and disclosure."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier, Event

import pytest

import app.services.visit_prep_packages as packages
import app.services.visit_prep_synthesis_publication as publication
from app.services.visit_prep_synthesis_publication import VisitPrepSynthesisStale
from app.services.visit_preps import VisitPrepNotFound

from activemodel.session_manager import get_session, global_session
from sqlalchemy import text

from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.services.visit_prep_contribution_commands_test import _correct
from tests.services.visit_prep_contribution_projection_test import _prepared
from tests.services.visit_prep_synthesis_publication_test import (
    _audits,
    _publish,
    _rows,
    _selected,
    _view,
)


def test_concurrent_domain_publication_replays_one_artifact_without_overwrite(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "publication-concurrent")
    selected = _selected(fixture)
    barrier = Barrier(2)

    def publish():
        barrier.wait(timeout=5)
        return _publish(fixture, selected)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(publish) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert {result.disposition for result in results} == {"published", "replayed"}
    assert len({result.artifact_id for result in results}) == 1
    assert len(_rows(fixture)) == 1 and len(_audits(fixture)) == 2


@pytest.mark.parametrize("operation", ["publish", "read"])
@pytest.mark.parametrize("change", ["correction", "baa"])
def test_waiting_domain_operation_rechecks_committed_sources_or_authority(
    monkeypatch, operation, change
):
    fixture = _prepared(monkeypatch, "publication-wait")
    selected = _selected(fixture)
    _publish(fixture, selected)
    initial_audits = len(_audits(fixture))
    started = Event()
    original = publication._visit_prep_session

    @contextmanager
    def tagged():
        with original() as session:
            session.execute(text("SET LOCAL application_name = 'vp_publication_wait'"))
            started.set()
            yield session

    monkeypatch.setattr(publication, "_visit_prep_session", tagged)
    with global_session() as lock_session:
        # Use the real shared lock order. Only the owning transaction is ambient;
        # the waiting operation has its own connection and refreshed ORM rows.
        packages._owner(
            lock_session,
            owner_practice_member_id=fixture.owner.id,
            owner_doctor_id=fixture.doctor.id,
            practice_id=fixture.practice.id,
            expected_clerk_subject=fixture.doctor.clerk_id,
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _publish if operation == "publish" else _view,
                *([fixture, selected] if operation == "publish" else [fixture]),
            )
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(lock_session, "vp_publication_wait")
                if change == "correction":
                    _correct(
                        fixture, answer="Synthetic correction before waiting operation."
                    )
                else:
                    fixture.practice.negotiated_baa_on_file = False
                    fixture.practice.save()
            finally:
                lock_session.commit()
            if change == "baa":
                with pytest.raises(VisitPrepNotFound):
                    future.result(timeout=10)
            elif operation == "publish":
                with pytest.raises(VisitPrepSynthesisStale):
                    future.result(timeout=10)
            else:
                result = future.result(timeout=10)
                assert result.status == "stale" and result.prepared is None
    assert len(_rows(fixture)) == 1
    assert len(_audits(fixture)) == initial_audits + (
        1 if change == "correction" and operation == "read" else 0
    )


def test_current_brief_read_blocks_correction_until_audit_and_snapshot_commit(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "publication-read-lock")
    selected = _selected(fixture)
    _publish(fixture, selected)
    loaded, release = Event(), Event()
    original_decode = publication._decoded_current

    def decode_under_lock(*args, **kwargs):
        result = original_decode(*args, **kwargs)
        loaded.set()
        assert release.wait(timeout=5)
        return result

    monkeypatch.setattr(publication, "_decoded_current", decode_under_lock)
    from app.services import visit_prep_interviews as interviews

    original_session = interviews.get_session

    @contextmanager
    def tagged_correction():
        with original_session() as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_brief_read_correction'")
            )
            yield session

    monkeypatch.setattr(interviews, "get_session", tagged_correction)
    with ThreadPoolExecutor(max_workers=2) as executor:
        reading = executor.submit(_view, fixture)
        try:
            assert loaded.wait(timeout=5)
            correcting = executor.submit(
                _correct,
                fixture,
                basis=selected.basis,
                answer="Synthetic correction after brief snapshot.",
            )
            with get_session() as observer:
                _observe_waiting_lock(observer, "vp_brief_read_correction")
            assert not correcting.done()
            release.set()
            before = reading.result(timeout=10)
            correcting.result(timeout=10)
        finally:
            release.set()
    assert (
        before.status == "ready"
        and before.package.source_version == selected.source_version
    )
    monkeypatch.setattr(publication, "_decoded_current", original_decode)
    after = _view(fixture)
    assert (
        after.status == "stale"
        and after.package.source_version != selected.source_version
    )
    assert len(_rows(fixture)) == 1
