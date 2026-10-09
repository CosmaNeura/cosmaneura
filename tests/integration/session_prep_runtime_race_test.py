"""Independent connections prove claims and idempotency serialize correctly."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event

from app.models.session_prep_runtime import (
    SessionPrepArtifact,
    SessionPrepBuild,
    SessionPrepReadReceipt,
)

import tests.services.session_prep_runtime_test as runtime_tests
from tests.services.session_prep_runtime_test import _action, _brief, _read, _work

scope = runtime_tests.scope


def test_two_workers_do_not_generate_the_same_build(scope):
    entered, release = Event(), Event()

    def slow_model(selection):
        entered.set()
        assert release.wait(timeout=8)
        return _brief(selection)

    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(_work, slow_model)
        try:
            assert entered.wait(timeout=5)
            assert workers.submit(_work).result(timeout=5) == "idle"
        finally:
            release.set()
        assert first.result(timeout=5) == "ready"
    assert SessionPrepArtifact.count() == 1


def test_late_worker_cannot_replace_reclaimed_build_or_pending_receipt(
    scope, monkeypatch
):
    _doctor, _target, _note, token, record = scope
    original_action = _action(token)
    original_key = "late-worker-original-pending"
    pending = _read(token, record, original_action, key=original_key)
    assert pending.response.result.state == "pending"
    pending_payload = pending.response.model_dump(mode="json")
    entered, release = Event(), Event()
    first_time = runtime_tests.NOW
    reclaimed_time = first_time + timedelta(seconds=91)
    late_time = first_time + timedelta(seconds=92)
    first_text = "The prior reviewed note documents synthetic goals from worker A."
    replacement_text = (
        "The prior reviewed note documents synthetic goals from worker B."
    )

    def slow_model(selection):
        entered.set()
        assert release.wait(timeout=30)
        result = _brief(selection)
        result.recap.text = first_text
        return result

    def replacement_model(selection):
        assert not release.is_set(), "The original generator must still be running"
        result = _brief(selection)
        result.recap.text = replacement_text
        return result

    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(
            runtime_tests.run_one_build,
            generate=slow_model,
            clock=lambda: late_time if release.is_set() else first_time,
            settings=runtime_tests.AUTHORITY_SETTINGS,
        )
        try:
            assert entered.wait(timeout=10)
            claimed = SessionPrepBuild.one()
            assert claimed.state == "running" and claimed.attempts == 1
            assert claimed.lease_until is not None
            assert claimed.lease_until < reclaimed_time
            assert (
                workers.submit(_work, replacement_model, now=reclaimed_time).result(
                    timeout=10
                )
                == "ready"
            )
            assert not first.done(), "Recovery must finish before the old model returns"
            replacement = SessionPrepBuild.one()
            assert replacement.id == claimed.id
            assert replacement.state == "ready" and replacement.attempts == 2
            assert replacement.lease_until is None
            build_payload = replacement.model_dump(mode="json")
            assert SessionPrepArtifact.count() == 1
            artifact = SessionPrepArtifact.one()
            assert artifact.version == 1
            artifact_payload = artifact.model_dump(mode="json")
            result_payload = artifact.result_payload.copy()
        finally:
            release.set()
        assert first.result(timeout=10) == "discarded"

    monkeypatch.setattr(runtime_tests, "NOW", late_time)
    assert SessionPrepBuild.one().model_dump(mode="json") == build_payload
    assert SessionPrepArtifact.count() == 1
    retained = SessionPrepArtifact.one()
    assert retained.model_dump(mode="json") == artifact_payload
    assert retained.result_payload == result_payload
    ready = _read(token, record, _action(token), key="late-worker-fresh-ready")
    assert not ready.replayed and ready.response.result.state == "ready"
    assert ready.response.result.brief_ref == f"brief_{artifact.id}"
    assert ready.response.result.prepared_brief is not None
    assert ready.response.result.prepared_brief.recap.text == replacement_text
    replay = _read(token, record, original_action, key=original_key)
    assert replay.replayed
    assert replay.response.model_dump(mode="json") == pending_payload
    assert SessionPrepReadReceipt.count() == 2


def test_concurrent_identical_reads_share_one_durable_receipt(scope):
    _doctor, _target, _note, token, record = scope
    assert _work() == "ready"
    action, barrier = _action(token), Barrier(2)

    def read():
        barrier.wait(timeout=5)
        return _read(token, record, action)

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(read) for _ in range(2)]
        results = [future.result(timeout=8) for future in futures]
    assert sorted(result.replayed for result in results) == [False, True]
    assert results[0].response == results[1].response
    assert SessionPrepReadReceipt.count() == 1
