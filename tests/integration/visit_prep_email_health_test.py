"""Independent-connection email-health snapshots, without sender installation."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.services.visit_prep_email_operations import runtime_health
from app.services.visit_prep_email_worker import (
    EmailSubmissionClaim,
    claim_email_invitation,
    dispatch_email_claim,
)

from activemodel.session_manager import global_session
from app.models.practice import Practice
from app.models.visit_prep import VisitPrep
from sqlalchemy import func, update
from sqlmodel import col, select

from tests.services.visit_prep_email_operations_test import _assert_delta, _snapshot
from tests.services.visit_prep_email_worker_test import _options, _queued


@pytest.mark.parametrize("commit", [True, False])
def test_uncommitted_revocation_does_not_leak_into_readonly_health(commit):
    fixture = _queued("health-committed-state")
    baseline = runtime_health()
    scope, root, _, _ = fixture
    with global_session() as parent:
        parent.exec(
            select(Practice).where(Practice.id == scope.practice.id).with_for_update()
        ).one()
        parent.execute(
            update(VisitPrep)
            .where(col(VisitPrep.id) == root.id)
            .values(
                revoked_at=func.clock_timestamp(),
                revoked_by_ref="actor:synthetic-health-withdrawal",
                revocation_reason="physician_revoked",
            )
        )
        # The service's private global session in another thread owns its own
        # connection; it neither waits on these row locks nor sees uncommitted
        # withdrawal. This guarded metadata update intentionally leaves the
        # transaction open; the public revocation command commits its work.
        with ThreadPoolExecutor(max_workers=1) as executor:
            observed = executor.submit(runtime_health).result(timeout=5)
        _assert_delta(baseline, observed)
        parent.commit() if commit else parent.rollback()
    _assert_delta(baseline, runtime_health(), obsolete_queued=1 if commit else 0)


def test_health_reads_committed_submission_while_transport_holds_owner_fences():
    fixture = _queued("health-during-submission")
    baseline = runtime_health()
    claim = claim_email_invitation(**_options(fixture))
    assert isinstance(claim, EmailSubmissionClaim)
    called, release = Event(), Event()
    calls = []

    def transport(invitation):
        calls.append(invitation.operation_id)
        called.set()
        assert release.wait(timeout=8)
        return "accepted"

    with ThreadPoolExecutor(max_workers=2) as executor:
        submitting = executor.submit(
            dispatch_email_claim, claim, **_options(fixture), transport=transport
        )
        try:
            assert called.wait(timeout=5)
            observed = executor.submit(runtime_health).result(timeout=5)
            _assert_delta(baseline, observed, queued=-1, submitting=1)
        finally:
            release.set()
        assert submitting.result(timeout=5) == "accepted"
    _assert_delta(baseline, runtime_health(), queued=-1, accepted=1)
    saved = _snapshot(fixture)
    _assert_delta(baseline, runtime_health(), queued=-1, accepted=1)
    assert _snapshot(fixture) == saved and calls == [fixture[3]["operation_id"]]
