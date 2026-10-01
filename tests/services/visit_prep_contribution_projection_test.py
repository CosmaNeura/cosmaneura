"""Synthetic PostgreSQL evidence for one current patient-report projection."""

from dataclasses import replace
from typing import Any, Literal
from uuid import uuid4

import pytest

from app.services import visit_prep_interviews as interviews
from app.services.visit_prep_contribution_contract import (
    ContributionBasis,
    contribution_payload_hmac,
)
from app.services.visit_prep_contributions import _load_contribution
from app.services.visit_prep_interview_contract import (
    ResponseInput,
    VisitPrepInterviewUnavailable,
    VisitPrepInterviewValidation,
    _answer_envelope,
    _CanonicalResponse,
)
from app.utils.encryption import encrypt_value

from activemodel.session_manager import get_session
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_contribution import VisitPrepContributionEvent
from app.models.visit_prep_question_set import VisitPrepQuestion
from app.models.visit_prep_response import VisitPrepQuestionResponse
from sqlmodel import select

from tests.services.visit_prep_interviews_test import _fixture, _respond


def _prepared(
    monkeypatch,
    label,
    *,
    adherence="yes",
    role: Literal["patient", "caregiver"] = "patient",
    complete=True,
):
    fixture = _fixture(monkeypatch, label, recipient_role=role)
    interviews.confirm_interview(
        access_token=fixture.access_token, first_name="José", role=role
    )
    _respond(fixture, "reason", answer="My original invented reason.")
    if complete:
        _respond(fixture, "medications")
        _respond(fixture, "adherence", answer=adherence)
        if adherence in {"some", "no"}:
            _respond(fixture, "adherence_barrier", answer="An invented barrier.")
        _respond(fixture, "story", answer="My invented health story.")
        interviews.complete_interview(access_token=fixture.access_token)
    return fixture


def _projection(fixture):
    with get_session() as session:
        authorized = interviews._lock_authorized_interview(
            session, access_token=fixture.access_token
        )
        return _load_contribution(session, visit_prep=authorized.visit_prep)


def _append(fixture, *, question_key=None, answer=None, review=False):
    with get_session() as session:
        authorized = interviews._lock_authorized_interview(
            session, access_token=fixture.access_token
        )
        prep = authorized.visit_prep
        current = _load_contribution(session, visit_prep=prep)
        original = question = canonical = None
        if not review:
            assert isinstance(question_key, str)
            question = session.exec(
                select(VisitPrepQuestion).where(
                    VisitPrepQuestion.question_set_version_id
                    == prep.question_set_version_id,
                    VisitPrepQuestion.question_key == question_key,
                )
            ).one()
            original = session.exec(
                select(VisitPrepQuestionResponse).where(
                    VisitPrepQuestionResponse.visit_prep_id == prep.id,
                    VisitPrepQuestionResponse.question_id == question.id,
                )
            ).one()
            canonical = interviews._canonical_response(
                ResponseInput(
                    question_key, "skipped" if answer is None else "answered", answer
                ),
                question=question,
            )
        event = VisitPrepContributionEvent(
            visit_prep_id=prep.id,
            owner_practice_member_id=prep.owner_practice_member_id,
            question_set_version_id=prep.question_set_version_id,
            credential_generation=authorized.credential.generation,
            actor_role=current.actor_role,
            event_kind="review" if review else "correction",
            event_sequence=current.basis.event_sequence + 1,
            basis_event_sequence=current.basis.event_sequence,
            basis_response_count=current.basis.original_response_count,
            response_id=original.id if original else None,
            response_kind=canonical.response_kind if canonical else None,
            answer_ciphertext=(
                encrypt_value(
                    _answer_envelope(kind=question.kind, value=canonical.answer)
                )
                if question and canonical and canonical.answer is not None
                else None
            ),
            answer_format_version=1
            if canonical and canonical.answer is not None
            else None,
            idempotency_key=str(uuid4()),
            payload_hmac_sha256=contribution_payload_hmac(
                visit_prep_id=prep.id,
                actor_role=current.actor_role,
                event_kind="review" if review else "correction",
                basis=current.basis,
                original_response_id=original.id if original else None,
                response=canonical,
            ),
        )
        session.add(event)
        session.commit()
        session.refresh(event)
        return event


def _topic(contribution, key):
    return next(topic for topic in contribution.topics if topic.question.key == key)


def test_current_projection_retains_original_wording_and_exact_source(monkeypatch):
    fixture = _prepared(monkeypatch, "projection-source")
    before = _projection(fixture)
    correction = _append(
        fixture, question_key="reason", answer="My corrected invented reason."
    )
    current = _projection(fixture)
    topic = _topic(current, "reason")
    assert topic.answer == "My corrected invented reason."
    assert topic.original_answer == "My original invented reason."
    assert topic.original_response_id == _topic(before, "reason").original_response_id
    assert topic.original_response_kind == "answered"
    assert topic.original_recorded_at == _topic(before, "reason").recorded_at
    assert topic.current_correction_id == correction.id
    assert topic.recorded_at == correction.recorded_at
    assert current.basis == ContributionBasis(4, 1)
    assert current.current_answers_complete and current.original_interview_completed
    assert not current.current_answers_reviewed
    assert "invented" not in repr(topic) and "invented" not in repr(current)


def test_current_review_is_invalidated_by_any_later_correction(monkeypatch):
    fixture = _prepared(monkeypatch, "projection-review")
    review = _append(fixture, review=True)
    reviewed = _projection(fixture)
    assert reviewed.current_answers_reviewed
    assert (
        reviewed.current_review_id == review.id
        and reviewed.reviewed_at == review.recorded_at
    )
    _append(fixture, question_key="story", answer="My revised invented health story.")
    changed = _projection(fixture)
    assert not changed.current_answers_reviewed
    assert changed.current_review_id is None and changed.reviewed_at is None
    renewed = _append(fixture, review=True)
    assert _projection(fixture).current_review_id == renewed.id
    with get_session() as session:
        assert session.get(VisitPrepContributionEvent, review.id) is not None


def test_changed_condition_reveals_missing_topic_despite_original_completion(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "projection-reveal")
    assert not _topic(_projection(fixture), "adherence_barrier").applicable
    _append(fixture, question_key="adherence", answer="some")
    current = _projection(fixture)
    assert current.original_interview_completed
    assert not current.current_answers_complete
    assert current.missing_question_keys == ("adherence_barrier",)
    # Raw storage review evidence alone cannot imply complete patient review.
    _append(fixture, review=True)
    assert not _projection(fixture).current_answers_reviewed
    assert _topic(current, "story").applicable


def test_changed_condition_excludes_but_does_not_erase_old_barrier(monkeypatch):
    fixture = _prepared(monkeypatch, "projection-hide", adherence="some")
    original = _topic(_projection(fixture), "adherence_barrier")
    _append(fixture, question_key="adherence", answer="yes")
    current = _projection(fixture)
    barrier = _topic(current, "adherence_barrier")
    assert not barrier.applicable and barrier.answer == "An invented barrier."
    assert barrier.original_response_id == original.original_response_id
    assert current.current_answers_complete and not current.missing_question_keys


def test_skip_correction_preserves_original_and_skipped_information_is_missing(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "projection-skip")
    _append(fixture, question_key="story")
    topic = _topic(_projection(fixture), "story")
    assert topic.response_kind == "skipped" and topic.answer is None
    assert topic.original_answer == "My invented health story."
    _append(fixture, review=True)
    assert _projection(fixture).current_answers_reviewed


def test_new_original_answer_invalidates_an_earlier_partial_review(monkeypatch):
    fixture = _prepared(monkeypatch, "projection-new-answer", complete=False)
    _append(fixture, review=True)
    partial = _projection(fixture)
    assert not partial.current_answers_complete and not partial.current_answers_reviewed
    assert partial.missing_question_keys == ("medications", "adherence", "story")
    _respond(fixture, "medications")
    for key in ("adherence", "story"):
        _respond(fixture, key)
    interviews.complete_interview(access_token=fixture.access_token)
    current = _projection(fixture)
    assert current.current_answers_complete and not current.current_answers_reviewed
    assert current.basis == ContributionBasis(4, 1)


def test_caregiver_report_stays_caregiver_not_clinician_verification(monkeypatch):
    fixture = _prepared(monkeypatch, "projection-caregiver", role="caregiver")
    _append(fixture, question_key="reason", answer="An invented caregiver correction.")
    _append(fixture, review=True)
    assert _projection(fixture).actor_role == "caregiver"
    assert _projection(fixture).current_answers_reviewed


def test_invited_and_quarantined_sources_are_not_disclosed(monkeypatch):
    fixture = _fixture(monkeypatch, "projection-unconfirmed")
    with pytest.raises(VisitPrepInterviewUnavailable):
        _projection(fixture)
    interviews.confirm_interview(
        access_token=fixture.access_token, first_name="José", role="patient"
    )
    empty = _projection(fixture)
    assert empty.basis == ContributionBasis(0, 0)
    assert not empty.current_answers_complete
    interviews.report_wrong_recipient(access_token=fixture.access_token)
    with get_session() as session:
        prep = session.get(VisitPrep, fixture.prep_id)
        assert prep is not None
        with pytest.raises(VisitPrepInterviewUnavailable):
            _load_contribution(session, visit_prep=prep)


@pytest.mark.parametrize(
    "corruption", ["ciphertext", "hmac", "basis", "empty_basis", "role", "sequence"]
)
def test_untrusted_or_corrupt_event_is_not_treated_as_patient_evidence(
    monkeypatch, corruption
):
    fixture = _prepared(monkeypatch, "projection-corrupt")
    correction = _append(
        fixture, question_key="reason", answer="An invented correction."
    )
    # Mutate only the loaded ORM object, not persisted append-only rows. This
    # exercises the loader's checks even if an upstream caller hands it bad state.
    with get_session() as session:
        authorized = interviews._lock_authorized_interview(
            session, access_token=fixture.access_token
        )
        row = session.get(VisitPrepContributionEvent, correction.id)
        assert row is not None
        if corruption == "ciphertext":
            row.answer_ciphertext = "invalid invented encrypted envelope"
        elif corruption == "hmac":
            row.payload_hmac_sha256 = "0" * 64
        elif corruption == "basis":
            row.basis_response_count = 1
        elif corruption == "empty_basis":
            row.basis_response_count = 0
        elif corruption == "role":
            row.actor_role = "caregiver"
        else:
            row.event_sequence = 7
        with session.no_autoflush:
            with pytest.raises(VisitPrepInterviewUnavailable):
                _load_contribution(session, visit_prep=authorized.visit_prep)
        session.rollback()


def test_event_fingerprint_is_keyed_and_separates_operation_basis_actor_and_source():
    from typeid import TypeID

    prep_id, response_id = TypeID("visit_prep"), TypeID("visit_prep_response")
    response = _CanonicalResponse("story", "answered", "An invented story.")
    kwargs: dict[str, Any] = dict(
        visit_prep_id=prep_id,
        actor_role="patient",
        event_kind="correction",
        basis=ContributionBasis(4, 0),
        original_response_id=response_id,
        response=response,
    )
    digest = contribution_payload_hmac(**kwargs)
    assert len(digest) == 64 and "invented" not in digest
    assert digest == contribution_payload_hmac(**kwargs)
    for changes in (
        {"visit_prep_id": TypeID("visit_prep")},
        {"actor_role": "caregiver"},
        {"basis": ContributionBasis(4, 1)},
        {"basis": ContributionBasis(5, 0)},
        {"original_response_id": TypeID("visit_prep_response")},
        {"response": replace(response, answer="Another invented story.")},
        {"response": replace(response, question_key="reason")},
        {"event_kind": "review", "response": None, "original_response_id": None},
    ):
        assert digest != contribution_payload_hmac(**(kwargs | changes))


@pytest.mark.parametrize(
    "count,sequence", [(-1, 0), (6, 0), (True, 0), (0, 1), (1, -1), (1, True)]
)
def test_basis_rejects_out_of_bounds_or_coerced_values(count, sequence):
    with pytest.raises(VisitPrepInterviewValidation):
        ContributionBasis(count, sequence)
