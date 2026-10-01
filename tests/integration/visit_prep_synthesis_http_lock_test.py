"""Real provider HTTP fences on independent PostgreSQL connections."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier, Event

import pytest

from app.services import visit_prep_packages as packages
from app.services import visit_prep_synthesis_builds as builds
from app.services import visit_prep_synthesis_publication as publication
from app.services.visit_prep_synthesis_worker import run_one_build

from activemodel.session_manager import get_session, global_session
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep_synthesis_build import VisitPrepSynthesisBuild
from sqlalchemy import text
from sqlmodel import select

from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.routes.visit_prep_question_sets_test import _provider_client
from tests.routes.visit_prep_synthesis_test import _path
from tests.services.visit_prep_contribution_commands_test import _correct
from tests.services.visit_prep_contribution_projection_test import _prepared
from tests.services.visit_prep_synthesis_builds_test import _build
from tests.services.visit_prep_synthesis_publication_test import _rows, _selected
from tests.services.visit_prep_synthesis_test import _brief, _fake
from tests.services.visit_prep_synthesis_usage_test import _events


def test_concurrent_real_http_requests_queue_one_attempt_and_one_model_call(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "synthesis-http-lock-request")
    selected = _selected(fixture)
    captured = _fake(monkeypatch, _brief(selected).model_dump_json())
    barrier = Barrier(2)
    with _provider_client(fixture.doctor, monkeypatch) as client:

        def request():
            barrier.wait(timeout=5)
            return client.post(
                _path(fixture),
                json={"expected_source_version": selected.source_version},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(request) for _ in range(2)]
            responses = [future.result(timeout=10) for future in futures]
        assert all(response.status_code == 202 for response in responses)
        assert responses[0].json() == responses[1].json()
        assert _build(fixture).epoch == 1 and _build(fixture).attempts == 0
        with get_session() as session:
            assert (
                len(
                    session.exec(
                        select(VisitPrepSynthesisBuild).where(
                            VisitPrepSynthesisBuild.visit_prep_id == fixture.prep_id
                        )
                    ).all()
                )
                == 1
            )
        assert not captured and not _events(fixture)
        assert run_one_build() == "ready"
        assert run_one_build() == "idle"
        result = client.get(_path(fixture))
    assert result.status_code == 200 and result.json()["status"] == "ready"
    assert len(_rows(fixture)) == 1 and len(_events(fixture)) == 2


@pytest.mark.parametrize("change", ["correction", "baa"])
def test_request_waiting_on_real_owner_lock_rechecks_committed_state(
    monkeypatch, change
):
    fixture = _prepared(monkeypatch, "synthesis-http-lock-wait")
    selected = _selected(fixture)
    started = Event()
    original = builds._visit_prep_session

    @contextmanager
    def tagged():
        with original() as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_http_synthesis_request_wait'")
            )
            started.set()
            yield session

    monkeypatch.setattr(builds, "_visit_prep_session", tagged)
    with (
        _provider_client(fixture.doctor, monkeypatch) as client,
        global_session() as lock,
    ):
        packages._owner(
            lock,
            owner_practice_member_id=fixture.owner.id,
            owner_doctor_id=fixture.doctor.id,
            practice_id=fixture.practice.id,
            expected_clerk_subject=fixture.doctor.clerk_id,
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                client.post,
                _path(fixture),
                json={"expected_source_version": selected.source_version},
            )
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(lock, "vp_http_synthesis_request_wait")
                if change == "correction":
                    _correct(
                        fixture, answer="Synthetic correction while request waits."
                    )
                else:
                    fixture.practice.negotiated_baa_on_file = False
                    fixture.practice.save()
            finally:
                lock.commit()
            response = future.result(timeout=10)
    assert response.status_code == (409 if change == "correction" else 404)
    assert "invented" not in response.text
    with get_session() as session:
        assert not session.exec(
            select(VisitPrepSynthesisBuild).where(
                VisitPrepSynthesisBuild.visit_prep_id == fixture.prep_id
            )
        ).all()
    assert not _rows(fixture) and not _events(fixture)


def test_atomic_http_source_and_brief_read_stays_consistent_through_correction(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "synthesis-http-lock-read")
    selected = _selected(fixture)
    _fake(monkeypatch, _brief(selected).model_dump_json())
    loaded, release = Event(), Event()
    original = publication._decoded_current

    def paused(*args, **kwargs):
        result = original(*args, **kwargs)
        loaded.set()
        assert release.wait(timeout=5)
        return result

    with _provider_client(fixture.doctor, monkeypatch) as client:
        client.post(
            _path(fixture), json={"expected_source_version": selected.source_version}
        )
        assert run_one_build() == "ready"
        monkeypatch.setattr(publication, "_decoded_current", paused)
        from app.services import visit_prep_interviews as interviews

        original_session = interviews.get_session

        @contextmanager
        def correction_session():
            with original_session() as session:
                session.execute(
                    text(
                        "SET LOCAL application_name = 'vp_http_synthesis_read_correction'"
                    )
                )
                yield session

        monkeypatch.setattr(interviews, "get_session", correction_session)
        with ThreadPoolExecutor(max_workers=2) as executor:
            reading = executor.submit(client.get, _path(fixture))
            try:
                assert loaded.wait(timeout=5)
                correcting = executor.submit(
                    _correct,
                    fixture,
                    basis=selected.basis,
                    answer="Synthetic newer story after consistent read.",
                )
                with get_session() as observer:
                    _observe_waiting_lock(observer, "vp_http_synthesis_read_correction")
                assert not correcting.done()
                release.set()
                response = reading.result(timeout=10)
                correcting.result(timeout=10)
            finally:
                release.set()
        before = response.json()
        assert (
            response.status_code == 200
            and before["brief"] == _brief(selected).model_dump()
        )
        assert before["sources"]["source_version"] == selected.source_version
        assert any(
            topic["answer"] == "My invented health story."
            for topic in before["sources"]["topics"]
        )
        monkeypatch.setattr(publication, "_decoded_current", original)
        after = client.get(_path(fixture)).json()
    assert after["status"] == "stale" and after["brief"] is None
    assert after["sources"]["source_version"] != selected.source_version
    assert any(
        topic["answer"] == "Synthetic newer story after consistent read."
        for topic in after["sources"]["topics"]
    )
    with get_session() as session:
        audits = session.exec(
            select(AuthorityAuditEvent).where(
                AuthorityAuditEvent.resource_ref == str(fixture.prep_id),
                AuthorityAuditEvent.event_type == "visit_prep.synthesis_read",
            )
        ).all()
    assert {audit.request_digest for audit in audits} == {
        selected.source_version,
        after["sources"]["source_version"],
    }
