"""Status disclosure after actual owner/worker waits and atomic read audits."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest

from app.services.visit_prep_email_contract import VisitPrepEmailInvalid
from app.services.visit_prep_email_worker import (
    EmailSubmissionClaim,
    claim_email_invitation,
    dispatch_email_claim,
)
from app.services.visit_preps import rotate_visit_prep_credential

from activemodel.session_manager import get_session, global_session
from app.models.authority import AuthorityAuditEvent
from app.models.practice import Practice
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.services.visit_prep_delivery_model_test import _update
from tests.services.visit_prep_email_enqueue_test import _rows
from tests.services.visit_prep_email_status_test import _audits, _read
from tests.services.visit_prep_email_worker_test import _options, _queued
from tests.services.visit_preps_test import _withdraw_seat


@pytest.fixture
def queued_status():
    owned = []

    def create(*args, **kwargs):
        fixture = _queued(*args, **kwargs)
        owned.append(fixture)
        return fixture

    yield create
    # These reads deliberately leave work untouched. Close only this test's
    # invented pending rows so a later real queue worker cannot select them.
    for fixture in owned:
        for row in _rows(fixture):
            if row.state in {"queued", "submitting"}:
                claimed = row.state == "submitting"
                _update(
                    row,
                    state="uncertain" if claimed else "suppressed",
                    outcome_code="submission_uncertain"
                    if claimed
                    else "scope_unavailable",
                    payload_ciphertext=None,
                    lease_until=None,
                    finalized_at=datetime.now(UTC),
                )


@pytest.mark.parametrize("change", ["baa", "rotation", "expiry"])
def test_status_rechecks_committed_authority_lineage_and_time_after_wait(
    change, queued_status
):
    fixture = queued_status(
        "status-lock",
        expiry=datetime.now(UTC) + timedelta(seconds=2) if change == "expiry" else None,
    )
    started = Event()

    def read():
        with global_session() as session:
            session.execute(text("SET LOCAL application_name = 'vp_email_status_wait'"))
            started.set()
            return _read(fixture)

    with global_session() as parent:
        parent.exec(
            select(Practice)
            .where(Practice.id == fixture[0].practice.id)
            .with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(read)
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(parent, "vp_email_status_wait")
                if change == "baa":
                    fixture[0].practice.negotiated_baa_on_file = False
                    fixture[0].practice.save()
                elif change == "rotation":
                    scope = fixture[0]
                    rotate_visit_prep_credential(
                        owner_practice_member_id=scope.owner.id,
                        owner_doctor_id=scope.doctor.id,
                        practice_id=scope.practice.id,
                        visit_prep_id=fixture[1].id,
                        expected_credential_generation=1,
                    )
                else:
                    parent.execute(text("SELECT pg_sleep(2.1)"))
            finally:
                parent.commit()
            if change == "baa":
                with pytest.raises(VisitPrepEmailInvalid):
                    future.result(timeout=10)
            else:
                value = future.result(timeout=10)
                assert value.status == "queued"
                assert (
                    (
                        value.context_current is False
                        and value.current_credential_generation == 2
                    )
                    if change == "rotation"
                    else value.invitation_expired is True
                )


def test_read_waiting_on_real_submission_returns_committed_outcome(queued_status):
    fixture = queued_status("status-submission")
    claim = claim_email_invitation(**_options(fixture))
    assert isinstance(claim, EmailSubmissionClaim)
    called, release, started = Event(), Event(), Event()

    def transport(invitation):
        called.set()
        assert release.wait(timeout=5)
        return "accepted"

    def read():
        with global_session() as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_email_status_submission'")
            )
            started.set()
            return _read(fixture)

    with ThreadPoolExecutor(max_workers=2) as executor:
        sending = executor.submit(
            dispatch_email_claim, claim, **_options(fixture), transport=transport
        )
        assert called.wait(timeout=5)
        reading = executor.submit(read)
        try:
            assert started.wait(timeout=5)
            with get_session() as observer:
                _observe_waiting_lock(observer, "vp_email_status_submission")
            assert not reading.done()
        finally:
            release.set()
        assert sending.result(timeout=10) == "accepted"
        assert reading.result(timeout=10).status == "accepted"


def test_read_denies_seat_expiring_after_audit_and_rolls_it_back(
    monkeypatch, queued_status
):
    fixture = queued_status("status-final-expiry")
    _withdraw_seat(fixture[0].owner, ends_at=datetime.now(UTC) + timedelta(seconds=1))
    original = Session.flush

    def delay(session, *args, **kwargs):
        if any(
            isinstance(row, AuthorityAuditEvent)
            and row.event_type == "visit_prep.email_status_read"
            for row in session.new
        ):
            session.execute(text("SELECT pg_sleep(1.1)"))
        return original(session, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Session, "flush", delay)
        with pytest.raises(VisitPrepEmailInvalid):
            _read(fixture)
    assert _audits(fixture) == []


def test_lost_read_ack_preserves_audit_but_does_not_return_snapshot(
    monkeypatch, queued_status
):
    fixture = queued_status("status-lost-ack")
    original = Session.commit
    lost = False

    def lose(session):
        nonlocal lost
        original(session)
        if not lost:
            lost = True
            raise SQLAlchemyError("Synthetic lost read acknowledgment")

    with monkeypatch.context() as patch:
        patch.setattr(Session, "commit", lose)
        with pytest.raises(VisitPrepEmailInvalid):
            _read(fixture)
    assert lost and len(_audits(fixture)) == 1
    assert _read(fixture).status == "queued" and len(_audits(fixture)) == 2
