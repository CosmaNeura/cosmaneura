"""Independent PostgreSQL connections exercise actual command locks."""

from datetime import UTC, datetime, timedelta
from threading import Barrier, Thread
from uuid import uuid4

import pytest

from app.services.visit_preps import (
    VisitPrepConflict,
    VisitPrepNotFound,
    rotate_visit_prep_credential,
)

from activemodel.session_manager import SessionManager, global_session
from app.models.doctor import Doctor
from app.models.practice import PracticeMember
from app.models.visit_prep import VisitPrep
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError
from sqlmodel import col

from tests.services.visit_preps_test import _create, _scope


def test_two_concurrent_creates_share_one_aggregate_and_return_plaintext_only_once():
    now = datetime.now(UTC) + timedelta(seconds=2)
    scope = _scope("concurrent-create", now=now)
    start = Barrier(2)
    results, errors = [], []
    key = str(uuid4())

    def create():
        try:
            start.wait(timeout=5)
            results.append(
                _create(
                    scope,
                    now=now,
                    scheduled_at=now + timedelta(days=7),
                    idempotency_key=key,
                )
            )
        except BaseException as error:
            errors.append(error)

    threads = [Thread(target=create) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert errors == [] and len(results) == 2
    assert {result.disposition for result in results} == {"created", "replayed"}
    assert results[0].visit_prep.id == results[1].visit_prep.id
    assert sum(result.access_token is not None for result in results) == 1
    engine = SessionManager.get_instance().get_engine()
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(VisitPrep)
                .where(
                    col(VisitPrep.owner_practice_member_id) == scope.owner.id,
                )
            )
            == 1
        )


def test_request_cached_member_is_refreshed_after_a_separate_access_withdrawal():
    now = datetime.now(UTC) + timedelta(seconds=2)
    scope = _scope("cached-member", now=now)
    engine = SessionManager.get_instance().get_engine()
    with global_session() as request_session:
        cached = request_session.execute(
            select(PracticeMember).where(
                col(PracticeMember.id) == scope.owner.id,
            )
        ).scalar_one()
        assert cached.seat_state == "active"
        with engine.begin() as connection:
            connection.execute(
                update(PracticeMember)
                .where(
                    col(PracticeMember.id) == scope.owner.id,
                )
                .values(seat_state="inactive", access_ends_at=None)
            )
        with pytest.raises(VisitPrepNotFound):
            _create(scope, now=now, scheduled_at=now + timedelta(days=7))


def test_doctor_lock_contention_rolls_back_request_and_can_retry_safely():
    now = datetime.now(UTC) + timedelta(seconds=2)
    scope = _scope("doctor-contention", now=now)
    engine = SessionManager.get_instance().get_engine()
    with global_session():
        with engine.begin() as blocker:
            blocker.execute(
                select(Doctor)
                .where(
                    col(Doctor.id) == scope.doctor.id,
                )
                .with_for_update()
            ).one()
            with pytest.raises(OperationalError) as caught:
                _create(scope, now=now, scheduled_at=now + timedelta(days=7))
            assert getattr(caught.value.orig, "sqlstate", None) == "55P03"
        assert (
            _create(scope, now=now, scheduled_at=now + timedelta(days=7)).disposition
            == "created"
        )


def test_concurrent_rotations_have_one_winner_and_one_stale_generation():
    now = datetime.now(UTC) + timedelta(seconds=2)
    scope = _scope("rotate-race", now=now)
    prep = _create(scope, now=now, scheduled_at=now + timedelta(days=7)).visit_prep
    start, results, errors = Barrier(2), [], []

    def rotate():
        try:
            start.wait(timeout=5)
            results.append(
                rotate_visit_prep_credential(
                    owner_practice_member_id=scope.owner.id,
                    owner_doctor_id=scope.doctor.id,
                    practice_id=scope.practice.id,
                    visit_prep_id=prep.id,
                    expected_credential_generation=1,
                    now=now + timedelta(minutes=1),
                )
            )
        except BaseException as error:
            errors.append(error)

    threads = [Thread(target=rotate) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == len(errors) == 1
    assert isinstance(errors[0], VisitPrepConflict)
    assert results[0].access_token is not None
    assert results[0].visit_prep.credential_generation == 2
