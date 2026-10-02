"""PostgreSQL concurrency regression for Visit Prep responses."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from time import monotonic, sleep
from typing import Any, cast

import pytest

from activemodel.session_manager import get_session
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_response import VisitPrepQuestionResponse
from sqlalchemy import func, text
from sqlalchemy.exc import DBAPIError
from sqlmodel import select

from tests.services.visit_prep_response_model_test import _response, _scope, _start


def test_concurrent_same_sequence_accepts_exactly_one_response() -> None:
    scope = _scope("concurrent-sequence")
    _start(scope)
    starting_line = Barrier(2)

    def insert(question_index: int) -> VisitPrepQuestionResponse:
        response = _response(scope, question_index=question_index, sequence=1)
        starting_line.wait(timeout=5)
        with get_session() as session:
            session.add(response)
            session.commit()
            session.refresh(response)
        return response

    successes: list[VisitPrepQuestionResponse] = []
    failures: list[BaseException] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(insert, index) for index in (0, 1)]
        for future in futures:
            try:
                successes.append(future.result(timeout=10))
            except BaseException as exc:  # pragma: no cover - asserted below
                failures.append(exc)

    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], DBAPIError)
    assert "response sequence is not current" in str(failures[0])
    with get_session() as session:
        assert (
            session.exec(
                select(func.count())
                .select_from(VisitPrepQuestionResponse)
                .where(VisitPrepQuestionResponse.visit_prep_id == scope.visit_prep_id)
            ).one()
            == 1
        )


def test_truncate_cannot_bypass_response_retention() -> None:
    scope = _scope("truncate-guard")
    _start(scope)
    _response(scope, question_index=0, sequence=1).save()

    with get_session() as session:
        # PostgreSQL's new referencing FK rejects the single-table command
        # before BEFORE TRUNCATE runs. Test that fence without losing the
        # original response-trigger assertion below.
        with pytest.raises(DBAPIError, match="foreign key constraint") as rejected:
            session.execute(text("TRUNCATE TABLE public.visit_prep_question_response"))
        assert getattr(rejected.value.orig, "sqlstate", None) == "0A000"

    with get_session() as session:
        with pytest.raises(
            DBAPIError, match="responses cannot be truncated"
        ) as rejected:
            session.execute(
                text(
                    "TRUNCATE TABLE public.visit_prep_question_response, "
                    "public.visit_prep_contribution_event, "
                    "public.visit_prep_clarification_proposal, "
                    "public.visit_prep_clarification_decision"
                )
            )
        assert getattr(rejected.value.orig, "sqlstate", None) == "55000"

    with get_session() as session:
        # Either child guard may run first during aggregate TRUNCATE CASCADE.
        with pytest.raises(
            DBAPIError,
            match="responses cannot be truncated|contribution events cannot be truncated|clarification evidence cannot be truncated",
        ) as rejected:
            session.execute(text("TRUNCATE TABLE public.visit_prep CASCADE"))
        assert getattr(rejected.value.orig, "sqlstate", None) == "55000"


def test_response_rechecks_expiry_after_waiting_for_root_lock() -> None:
    expiry_at = datetime.now(UTC) + timedelta(seconds=5)
    scheduled_at = expiry_at - timedelta(hours=72)
    created_at = scheduled_at - timedelta(minutes=1)
    scope = _scope(
        "expiry-after-lock",
        now=created_at,
        scheduled_at=scheduled_at,
    )
    _start(scope, at=created_at + timedelta(seconds=10))
    insert_started = Event()

    def insert() -> VisitPrepQuestionResponse:
        response = _response(scope, question_index=0, sequence=1)
        with get_session() as session:
            session.execute(
                text("SET LOCAL application_name = 'visit_prep_expiry_lock_test'")
            )
            session.add(response)
            insert_started.set()
            session.commit()
            session.refresh(response)
        return response

    with get_session() as lock_session:
        lock_session.execute(
            select(VisitPrep)
            .where(cast(Any, VisitPrep.id) == scope.visit_prep_id)
            .with_for_update()
        ).scalar_one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(insert)
            try:
                assert insert_started.wait(timeout=5)

                wait_deadline = monotonic() + 5
                blocked = False
                while monotonic() < wait_deadline:
                    blocked = lock_session.execute(
                        text(
                            "SELECT EXISTS ("
                            "SELECT 1 FROM pg_stat_activity "
                            "WHERE application_name = 'visit_prep_expiry_lock_test' "
                            "AND wait_event_type = 'Lock')"
                        )
                    ).scalar_one()
                    if blocked:
                        break
                    sleep(0.02)
                assert blocked
                assert datetime.now(UTC) < expiry_at

                sleep(max(0.0, (expiry_at - datetime.now(UTC)).total_seconds()) + 0.1)
            finally:
                lock_session.rollback()

            with pytest.raises(DBAPIError, match="current active credential"):
                future.result(timeout=10)
