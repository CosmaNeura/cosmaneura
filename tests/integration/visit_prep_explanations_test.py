"""Owned real PostgreSQL evidence for patient-owned exact-choice explanations."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from app.services import visit_prep_contributions as contributions
from app.services.visit_prep_interview_contract import (
    ResponseInput,
    VisitPrepInterviewConflict,
    VisitPrepInterviewUnavailable,
    VisitPrepInterviewValidation,
)
from app.services.visit_prep_packages import get_visit_prep_package
from app.services.visit_prep_synthesis_contract import (
    VisitPrepBriefEvidence,
    VisitPrepBriefItem,
    VisitPrepPreparedBrief,
    VisitPrepSynthesisInvalid,
    select_patient_sources,
    validate_generated_brief,
)
from app.utils.encryption import decrypt_value

from activemodel.session_manager import get_session
from app.models.visit_prep_contribution import VisitPrepContributionEvent
from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError
from sqlmodel import select

from tests.services.visit_prep_contribution_projection_test import (
    _prepared,
    _projection,
    _topic,
)


def _explain(
    fixture,
    *,
    words="An invented explanation for my selected answer.",
    basis=None,
    key=None,
    role="patient",
    question="adherence",
):
    return contributions.explain_contribution(
        access_token=fixture.access_token,
        first_name="José",
        role=role,
        expected_basis=basis or _projection(fixture).basis,
        question_key=question,
        patient_words=words,
        idempotency_key=key or str(uuid4()),
    )


def _package(fixture):
    return get_visit_prep_package(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        expected_clerk_subject=fixture.doctor.clerk_id,
        visit_prep_id=fixture.prep_id,
    )


@pytest.mark.parametrize("role", ["patient", "caregiver"])
def test_explanation_preserves_selected_code_original_history_and_attribution(
    monkeypatch, role
):
    fixture = _prepared(monkeypatch, "explanation-source-" + role, role=role)
    before = _package(fixture)
    receipt = _explain(fixture, role=role)
    current = _projection(fixture)
    topic = _topic(current, "adherence")
    assert topic.answer == topic.original_answer == "yes"
    assert topic.current_correction_id is None
    assert topic.explanation_words == "An invented explanation for my selected answer."
    assert current.actor_role == role and receipt.basis.event_sequence == 1
    assert _package(fixture).source_version != before.source_version
    with get_session() as session:
        row = session.get(VisitPrepContributionEvent, topic.explanation_id)
        assert row.event_kind == "explanation" and row.target_revision_id is None
        assert row.response_id == topic.original_response_id
        assert "invented" not in row.answer_ciphertext
        assert "invented" in decrypt_value(row.answer_ciphertext)
        assert "answer_ciphertext" not in row.model_dump()
        assert "invented" not in repr(row) and "invented" not in repr(topic)


def test_update_and_withdraw_are_new_events_and_invalidate_review(monkeypatch):
    fixture = _prepared(monkeypatch, "explanation-withdraw")
    _explain(fixture)
    first = _topic(_projection(fixture), "adherence").explanation_id
    contributions.review_contribution(
        access_token=fixture.access_token,
        first_name="José",
        role="patient",
        expected_basis=_projection(fixture).basis,
        idempotency_key=str(uuid4()),
    )
    assert _projection(fixture).current_answers_reviewed
    _explain(fixture, words="My updated invented explanation.")
    assert not _projection(fixture).current_answers_reviewed
    _explain(fixture, words=None)
    topic = _topic(_projection(fixture), "adherence")
    assert topic.explanation_words is None and topic.answer == "yes"
    with get_session() as session:
        assert session.get(VisitPrepContributionEvent, first) is not None
        rows = session.exec(
            select(VisitPrepContributionEvent).where(
                VisitPrepContributionEvent.visit_prep_id == fixture.prep_id
            )
        ).all()
        assert len(rows) == 4


@pytest.mark.parametrize(
    "envelope",
    [
        {"version": True, "kind": "story", "value": "Invented text."},
        {"version": 1, "kind": "choice", "value": "Invented text."},
        {"version": 1, "kind": "story", "value": 123},
        {"version": 1, "kind": "story", "value": " Invented text. "},
        {"version": 1, "kind": "story", "value": "Invented text.", "extra": "x"},
    ],
)
def test_corrupt_explanation_envelope_is_not_projected(monkeypatch, envelope):
    fixture = _prepared(monkeypatch, "explanation-corrupt")
    _explain(fixture)
    monkeypatch.setattr(contributions, "decrypt_value", lambda _: json.dumps(envelope))
    with pytest.raises(VisitPrepInterviewUnavailable):
        _projection(fixture)


@pytest.mark.parametrize("answer", ["some", "yes", None])
def test_any_new_choice_revision_invalidates_old_explanation_even_same_code(
    monkeypatch, answer
):
    fixture = _prepared(monkeypatch, "explanation-revision")
    _explain(fixture)
    earlier = _topic(_projection(fixture), "adherence").explanation_id
    contributions.correct_contribution(
        access_token=fixture.access_token,
        first_name="José",
        role="patient",
        expected_basis=_projection(fixture).basis,
        idempotency_key=str(uuid4()),
        response=ResponseInput(
            "adherence", "answered" if answer else "skipped", answer
        ),
    )
    current = _projection(fixture)
    assert _topic(current, "adherence").explanation_words is None
    if answer:
        _explain(fixture, words="A new explanation for the exact new revision.")
        with get_session() as session:
            row = session.get(
                VisitPrepContributionEvent,
                _topic(_projection(fixture), "adherence").explanation_id,
            )
            assert (
                row.target_revision_id
                == _topic(current, "adherence").current_correction_id
            )
    else:
        with pytest.raises(VisitPrepInterviewConflict):
            _explain(fixture)
    with get_session() as session:
        assert session.get(VisitPrepContributionEvent, earlier) is not None


def test_exact_lost_ack_retry_is_once_and_divergent_retry_denied(monkeypatch):
    fixture = _prepared(monkeypatch, "explanation-replay")
    basis, key = _projection(fixture).basis, str(uuid4())
    assert _explain(fixture, basis=basis, key=key).disposition == "accepted"
    assert _explain(fixture, basis=basis, key=key).disposition == "replayed"
    with pytest.raises(VisitPrepInterviewConflict):
        _explain(fixture, words="Different invented words.", basis=basis, key=key)
    assert _projection(fixture).basis.event_sequence == 1
    contributions.correct_contribution(
        access_token=fixture.access_token,
        first_name="José",
        role="patient",
        expected_basis=_projection(fixture).basis,
        idempotency_key=str(uuid4()),
        response=ResponseInput("adherence", "answered", "some"),
    )
    assert _explain(fixture, basis=basis, key=key).disposition == "replayed"
    current = _projection(fixture)
    assert current.basis.event_sequence == 2
    assert _topic(current, "adherence").answer == "some"
    assert _topic(current, "adherence").explanation_words is None


@pytest.mark.parametrize(
    "question,words,error",
    [
        ("story", "Text", VisitPrepInterviewValidation),
        ("not_a_topic", "Text", VisitPrepInterviewValidation),
        ("adherence", " ", VisitPrepInterviewValidation),
        ("adherence", "x" * 2001, VisitPrepInterviewValidation),
        ("adherence", "a\x00b", VisitPrepInterviewValidation),
    ],
)
def test_invalid_words_and_nonchoice_topics_are_denied(
    monkeypatch, question, words, error
):
    fixture = _prepared(monkeypatch, "explanation-invalid")
    with pytest.raises(error):
        _explain(fixture, question=question, words=words)
    assert _projection(fixture).basis.event_sequence == 0


def test_stale_basis_and_wrong_role_are_denied_without_write(monkeypatch):
    fixture = _prepared(monkeypatch, "explanation-stale")
    basis = _projection(fixture).basis
    _explain(fixture)
    with pytest.raises(VisitPrepInterviewConflict):
        _explain(fixture, basis=basis)
    with pytest.raises(VisitPrepInterviewUnavailable):
        _explain(fixture, role="caregiver")
    assert _projection(fixture).basis.event_sequence == 1


@pytest.mark.parametrize("other_operation", ["explanation", "choice", "review"])
def test_real_concurrent_writes_share_exact_basis_one_winner(
    monkeypatch, other_operation
):
    fixture = _prepared(monkeypatch, "explanation-race-" + other_operation)
    basis, barrier = _projection(fixture).basis, Barrier(2)

    def run(explanation):
        barrier.wait(timeout=5)
        if explanation or other_operation == "explanation":
            return _explain(
                fixture,
                basis=basis,
                words="First invented words."
                if explanation
                else "Second invented words.",
            )
        args = dict(
            access_token=fixture.access_token,
            first_name="José",
            role="patient",
            expected_basis=basis,
            idempotency_key=str(uuid4()),
        )
        return (
            contributions.review_contribution(**args)
            if other_operation == "review"
            else contributions.correct_contribution(
                **args, response=ResponseInput("adherence", "answered", "some")
            )
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run, value) for value in (True, False)]
        results = []
        for future in futures:
            try:
                results.append(future.result(timeout=20))
            except VisitPrepInterviewConflict:
                results.append(None)
    assert sum(result is not None for result in results) == 1
    assert _projection(fixture).basis.event_sequence == 1


def test_explanation_is_separate_citable_source_and_not_choice_or_old_text(monkeypatch):
    fixture = _prepared(monkeypatch, "explanation-synthesis")
    _explain(fixture)
    selection = select_patient_sources(_package(fixture))
    selected = [s for s in selection.sources if s.question_key == "adherence"]
    assert len(selected) == 2
    choice, explanation = selected
    assert choice.answer == "yes" and choice.kind == "choice"
    assert explanation.explains_source_ref == choice.source_ref
    item = VisitPrepBriefItem(
        text="Recipient supplied an explanation.",
        evidence=[
            VisitPrepBriefEvidence(
                source_ref=explanation.source_ref, quote="invented explanation"
            )
        ],
    )
    brief = VisitPrepPreparedBrief(
        headline=item,
        summary=[item],
        visit_priorities=[],
        medication_and_adherence=[],
        barriers=[],
        uncertainties=[],
    )
    validate_generated_brief(brief, selection)
    with pytest.raises(VisitPrepSynthesisInvalid):
        validate_generated_brief(
            brief.model_copy(
                update={
                    "headline": item.model_copy(
                        update={
                            "evidence": [
                                VisitPrepBriefEvidence(
                                    source_ref=choice.source_ref,
                                    quote="invented explanation",
                                )
                            ]
                        }
                    )
                }
            ),
            selection,
        )


def test_raw_database_guard_denies_wrong_target_and_retention_bypass(monkeypatch):
    fixture = _prepared(monkeypatch, "explanation-db-guard")
    _explain(fixture)
    topic = _topic(_projection(fixture), "adherence")
    with get_session() as session:
        row = session.get(VisitPrepContributionEvent, topic.explanation_id)
        table = VisitPrepContributionEvent.__table__
        for statement in (
            update(table).where(table.c.id == row.id).values(target_revision_id=row.id),
            delete(table).where(table.c.id == row.id),
            text("TRUNCATE public.visit_prep_contribution_event"),
        ):
            with pytest.raises(DBAPIError):
                with session.begin_nested():
                    session.execute(statement)
        columns = {
            c.name: getattr(row, c.name)
            for c in table.columns
            if c.name not in {"id", "created_at", "updated_at", "recorded_at"}
        }
        columns.update(
            event_sequence=2,
            basis_event_sequence=1,
            target_revision_id=row.id,
            idempotency_key=str(uuid4()),
        )
        with pytest.raises(DBAPIError, match="target revision changed"):
            with session.begin_nested():
                session.execute(table.insert().values(**columns))
