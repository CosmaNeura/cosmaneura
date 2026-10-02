"""Owned integration boundaries retain evidence and isolate global workers."""

from datetime import UTC, datetime

import pytest

from app.services.visit_prep_email_enqueue import enqueue_email_invitation
from app.services.visit_prep_email_worker import (
    EmailSubmissionClaim,
    claim_email_invitation,
)

from sqlmodel import Session

from tests.services.visit_prep_delivery_model_test import _update
from tests.services.visit_prep_email_enqueue_test import _ready, _rows
from tests.services.visit_prep_email_worker_test import _options
from tests.visit_prep.isolated_plugin import owned_email_test_boundary


def _queued(label):
    fixture = _ready(label)
    enqueue_email_invitation(**fixture[3])
    return fixture


def test_boundary_settles_only_new_pending_rows_without_deleting_evidence():
    existing = _queued("isolation-prior")
    prior = _rows(existing)[0]
    before = prior.model_dump()
    with owned_email_test_boundary():
        queued = _queued("isolation-new")
        assert _rows(queued)[0].state == "queued"
    after = _rows(queued)[0]
    assert _rows(existing)[0].model_dump() == before
    assert _rows(existing)[0].payload_ciphertext == prior.payload_ciphertext
    assert after.state == "suppressed" and after.outcome_code == "scope_unavailable"
    assert after.payload_ciphertext is None and after.lease_until is None
    assert after.finalized_at is not None
    assert after.authorization_ciphertext is not None
    assert str(after.operation_id) == queued[3]["operation_id"]
    assert len(_rows(queued)) == 1


def test_boundary_marks_new_claim_uncertain_and_preserves_submission_clock():
    with owned_email_test_boundary():
        fixture = _queued("isolation-claimed")
        claim = claim_email_invitation(**_options(fixture))
        assert isinstance(claim, EmailSubmissionClaim)
        before = _rows(fixture)[0]
    after = _rows(fixture)[0]
    assert after.state == "uncertain" and after.outcome_code == "submission_uncertain"
    assert after.submitted_at == before.submitted_at == claim.submitted_at
    assert after.payload_ciphertext is None and after.lease_until is None
    assert after.finalized_at is not None and after.authorization_ciphertext is not None
    assert len(_rows(fixture)) == 1


def test_boundary_preserves_terminal_outcome_on_exception():
    pending = terminal = before = None
    with pytest.raises(RuntimeError, match="synthetic test failure"):
        with owned_email_test_boundary():
            pending = _queued("isolation-failed-test")
            terminal = _queued("isolation-terminal")
            before = _update(
                _rows(terminal)[0],
                state="failed",
                outcome_code="message_invalid",
                payload_ciphertext=None,
                lease_until=None,
                finalized_at=datetime.now(UTC),
            )
            raise RuntimeError("synthetic test failure")
    assert pending is not None and terminal is not None and before is not None
    assert _rows(pending)[0].state == "suppressed"
    assert _rows(terminal)[0].model_dump() == before.model_dump()


def test_boundary_refuses_unowned_database_before_any_cleanup(monkeypatch):
    fixture = _queued("isolation-guard")
    before = _rows(fixture)[0]
    with monkeypatch.context() as patch:
        patch.delenv("VISIT_PREP_OWNED_TEST_DIRECTORY")
        with pytest.raises(KeyError):
            with owned_email_test_boundary():
                raise AssertionError("an unowned boundary must never be entered")
    after = _rows(fixture)[0]
    assert after.model_dump() == before.model_dump()
    assert after.payload_ciphertext == before.payload_ciphertext


def test_connection_probe_mismatch_denies_boundary_without_touching_rows(monkeypatch):
    fixture = _queued("isolation-connection-probe")
    before = _rows(fixture)[0]
    original = Session.execute

    class OtherConnection:
        def one(self):
            return ("unowned", "unowned", "/unowned")

    def other(session, statement, *args, **kwargs):
        if str(statement).startswith("SELECT current_database(), current_user"):
            return OtherConnection()
        return original(session, statement, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Session, "execute", other)
        with pytest.raises(RuntimeError, match="owned test connection"):
            with owned_email_test_boundary():
                raise AssertionError("a mismatched connection must never be used")
    after = _rows(fixture)[0]
    assert after.model_dump() == before.model_dump()
    assert after.payload_ciphertext == before.payload_ciphertext
