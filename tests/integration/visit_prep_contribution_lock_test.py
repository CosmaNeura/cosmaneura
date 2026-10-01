"""Independent-connection fences for additive patient contribution history."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from time import monotonic, sleep

import pytest

from app.services import visit_prep_interviews as interviews
from app.services.visit_preps import rotate_visit_prep_credential

from activemodel.session_manager import get_session, global_session
from app.models.visit_prep import VisitPrep, VisitPrepLegalHold
from app.models.visit_prep_contribution import VisitPrepContributionEvent
from app.models.visit_prep_response import VisitPrepQuestionResponse
from sqlalchemy import func, text
from sqlalchemy.exc import DBAPIError
from sqlmodel import select

from tests.integration.visit_prep_interviews_lock_test import (
    _fixture as lock_fixture,
)
from tests.services.visit_prep_contribution_model_test import (
    _contribution_fixture,
    _event,
)
from tests.services.visit_prep_interviews_test import _Fixture


def _observe_waiting_lock(session, name):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        session.execute(text("SELECT pg_stat_clear_snapshot()"))
        blocked = session.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                "WHERE application_name = :name AND wait_event_type = 'Lock')"
            ),
            {"name": name},
        ).scalar_one()
        if blocked:
            return
        sleep(0.02)
    pytest.fail("the contribution INSERT never reached the expected row lock")


def test_concurrent_correction_and_review_accept_only_one_exact_basis(monkeypatch):
    fixture = _contribution_fixture(monkeypatch, "concurrent-contribution")
    barrier = Barrier(2)

    def insert(kind):
        event = _event(fixture, kind=kind)
        with get_session() as session:
            barrier.wait(timeout=5)
            session.add(event)
            session.commit()
            session.refresh(event)
            return event

    successes, failures = [], []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(insert, kind) for kind in ("correction", "review")]
        for future in futures:
            try:
                successes.append(future.result(timeout=10))
            except DBAPIError as error:
                failures.append(error)
    assert len(successes) == len(failures) == 1
    assert "basis is not current" in str(failures[0])
    with get_session() as session:
        assert (
            session.exec(
                select(func.count())
                .select_from(VisitPrepContributionEvent)
                .where(VisitPrepContributionEvent.visit_prep_id == fixture.prep_id)
            ).one()
            == 1
        )


@pytest.mark.parametrize("action", ["rotate", "report_wrong_recipient"])
def test_waiting_event_rechecks_authority_after_committed_change(monkeypatch, action):
    fixture = _contribution_fixture(monkeypatch, f"contribution-wait-{action}")
    started = Event()

    def insert():
        event = _event(fixture)
        with get_session() as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_contribution_change'")
            )
            started.set()
            session.add(event)
            session.commit()

    with global_session() as lock_session:
        lock_session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id).with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(insert)
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(lock_session, "vp_contribution_change")
                # These calls share the root owner's session/transaction. The
                # waiting INSERT observes their committed revocation/rotation.
                if action == "rotate":
                    rotate_visit_prep_credential(
                        owner_practice_member_id=fixture.owner.id,
                        owner_doctor_id=fixture.doctor.id,
                        practice_id=fixture.practice.id,
                        visit_prep_id=fixture.prep_id,
                        expected_credential_generation=1,
                    )
                else:
                    interviews.report_wrong_recipient(access_token=fixture.access_token)
            finally:
                lock_session.commit()
            with pytest.raises(
                DBAPIError, match="inactive Visit Prep|current active credential"
            ):
                future.result(timeout=10)


def test_expiry_is_rechecked_after_a_real_database_row_lock(monkeypatch):
    expiry_at = datetime.now(UTC) + timedelta(seconds=8)
    scheduled_at = expiry_at - timedelta(hours=72)
    # The fixture's credential expires at appointment + 72h; provider creation
    # may intentionally be in the past for this purely synthetic expiry race.
    fixture = lock_fixture(
        monkeypatch,
        "contribution-expiry",
        created_at=scheduled_at - timedelta(minutes=1),
        scheduled_at=scheduled_at,
    )
    interviews.confirm_interview(
        access_token=fixture.access_token, first_name="Synthetic", role="patient"
    )
    interviews.submit_response(
        access_token=fixture.access_token,
        idempotency_key="748aa0dc-6737-4d45-b371-586afde0b66d",
        response=interviews.ResponseInput(
            question_key="reason", response_kind="skipped"
        ),
    )
    # Both existing fixture types refer to the same root with different field
    # names. Use an explicit local alias, never change the feature contract.
    base = _Fixture(
        doctor=fixture.doctor,
        practice=fixture.practice,
        owner=fixture.owner,
        prep_id=fixture.visit_prep_id,
        access_token=fixture.access_token,
        scheduled_at=scheduled_at,
    )
    started = Event()

    def insert():
        event = _event(base, kind="review")
        with get_session() as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_contribution_expiry'")
            )
            session.add(event)
            started.set()
            session.commit()

    with get_session() as lock_session:
        lock_session.exec(
            select(VisitPrep).where(VisitPrep.id == base.prep_id).with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(insert)
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(lock_session, "vp_contribution_expiry")
                assert datetime.now(UTC) < expiry_at
                sleep(max(0.0, (expiry_at - datetime.now(UTC)).total_seconds()) + 0.1)
            finally:
                lock_session.rollback()
            with pytest.raises(DBAPIError, match="current active credential"):
                future.result(timeout=10)


def test_approved_synthetic_aggregate_purge_removes_both_histories_atomically(
    monkeypatch,
):
    fixture = _contribution_fixture(monkeypatch, "contribution-approved-cleanup")
    _event(fixture).save()
    _event(fixture, basis=1, kind="review").save()
    interviews.report_wrong_recipient(access_token=fixture.access_token)
    with get_session() as session:
        prep = session.get(VisitPrep, fixture.prep_id)
        assert prep is not None and prep.misdirected_at is not None
        prep.misdirected_disposition = "purge_approved"
        prep.misdirected_disposition_at = datetime.now(UTC)
        prep.misdirected_disposition_by_ref = "synthetic-security-operator"
        prep.misdirected_disposition_until = prep.misdirected_disposition_at
        session.add(prep)
        session.commit()
    hold = VisitPrepLegalHold(
        visit_prep_id=fixture.prep_id,
        owner_practice_member_id=fixture.owner.id,
        generation=1,
        placed_at=datetime.now(UTC),
        placed_by_ref="synthetic-security-operator",
        placement_reason="synthetic_retention_check",
    ).save()
    with get_session() as session:
        prep = session.get(VisitPrep, fixture.prep_id)
        assert prep is not None
        with pytest.raises(DBAPIError, match="active legal hold"):
            with session.begin_nested():
                session.delete(prep)
                session.flush()
    with get_session() as session:
        retained_hold = session.get(VisitPrepLegalHold, hold.id)
        assert retained_hold is not None
        # The existing hold guard compares with transaction time, not Python
        # wall time measured after the transaction's first SELECT.
        retained_hold.released_at = session.exec(select(func.now())).one()
        retained_hold.released_by_ref = "synthetic-security-operator"
        retained_hold.release_reason = "synthetic_release"
        session.add(retained_hold)
        session.commit()
    with get_session() as session:
        prep = session.get(VisitPrep, fixture.prep_id)
        assert prep is not None
        session.delete(prep)
        session.commit()
    with get_session() as session:
        assert session.get(VisitPrep, fixture.prep_id) is None
        for model in (VisitPrepQuestionResponse, VisitPrepContributionEvent):
            assert (
                session.exec(
                    select(model).where(model.visit_prep_id == fixture.prep_id)
                ).all()
                == []
            )
