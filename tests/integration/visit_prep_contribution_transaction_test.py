"""Caller-owned patient corrections using actual isolated synthetic PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace
from typing import Literal
from uuid import uuid4

import pytest

from app.configuration.visit_prep import VisitPrepSettings
from app.services import visit_prep_contributions as contributions
from app.services import visit_prep_interviews as interviews
from app.services import visit_prep_packages as packages
from app.services.visit_prep_contribution_contract import ContributionBasis
from app.services.visit_prep_contributions import correct_contribution_in_session
from app.services.visit_prep_interview_contract import (
    ResponseInput,
    VisitPrepInterviewConflict,
    VisitPrepInterviewUnavailable,
)

from activemodel.session_manager import get_session, global_session
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep_contribution import VisitPrepContributionEvent
from sqlalchemy import func, literal, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.services.visit_prep_contribution_commands_test import _correct, _review
from tests.services.visit_prep_contribution_projection_test import (
    _prepared,
    _projection,
    _topic,
)


def _stage(
    session,
    fixture,
    *,
    basis,
    key=None,
    question="story",
    answer="Invented staged story.",
    role: Literal["patient", "caregiver"] = "patient",
):
    return correct_contribution_in_session(
        session,
        access_token=fixture.access_token,
        first_name="José",
        role=role,
        expected_basis=basis,
        idempotency_key=key or str(uuid4()),
        response=ResponseInput(
            question, "answered" if answer is not None else "skipped", answer
        ),
    )


def _count(session, fixture):
    return session.exec(
        select(func.count())
        .select_from(VisitPrepContributionEvent)
        .where(VisitPrepContributionEvent.visit_prep_id == fixture.prep_id)
    ).one()


def _marker(session, fixture):
    # A synthetic transaction marker, NOT real AI confirmation/action evidence.
    session.add(
        AuthorityAuditEvent(
            event_type="visit_prep.synthetic_transaction_check",
            outcome="allowed",
            occurred_at=datetime.now(UTC),
            resource_type="visit_prep",
            resource_ref=str(fixture.prep_id),
            route="synthetic_owned_transaction",
            intent="synthetic_atomicity_check",
        )
    )
    session.flush()


def _markers(session, fixture):
    return session.exec(
        select(func.count())
        .select_from(AuthorityAuditEvent)
        .where(
            AuthorityAuditEvent.resource_ref == str(fixture.prep_id),
            AuthorityAuditEvent.event_type == "visit_prep.synthetic_transaction_check",
        )
    ).one()


@pytest.mark.parametrize("role", ["patient", "caregiver"])
def test_staged_correction_and_marker_remain_private_until_one_caller_commit(
    monkeypatch, role
):
    fixture = _prepared(monkeypatch, "correction-transaction-private", role=role)
    before = _projection(fixture)
    key = str(uuid4())
    with get_session() as owner, get_session() as observer:
        baseline = _markers(observer, fixture)
        with monkeypatch.context() as patch:
            patch.setattr(
                contributions,
                "_visit_prep_session",
                lambda: pytest.fail("staging opened another session"),
            )
            for method in ("commit", "rollback"):
                patch.setattr(
                    owner,
                    method,
                    lambda: pytest.fail("staging ended the caller transaction"),
                )
            receipt = _stage(
                owner,
                fixture,
                basis=before.basis,
                key=key,
                role=role,
                answer="Invented atomic patient words.",
            )
            replay = _stage(
                owner,
                fixture,
                basis=before.basis,
                key=key,
                role=role,
                answer="Invented atomic patient words.",
            )
        assert receipt.basis == replay.basis == ContributionBasis(4, 1)
        assert receipt.disposition == "accepted" and replay.disposition == "replayed"
        assert "Invented" not in repr(receipt)
        assert _count(owner, fixture) == 1 and _count(observer, fixture) == 0
        _marker(owner, fixture)
        assert _markers(owner, fixture) == baseline + 1
        assert _markers(observer, fixture) == baseline
        assert owner.in_transaction()
        owner.commit()
        assert _count(observer, fixture) == 1
        assert _markers(observer, fixture) == baseline + 1
    after = _projection(fixture)
    assert _topic(after, "story").answer == "Invented atomic patient words."
    assert _topic(after, "story").original_answer == _topic(before, "story").answer
    assert after.actor_role == role


@pytest.mark.parametrize("failure", ["denial", "commit_acknowledgment"])
def test_enclosing_failure_never_returns_a_saved_receipt(monkeypatch, failure):
    fixture = _prepared(monkeypatch, "correction-transaction-failure")
    basis, key = _projection(fixture).basis, str(uuid4())
    disclosed = []
    with get_session() as observer:
        baseline = _markers(observer, fixture)

    def boundary():
        with get_session() as owner:
            _stage(owner, fixture, basis=basis, key=key)
            _marker(owner, fixture)
            with get_session() as observer:
                assert _count(observer, fixture) == 0
                assert _markers(observer, fixture) == baseline
            if failure == "denial":
                raise VisitPrepInterviewUnavailable("Visit Prep link is unavailable")

            def commit_without_ack():
                owner.commit()
                raise RuntimeError("Synthetic lost commit acknowledgment")

            commit_without_ack()
            disclosed.append("saved")

    expected = VisitPrepInterviewUnavailable if failure == "denial" else RuntimeError
    with pytest.raises(expected):
        boundary()
    assert disclosed == []
    with get_session() as observer:
        durable = int(failure == "commit_acknowledgment")
        assert _count(observer, fixture) == durable
        assert _markers(observer, fixture) == baseline + durable
    if failure == "commit_acknowledgment":
        with get_session() as owner:
            replay = _stage(owner, fixture, basis=basis, key=key)
            assert replay.disposition == "replayed"
            owner.commit()


def test_final_authority_loss_after_flush_leaves_rollback_to_caller(monkeypatch):
    fixture = _prepared(monkeypatch, "correction-transaction-final-gate")
    basis = _projection(fixture).basis
    original = contributions._append_event_in_session

    def append_then_disable(*args, **kwargs):
        result = original(*args, **kwargs)
        monkeypatch.setattr(
            interviews.visit_prep_access, "VISIT_PREP_SETTINGS", VisitPrepSettings()
        )
        return result

    monkeypatch.setattr(contributions, "_append_event_in_session", append_then_disable)
    with get_session() as owner, get_session() as observer:
        with monkeypatch.context() as patch:
            for method in ("commit", "rollback"):
                patch.setattr(
                    owner,
                    method,
                    lambda: pytest.fail("staging ended the caller transaction"),
                )
            with pytest.raises(VisitPrepInterviewUnavailable):
                _stage(owner, fixture, basis=basis)
        assert owner.in_transaction()
        assert _count(owner, fixture) == 1 and _count(observer, fixture) == 0
        owner.rollback()
        assert _count(observer, fixture) == 0


def test_exact_replay_still_rechecks_final_authority(monkeypatch):
    fixture = _prepared(monkeypatch, "correction-transaction-replay-final-gate")
    basis, key = _projection(fixture).basis, str(uuid4())
    with get_session() as owner:
        _stage(owner, fixture, basis=basis, key=key)
        owner.commit()
    original = contributions._existing_event

    def replay_then_disable(*args, **kwargs):
        result = original(*args, **kwargs)
        assert result
        monkeypatch.setattr(
            interviews.visit_prep_access, "VISIT_PREP_SETTINGS", VisitPrepSettings()
        )
        return result

    monkeypatch.setattr(contributions, "_existing_event", replay_then_disable)
    with get_session() as owner, get_session() as observer:
        with pytest.raises(VisitPrepInterviewUnavailable):
            _stage(owner, fixture, basis=basis, key=key)
        owner.rollback()
        assert _count(observer, fixture) == 1


def test_existing_patient_api_rolls_back_correction_on_final_denial(monkeypatch):
    fixture = _prepared(monkeypatch, "correction-standalone-final-gate")
    basis = _projection(fixture).basis
    original = contributions._append_event_in_session

    def append_then_disable(*args, **kwargs):
        result = original(*args, **kwargs)
        monkeypatch.setattr(
            interviews.visit_prep_access, "VISIT_PREP_SETTINGS", VisitPrepSettings()
        )
        return result

    monkeypatch.setattr(contributions, "_append_event_in_session", append_then_disable)
    with pytest.raises(VisitPrepInterviewUnavailable):
        _correct(fixture, basis=basis)
    with get_session() as observer:
        assert _count(observer, fixture) == 0


def test_flush_conflict_never_rolls_back_other_caller_work(monkeypatch):
    fixture = _prepared(monkeypatch, "correction-transaction-flush-error")
    basis = _projection(fixture).basis
    with get_session() as owner, get_session() as observer:
        baseline = _markers(observer, fixture)
        _marker(owner, fixture)
        with monkeypatch.context() as patch:
            patch.setattr(
                owner, "rollback", lambda: pytest.fail("staging rolled back the caller")
            )

            def fail():
                raise IntegrityError("synthetic", {}, Exception("synthetic conflict"))

            original = contributions._append_event_in_session

            def failing_append(*args, **kwargs):
                with monkeypatch.context() as flush_patch:
                    flush_patch.setattr(owner, "flush", fail)
                    return original(*args, **kwargs)

            patch.setattr(contributions, "_append_event_in_session", failing_append)
            with pytest.raises(VisitPrepInterviewConflict):
                _stage(owner, fixture, basis=basis)
        assert owner.in_transaction()
        owner.rollback()
        assert _markers(observer, fixture) == baseline
        assert _count(observer, fixture) == 0


def test_time_dependent_entitlement_is_rechecked_after_staged_write(monkeypatch):
    fixture = _prepared(monkeypatch, "correction-transaction-final-expiry")
    future = datetime.now(UTC) + timedelta(days=1)
    fixture.practice.billing_exempt_until = future - timedelta(seconds=1)
    fixture.practice.save()
    basis = _projection(fixture).basis
    original = contributions._append_event_in_session

    def append_then_expire(*args, **kwargs):
        result = original(*args, **kwargs)
        monkeypatch.setattr(
            interviews, "func", SimpleNamespace(clock_timestamp=lambda: literal(future))
        )
        return result

    monkeypatch.setattr(contributions, "_append_event_in_session", append_then_expire)
    with get_session() as owner, get_session() as observer:
        with pytest.raises(VisitPrepInterviewUnavailable):
            _stage(owner, fixture, basis=basis)
        assert _count(owner, fixture) == 1 and _count(observer, fixture) == 0
        owner.rollback()
        assert _count(observer, fixture) == 0


def test_staging_preserves_newly_revealed_topic_and_exact_current_review_replay(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "correction-transaction-revealed")
    _correct(fixture, question="adherence", answer="some")
    basis, key = _projection(fixture).basis, str(uuid4())
    with get_session() as owner:
        receipt = _stage(
            owner, fixture, basis=basis, key=key, question="adherence_barrier"
        )
        assert receipt.current_answers_complete and not receipt.current_answers_reviewed
        owner.commit()
    _review(fixture)
    with get_session() as owner:
        replay = _stage(
            owner, fixture, basis=basis, key=key, question="adherence_barrier"
        )
        assert replay.disposition == "replayed" and replay.current_answers_reviewed
        owner.commit()
    topic = _topic(_projection(fixture), "adherence_barrier")
    assert topic.original_response_id is None
    assert topic.additional_answer_source_id == topic.current_correction_id


def test_later_correction_waits_for_caller_transaction_and_cannot_use_stale_basis(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "correction-transaction-held-lock")
    basis = _projection(fixture).basis
    original = interviews.get_session

    @contextmanager
    def tagged():
        with original() as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_correction_transaction_wait'")
            )
            yield session

    monkeypatch.setattr(interviews, "get_session", tagged)
    with get_session() as owner:
        _stage(owner, fixture, basis=basis)
        with ThreadPoolExecutor(max_workers=1) as executor:
            later = executor.submit(_correct, fixture, basis=basis)
            try:
                with get_session() as observer:
                    _observe_waiting_lock(observer, "vp_correction_transaction_wait")
                assert not later.done()
            finally:
                owner.commit()
            with pytest.raises(VisitPrepInterviewConflict):
                later.result(timeout=10)
    with get_session() as observer:
        assert _count(observer, fixture) == 1


@pytest.mark.parametrize("change", ["baa", "correction"])
def test_staging_rechecks_authority_and_sources_after_real_owner_lock_wait(
    monkeypatch, change
):
    fixture = _prepared(monkeypatch, "correction-transaction-owner-wait")
    basis = _projection(fixture).basis
    started = Event()

    def waiting():
        with get_session() as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_correction_owner_wait'")
            )
            started.set()
            staged = _stage(session, fixture, basis=basis)
            session.commit()
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
            job = executor.submit(waiting)
            try:
                assert started.wait(timeout=5)
                with get_session() as observer:
                    _observe_waiting_lock(observer, "vp_correction_owner_wait")
                if change == "baa":
                    fixture.practice.negotiated_baa_on_file = False
                    fixture.practice.save()
                else:
                    _correct(fixture, answer="Invented intervening correction.")
            finally:
                holding.commit()
            expected = (
                VisitPrepInterviewUnavailable
                if change == "baa"
                else VisitPrepInterviewConflict
            )
            with pytest.raises(expected):
                job.result(timeout=10)
    with get_session() as observer:
        assert _count(observer, fixture) == int(change == "correction")
