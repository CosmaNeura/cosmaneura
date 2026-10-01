"""Misdelivery serialization using real, isolated PostgreSQL transactions."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

import app.services.visit_prep_interviews as interviews

from activemodel.session_manager import get_session
from app.models.visit_prep import VisitPrep, VisitPrepCredentialGeneration
from app.models.visit_prep_response import VisitPrepQuestionResponse
from sqlmodel import select

from tests.integration.visit_prep_interviews_lock_test import _confirm, _fixture


def _race(*commands: Callable[[], object]) -> tuple[list[object], list[Exception]]:
    start = Barrier(len(commands))

    def invoke(command: Callable[[], object]) -> object:
        start.wait(timeout=5)
        return command()

    results: list[object] = []
    errors: list[Exception] = []
    with ThreadPoolExecutor(max_workers=len(commands)) as executor:
        futures = [executor.submit(invoke, command) for command in commands]
        for future in futures:
            try:
                results.append(future.result(timeout=10))
            except Exception as error:
                errors.append(error)
    return results, errors


def test_simultaneous_reports_replay_one_quarantine(monkeypatch: pytest.MonkeyPatch):
    fixture = _fixture(monkeypatch, "concurrent-misdelivery")
    _confirm(fixture)

    def report() -> None:
        interviews.report_wrong_recipient(access_token=fixture.access_token)

    results, errors = _race(report, report)
    assert results == [None, None]
    assert errors == []
    with get_session() as session:
        prep = session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.visit_prep_id)
        ).one()
        credential = session.exec(
            select(VisitPrepCredentialGeneration).where(
                VisitPrepCredentialGeneration.visit_prep_id == fixture.visit_prep_id
            )
        ).one()
        assert prep.lifecycle_state == "misdirected"
        assert prep.misdirected_disposition == "pending_review"
        assert prep.revoked_at == prep.misdirected_at == credential.revoked_at
        incident = prep.misdirected_incident_ref
        assert incident is not None
    report()
    with get_session() as session:
        prep = session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.visit_prep_id)
        ).one()
        assert prep.misdirected_incident_ref == incident


def test_report_racing_answer_retains_at_most_one_quarantined_answer(monkeypatch):
    fixture = _fixture(monkeypatch, "misdelivery-answer")
    _confirm(fixture)

    def report() -> None:
        interviews.report_wrong_recipient(access_token=fixture.access_token)

    def answer() -> object:
        return interviews.submit_response(
            access_token=fixture.access_token,
            idempotency_key=str(uuid4()),
            response=interviews.ResponseInput(
                question_key="reason",
                response_kind="answered",
                answer="Synthetic answer concurrent with misdelivery reporting",
            ),
        )

    results, errors = _race(report, answer)
    assert None in results
    assert len(errors) <= 1
    assert all(
        isinstance(error, interviews.VisitPrepInterviewUnavailable) for error in errors
    )
    with get_session() as session:
        prep = session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.visit_prep_id)
        ).one()
        responses = session.exec(
            select(VisitPrepQuestionResponse).where(
                VisitPrepQuestionResponse.visit_prep_id == fixture.visit_prep_id
            )
        ).all()
        assert prep.lifecycle_state == "misdirected"
        assert prep.misdirected_disposition == "pending_review"
        assert len(responses) == (0 if errors else 1)
        assert prep.retain_until is not None
    with pytest.raises(interviews.VisitPrepInterviewUnavailable):
        interviews.get_interview(access_token=fixture.access_token)


def test_report_racing_confirmation_never_leaves_an_active_preparation(monkeypatch):
    fixture = _fixture(monkeypatch, "misdelivery-confirmation")

    def report() -> None:
        interviews.report_wrong_recipient(access_token=fixture.access_token)

    results, errors = _race(report, lambda: _confirm(fixture))
    assert None in results
    assert len(errors) <= 1
    assert all(
        isinstance(error, interviews.VisitPrepInterviewUnavailable) for error in errors
    )
    with get_session() as session:
        prep = session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.visit_prep_id)
        ).one()
        assert prep.revoked_at is not None
        assert prep.revocation_reason == "wrong_recipient"
        if errors:
            assert prep.lifecycle_state == "invited"
            assert prep.confirmed_at is prep.retain_until is None
        else:
            assert prep.lifecycle_state == "misdirected"
            assert prep.confirmed_at is not None
            assert prep.retain_until is not None
