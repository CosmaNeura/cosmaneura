"""Caller-owned package reads: real commits, observers and correction locks.

This qualifies the reusable Core read, not a mounted companion receiver. Every
owner and answer is invented and the cluster belongs to the synthetic runner.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest

import app.services.visit_prep_synthesis_publication as publication
from app.services.visit_prep_synthesis_publication import (
    read_visit_prep_synthesis_in_session,
)
from app.services.visit_preps import VisitPrepNotFound

from activemodel.session_manager import get_session, global_session
from app.models.authority import AuthorityAuditEvent
from sqlalchemy import func, text
from sqlmodel import select

from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.services.visit_prep_contribution_commands_test import _correct
from tests.services.visit_prep_contribution_projection_test import _prepared
from tests.services.visit_prep_packages_test import _scope
from tests.services.visit_prep_synthesis_publication_test import _publish, _view


def _staged(session, fixture):
    return read_visit_prep_synthesis_in_session(
        session, **_scope(fixture), visit_prep_id=fixture.prep_id
    )


def _audit_count(session, fixture):
    return session.exec(
        select(func.count())
        .select_from(AuthorityAuditEvent)
        .where(
            AuthorityAuditEvent.resource_ref == str(fixture.prep_id),
            AuthorityAuditEvent.event_type == "visit_prep.synthesis_read",
        )
    ).one()


@pytest.mark.parametrize("role,complete", [("patient", True), ("caregiver", False)])
def test_staged_read_matches_dashboard_and_audit_waits_for_caller_commit(
    monkeypatch, role, complete
):
    fixture = _prepared(monkeypatch, "transaction-parity", role=role, complete=complete)
    _publish(fixture)
    dashboard = _view(fixture)
    with get_session() as owner, get_session() as observer:
        baseline = _audit_count(observer, fixture)
        with monkeypatch.context() as patch:
            patch.setattr(
                publication,
                "_visit_prep_session",
                lambda: pytest.fail("staged read opened another session"),
            )
            for method in ("commit", "rollback"):
                patch.setattr(
                    owner,
                    method,
                    lambda: pytest.fail("staged read ended the caller transaction"),
                )
            staged = _staged(owner, fixture)
        assert staged == dashboard
        assert staged.package.preparation.patient_id is None
        assert staged.package.preparation.appointment_id is None
        assert owner.in_transaction()
        assert _audit_count(owner, fixture) == baseline + 1
        assert _audit_count(observer, fixture) == baseline
        owner.commit()
        assert _audit_count(observer, fixture) == baseline + 1


@pytest.mark.parametrize("failure", ["denial", "commit_acknowledgment"])
def test_enclosing_failure_discloses_nothing_and_never_commits_early(
    monkeypatch, failure
):
    fixture = _prepared(monkeypatch, "transaction-failure")
    _publish(fixture)
    disclosed = []
    with get_session() as observer:
        baseline = _audit_count(observer, fixture)

    def finish(owner):
        if failure == "denial":
            raise VisitPrepNotFound("Visit Prep is unavailable")
        owner.commit()
        raise RuntimeError("synthetic lost enclosing commit acknowledgment")

    def receiver_boundary():
        # A receiver must stage its own authority/audit and disclose only after
        # an acknowledged commit. This exercise does not grant bot permission.
        with get_session() as owner:
            staged = _staged(owner, fixture)
            with get_session() as observer:
                assert _audit_count(observer, fixture) == baseline
            finish(owner)
            disclosed.append(staged)

    expected = VisitPrepNotFound if failure == "denial" else RuntimeError
    with pytest.raises(expected):
        receiver_boundary()
    assert not disclosed
    with get_session() as observer:
        assert _audit_count(observer, fixture) == baseline + (
            1 if failure == "commit_acknowledgment" else 0
        )


def test_staged_read_rechecks_access_after_audit_and_caller_rolls_back(monkeypatch):
    fixture = _prepared(monkeypatch, "transaction-final-expiry")
    _publish(fixture)
    future = datetime.now(UTC) + timedelta(days=1)
    fixture.practice.billing_exempt_until = future - timedelta(seconds=1)
    fixture.practice.save()
    original = publication._audit

    def audit_then_expire(*args, **kwargs):
        original(*args, **kwargs)
        monkeypatch.setattr(publication, "_database_now", lambda _session: future)

    monkeypatch.setattr(publication, "_audit", audit_then_expire)
    with get_session() as owner, get_session() as observer:
        baseline = _audit_count(observer, fixture)
        with pytest.raises(VisitPrepNotFound):
            _staged(owner, fixture)
        assert owner.in_transaction()
        assert _audit_count(owner, fixture) == baseline + 1
        assert _audit_count(observer, fixture) == baseline
        owner.rollback()
        assert _audit_count(observer, fixture) == baseline


def test_correction_waits_until_enclosing_transaction_finishes(monkeypatch):
    fixture = _prepared(monkeypatch, "transaction-correction-wait")
    _publish(fixture)
    from app.services import visit_prep_interviews as interviews

    original = interviews.get_session

    @contextmanager
    def tagged_correction():
        with original() as session:
            session.execute(text("SET LOCAL application_name = 'vp_staged_correction'"))
            yield session

    monkeypatch.setattr(interviews, "get_session", tagged_correction)
    with get_session() as owner:
        staged = _staged(owner, fixture)
        with ThreadPoolExecutor(max_workers=1) as executor:
            correcting = executor.submit(
                _correct,
                fixture,
                basis=staged.package.contribution.basis,
                answer="Synthetic correction after the enclosing read transaction.",
            )
            try:
                with get_session() as observer:
                    _observe_waiting_lock(observer, "vp_staged_correction")
                assert not correcting.done()
            finally:
                owner.commit()
            correcting.result(timeout=10)
    monkeypatch.setattr(
        publication,
        "decrypt_synthesis_payload",
        lambda *_args: pytest.fail("stale payload was decrypted"),
    )
    with get_session() as owner:
        after = _staged(owner, fixture)
        assert after.status == "stale" and after.prepared is None
        assert after.artifact_id is None and after.version is None
        assert after.package.source_version != staged.package.source_version
        owner.rollback()


@pytest.mark.parametrize("change", ["baa", "correction"])
def test_staged_read_reacquires_context_after_real_owner_lock_wait(monkeypatch, change):
    fixture = _prepared(monkeypatch, "transaction-owner-wait")
    _publish(fixture)
    started = Event()
    from app.services import visit_prep_packages as packages

    def reading():
        with get_session() as owner:
            owner.execute(text("SET LOCAL application_name = 'vp_staged_owner_wait'"))
            started.set()
            staged = _staged(owner, fixture)
            owner.commit()
            return staged

    with global_session() as holding:
        packages._owner(
            holding,
            owner_practice_member_id=fixture.owner.id,
            owner_doctor_id=fixture.doctor.id,
            practice_id=fixture.practice.id,
            expected_clerk_subject=fixture.doctor.clerk_id,
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            waiting = executor.submit(reading)
            try:
                assert started.wait(timeout=5)
                with get_session() as observer:
                    baseline = _audit_count(observer, fixture)
                    _observe_waiting_lock(observer, "vp_staged_owner_wait")
                if change == "baa":
                    fixture.practice.negotiated_baa_on_file = False
                    fixture.practice.save()
                else:
                    _correct(fixture, answer="Synthetic wording before staged read.")
            finally:
                holding.commit()
            if change == "baa":
                with pytest.raises(VisitPrepNotFound):
                    waiting.result(timeout=10)
            else:
                result = waiting.result(timeout=10)
                assert result.status == "stale" and result.prepared is None
    with get_session() as observer:
        assert _audit_count(observer, fixture) == baseline + (
            1 if change == "correction" else 0
        )
