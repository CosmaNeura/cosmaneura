"""PostgreSQL concurrency regressions for the public Visit Prep interview."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from time import monotonic, sleep
from typing import Any, cast
from uuid import uuid4

import pytest

import app.services.visit_prep_interviews as interviews
from app.configuration.visit_prep import VisitPrepSettings
from app.services.visit_prep_interviews import (
    InterviewMutationResult,
    InterviewState,
    ResponseInput,
    VisitPrepInterviewConflict,
    VisitPrepInterviewUnavailable,
)
from app.services.visit_prep_question_sets import ensure_default_question_set
from app.services.visit_preps import RecipientInput, create_visit_prep

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.doctor import Doctor
from app.models.practice import Practice, PracticeMember
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_response import VisitPrepQuestionResponse
from sqlalchemy import func, text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select


@dataclass(frozen=True)
class _Fixture:
    doctor: Doctor
    practice: Practice
    owner: PracticeMember
    visit_prep_id: TypeIDType
    access_token: str


def _fixture(
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    *,
    created_at: datetime | None = None,
    scheduled_at: datetime | None = None,
) -> _Fixture:
    effective_created_at = created_at or datetime.now(UTC)
    effective_scheduled_at = scheduled_at or effective_created_at + timedelta(days=20)
    nonce = secrets.token_hex(5)
    doctor = Doctor(
        clerk_id=f"user_visit_prep_interview_lock_{label}_{nonce}",
        email=f"visit-prep-interview-lock-{label}-{nonce}@example.test",
        biography="Synthetic Visit Prep interview-lock test account.",
    ).save()
    practice = Practice(
        legal_name=f"Synthetic Interview Lock Practice {label} {nonce}",
        join_code=f"I{secrets.token_hex(4).upper()[:7]}",
        email_domain="example.test",
        negotiated_baa_on_file=True,
        negotiated_baa_effective_date=effective_created_at - timedelta(days=1),
        negotiated_baa_reference=f"test:visit-prep-interview-lock:{label}:{nonce}",
        billing_exempt=True,
    ).save()
    owner = PracticeMember(
        practice_id=practice.id,
        doctor_id=doctor.id,
        role="owner",
        attested_at=effective_created_at - timedelta(days=1),
        attestation_name="Synthetic Physician",
    ).save()
    seeded = ensure_default_question_set(
        owner_practice_member_id=owner.id,
        now=datetime.now(UTC) + timedelta(seconds=1),
    )
    created = create_visit_prep(
        owner_practice_member_id=owner.id,
        owner_doctor_id=doctor.id,
        practice_id=practice.id,
        idempotency_key=str(uuid4()),
        recipient=RecipientInput(
            first_name="Synthetic",
            role="patient",
            email="synthetic-patient@example.com",
        ),
        physician_display_name="Dr. Synthetic",
        scheduled_at=effective_scheduled_at,
        timezone_name="UTC",
        question_set_version_id=seeded.version.id,
        now=effective_created_at,
    )
    assert created.access_token is not None
    monkeypatch.setattr(
        interviews.visit_prep_access,
        "VISIT_PREP_SETTINGS",
        VisitPrepSettings(
            enabled=True,
            public_api_enabled=True,
            enabled_doctor_ids=frozenset({str(doctor.id)}),
        ),
    )
    return _Fixture(
        doctor=doctor,
        practice=practice,
        owner=owner,
        visit_prep_id=created.visit_prep.id,
        access_token=created.access_token,
    )


def _confirm(fixture: _Fixture) -> None:
    result = interviews.confirm_interview(
        access_token=fixture.access_token,
        first_name="Synthetic",
        role="patient",
    )
    assert result.disposition == "confirmed"


def _race(
    *commands: Callable[[], InterviewMutationResult],
) -> tuple[list[InterviewMutationResult], list[BaseException]]:
    starting_line = Barrier(len(commands))

    def invoke(
        command: Callable[[], InterviewMutationResult],
    ) -> InterviewMutationResult:
        starting_line.wait(timeout=5)
        return command()

    results: list[InterviewMutationResult] = []
    errors: list[BaseException] = []
    with ThreadPoolExecutor(max_workers=len(commands)) as executor:
        futures = [executor.submit(invoke, command) for command in commands]
        for future in futures:
            try:
                results.append(future.result(timeout=10))
            except BaseException as exc:  # pragma: no cover - asserted by callers
                errors.append(exc)
    return results, errors


def _response_count(visit_prep_id: TypeIDType) -> int:
    with get_session() as session:
        return session.exec(
            select(func.count())
            .select_from(VisitPrepQuestionResponse)
            .where(VisitPrepQuestionResponse.visit_prep_id == visit_prep_id)
        ).one()


def test_concurrent_exact_retries_create_one_response_and_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(monkeypatch, "exact-retry")
    _confirm(fixture)
    idempotency_key = str(uuid4())
    response = ResponseInput(
        question_key="reason",
        response_kind="answered",
        answer="Synthetic concurrent reason",
    )

    def submit() -> InterviewMutationResult:
        return interviews.submit_response(
            access_token=fixture.access_token,
            idempotency_key=idempotency_key,
            response=response,
        )

    results, errors = _race(submit, submit)

    assert errors == []
    assert sorted(result.disposition for result in results) == ["accepted", "replayed"]
    assert all(result.interview.submitted_questions == 1 for result in results)
    assert all(
        result.interview.current_question is not None
        and result.interview.current_question.key == "medications"
        for result in results
    )
    assert _response_count(fixture.visit_prep_id) == 1


def test_concurrent_divergent_reuse_accepts_one_response_and_conflicts_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(monkeypatch, "divergent-retry")
    _confirm(fixture)
    idempotency_key = str(uuid4())

    def submit(answer: str) -> InterviewMutationResult:
        return interviews.submit_response(
            access_token=fixture.access_token,
            idempotency_key=idempotency_key,
            response=ResponseInput(
                question_key="reason",
                response_kind="answered",
                answer=answer,
            ),
        )

    results, errors = _race(
        lambda: submit("Synthetic answer alpha"),
        lambda: submit("Synthetic answer beta"),
    )

    assert len(results) == 1
    assert results[0].disposition == "accepted"
    assert len(errors) == 1
    assert isinstance(errors[0], VisitPrepInterviewConflict)
    assert str(errors[0]) == "idempotency key belongs to a different response"
    assert _response_count(fixture.visit_prep_id) == 1


def test_concurrent_distinct_keys_for_current_question_have_one_winner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(monkeypatch, "distinct-key-race")
    _confirm(fixture)

    def submit(answer: str) -> InterviewMutationResult:
        return interviews.submit_response(
            access_token=fixture.access_token,
            idempotency_key=str(uuid4()),
            response=ResponseInput(
                question_key="reason",
                response_kind="answered",
                answer=answer,
            ),
        )

    results, errors = _race(
        lambda: submit("Synthetic answer alpha"),
        lambda: submit("Synthetic answer beta"),
    )

    assert len(results) == 1
    assert results[0].disposition == "accepted"
    assert len(errors) == 1
    assert isinstance(errors[0], VisitPrepInterviewConflict)
    assert str(errors[0]) == "question is not current"
    assert _response_count(fixture.visit_prep_id) == 1


def test_response_rechecks_expiry_after_waiting_for_aggregate_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expiry_at = datetime.now(UTC) + timedelta(seconds=8)
    scheduled_at = expiry_at - timedelta(hours=72)
    created_at = scheduled_at - timedelta(minutes=1)
    fixture = _fixture(
        monkeypatch,
        "expiry-after-lock",
        created_at=created_at,
        scheduled_at=scheduled_at,
    )
    _confirm(fixture)
    assert datetime.now(UTC) < expiry_at

    original_get_session = interviews.get_session

    @contextmanager
    def tagged_service_session() -> Iterator[Session]:
        with original_get_session() as session:
            session.execute(
                text("SET LOCAL application_name = 'visit_prep_interview_expiry_test'")
            )
            yield session

    monkeypatch.setattr(interviews, "get_session", tagged_service_session)
    submission_started = Event()

    def submit() -> InterviewMutationResult:
        submission_started.set()
        return interviews.submit_response(
            access_token=fixture.access_token,
            idempotency_key=str(uuid4()),
            response=ResponseInput(
                question_key="reason",
                response_kind="answered",
                answer="Synthetic response that must not be stored",
            ),
        )

    with get_session() as lock_session:
        lock_session.execute(
            select(VisitPrep)
            .where(cast(Any, VisitPrep.id) == fixture.visit_prep_id)
            .with_for_update()
        ).scalar_one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(submit)
            try:
                assert submission_started.wait(timeout=5)
                wait_deadline = monotonic() + 5
                blocked = False
                while monotonic() < wait_deadline:
                    blocked = lock_session.execute(
                        text(
                            "SELECT EXISTS ("
                            "SELECT 1 FROM pg_stat_activity "
                            "WHERE application_name = "
                            "'visit_prep_interview_expiry_test' "
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

            with pytest.raises(VisitPrepInterviewUnavailable):
                future.result(timeout=10)

    assert _response_count(fixture.visit_prep_id) == 0
    with get_session() as session:
        visit_prep = session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.visit_prep_id)
        ).one()
    assert visit_prep.lifecycle_state == "confirmed"
    assert visit_prep.started_at is None


def test_public_request_matches_practice_first_security_lock_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(monkeypatch, "practice-first")
    original_get_session = interviews.get_session

    @contextmanager
    def tagged_service_session() -> Iterator[Session]:
        with original_get_session() as session:
            session.execute(
                text("SET LOCAL application_name = 'visit_prep_practice_lock_test'")
            )
            yield session

    monkeypatch.setattr(interviews, "get_session", tagged_service_session)
    request_started = Event()

    def resume() -> InterviewState:
        request_started.set()
        return interviews.get_interview(access_token=fixture.access_token)

    with get_session() as security_session:
        security_session.exec(
            select(Practice).where(Practice.id == fixture.practice.id).with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(resume)
            try:
                assert request_started.wait(timeout=5)
                wait_deadline = monotonic() + 5
                blocked = False
                while monotonic() < wait_deadline:
                    security_session.execute(text("SELECT pg_stat_clear_snapshot()"))
                    blocked = security_session.execute(
                        text(
                            "SELECT EXISTS ("
                            "SELECT 1 FROM pg_stat_activity "
                            "WHERE application_name = 'visit_prep_practice_lock_test' "
                            "AND wait_event_type = 'Lock')"
                        )
                    ).scalar_one()
                    if blocked:
                        break
                    sleep(0.02)
                assert blocked

                # A member-first public lock would deadlock here. Matching the
                # security workflows' practice-first order keeps this immediate.
                security_session.exec(
                    select(PracticeMember)
                    .where(PracticeMember.id == fixture.owner.id)
                    .with_for_update()
                ).one()
            finally:
                security_session.rollback()

            result = future.result(timeout=10)

    assert result.status == "confirmation_required"


def test_public_request_fails_fast_against_member_then_doctor_lock_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(monkeypatch, "member-doctor-inversion")

    with get_session() as inverse_session:
        inverse_session.exec(
            select(PracticeMember)
            .where(PracticeMember.id == fixture.owner.id)
            .with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                interviews.get_interview,
                access_token=fixture.access_token,
            )
            with pytest.raises(OperationalError) as caught:
                future.result(timeout=5)

        assert getattr(caught.value.orig, "sqlstate", None) == "55P03"

        # The failed request rolled back its Practice and Doctor locks, so the
        # inverse holder can finish instead of forming M -> D / D -> M cycle.
        inverse_session.exec(
            select(Doctor)
            .where(Doctor.id == fixture.doctor.id)
            .with_for_update(nowait=True)
        ).one()
        inverse_session.exec(
            select(Practice)
            .where(Practice.id == fixture.practice.id)
            .with_for_update(nowait=True)
        ).one()
