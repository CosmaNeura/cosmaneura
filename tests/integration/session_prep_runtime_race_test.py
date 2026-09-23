"""Independent connections prove claims and idempotency serialize correctly."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

from app.models.session_prep_runtime import SessionPrepArtifact, SessionPrepReadReceipt

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
