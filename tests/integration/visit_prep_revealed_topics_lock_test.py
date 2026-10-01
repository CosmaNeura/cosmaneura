"""Real PostgreSQL concurrency and retention of newly reported conditional topics."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import uuid4

import pytest

from app.services import visit_prep_interviews as interviews
from app.services.visit_prep_contributions import correct_contribution
from app.services.visit_prep_interview_contract import (
    ResponseInput,
    VisitPrepInterviewConflict,
)

from activemodel.session_manager import get_session
from app.models.visit_prep import VisitPrep, VisitPrepLegalHold
from app.models.visit_prep_contribution import VisitPrepContributionEvent
from app.models.visit_prep_response import VisitPrepQuestionResponse
from sqlalchemy import func
from sqlalchemy.exc import DBAPIError
from sqlmodel import select

from tests.services.visit_prep_contribution_commands_test import _correct, _events
from tests.services.visit_prep_contribution_projection_test import _projection, _topic
from tests.services.visit_prep_revealed_topics_test import _revealed


@pytest.mark.parametrize("same_key", [False, True])
def test_simultaneous_additional_answer_keeps_one_source_and_exact_retry(
    monkeypatch, same_key
):
    fixture = _revealed(monkeypatch, "revealed-race")
    basis, key, barrier = _projection(fixture).basis, str(uuid4()), Barrier(2)

    def run(index):
        barrier.wait(timeout=5)
        return correct_contribution(
            access_token=fixture.access_token,
            first_name="José",
            role="patient",
            expected_basis=basis,
            idempotency_key=key if same_key else str(uuid4()),
            response=ResponseInput(
                "adherence_barrier",
                "answered",
                "An invented shared barrier."
                if same_key
                else f"An invented barrier {index}.",
            ),
        )

    successes, failures = [], []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run, index) for index in range(2)]
        for future in futures:
            try:
                successes.append(future.result(timeout=15))
            except VisitPrepInterviewConflict as error:
                failures.append(error)
    if same_key:
        assert len(successes) == 2 and not failures
        assert {row.disposition for row in successes} == {"accepted", "replayed"}
    else:
        assert len(successes) == len(failures) == 1
    events = _events(fixture)
    assert (
        len(events) == 2
        and sum(row.event_kind == "topic_answer" for row in events) == 1
    )
    topic = _topic(_projection(fixture), "adherence_barrier")
    assert topic.original_response_id is None
    assert topic.additional_answer_source_id == topic.current_correction_id


def test_additional_sources_follow_existing_legal_hold_and_approved_aggregate_cleanup(
    monkeypatch,
):
    fixture = _revealed(monkeypatch, "revealed-cleanup")
    _correct(fixture, question="adherence_barrier")
    interviews.report_wrong_recipient(access_token=fixture.access_token)
    with get_session() as session:
        prep = session.get(VisitPrep, fixture.prep_id)
        assert prep is not None
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
        retained = session.get(VisitPrepLegalHold, hold.id)
        assert retained is not None
        retained.released_at = session.exec(select(func.now())).one()
        retained.released_by_ref = "synthetic-security-operator"
        retained.release_reason = "synthetic_release"
        session.add(retained)
        session.commit()
    with get_session() as session:
        prep = session.get(VisitPrep, fixture.prep_id)
        assert prep is not None
        session.delete(prep)
        session.commit()
    with get_session() as session:
        assert session.get(VisitPrep, fixture.prep_id) is None
        for model in (VisitPrepQuestionResponse, VisitPrepContributionEvent):
            assert not session.exec(
                select(model).where(model.visit_prep_id == fixture.prep_id)
            ).all()


def test_parent_change_and_new_topic_answer_share_one_current_basis(monkeypatch):
    fixture = _revealed(monkeypatch, "revealed-parent-race")
    basis, barrier = _projection(fixture).basis, Barrier(2)

    def run(hide):
        barrier.wait(timeout=5)
        return correct_contribution(
            access_token=fixture.access_token,
            first_name="José",
            role="patient",
            expected_basis=basis,
            idempotency_key=str(uuid4()),
            response=ResponseInput(
                "adherence" if hide else "adherence_barrier",
                "answered",
                "yes" if hide else "An invented concurrent barrier.",
            ),
        )

    successes, failures = [], []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run, hide) for hide in (False, True)]
        for future in futures:
            try:
                successes.append(future.result(timeout=15))
            except VisitPrepInterviewConflict as error:
                failures.append(error)
    assert len(successes) == len(failures) == 1
    events = _events(fixture)
    assert len(events) == 2
    added = next(
        (event for event in events if event.event_kind == "topic_answer"), None
    )
    current = _projection(fixture)
    topic = _topic(current, "adherence_barrier")
    assert topic.applicable == (added is not None)
    assert topic.additional_answer_source_id == (added.id if added else None)
    assert current.current_answers_complete and not current.current_answers_reviewed
