"""Patient-attributed correction/review commands with invented clinical text."""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

import pytest

from app.configuration.visit_prep import VisitPrepSettings
from app.services import visit_prep_interviews as interviews
from app.services.visit_prep_contribution_contract import ContributionBasis
from app.services.visit_prep_contributions import (
    correct_contribution,
    get_patient_contribution,
    review_contribution,
)
from app.services.visit_prep_interview_contract import (
    ResponseInput,
    VisitPrepInterviewConflict,
    VisitPrepInterviewUnavailable,
    VisitPrepInterviewValidation,
)
from app.services.visit_preps import rotate_visit_prep_credential
from app.utils.encryption import EncryptionError

from activemodel.session_manager import get_session
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_contribution import VisitPrepContributionEvent
from app.models.visit_prep_response import VisitPrepQuestionResponse
from sqlalchemy import update
from sqlmodel import SQLModel, select

from tests.services.visit_prep_contribution_projection_test import (
    _prepared,
    _projection,
    _topic,
)


def _correct(
    fixture,
    *,
    key=None,
    basis=None,
    question="story",
    answer: str | None = "An invented correction.",
    role: Literal["patient", "caregiver"] = "patient",
    token=None,
):
    return correct_contribution(
        access_token=token or fixture.access_token,
        first_name="José",
        role=role,
        expected_basis=basis or _projection(fixture).basis,
        idempotency_key=key or str(uuid4()),
        response=ResponseInput(
            question, "answered" if answer is not None else "skipped", answer
        ),
    )


def _review(
    fixture,
    *,
    key=None,
    basis=None,
    role: Literal["patient", "caregiver"] = "patient",
    token=None,
):
    return review_contribution(
        access_token=token or fixture.access_token,
        first_name="José",
        role=role,
        expected_basis=basis or _projection(fixture).basis,
        idempotency_key=key or str(uuid4()),
    )


def _events(fixture):
    with get_session() as session:
        return session.exec(
            select(VisitPrepContributionEvent).where(
                VisitPrepContributionEvent.visit_prep_id == fixture.prep_id
            )
        ).all()


def test_correction_commit_preserves_original_and_returns_value_free_receipt(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "command-correct")
    before = _projection(fixture)
    receipt = _correct(fixture)
    assert receipt.disposition == "accepted" and receipt.basis == ContributionBasis(
        4, 1
    )
    assert receipt.current_answers_complete and not receipt.current_answers_reviewed
    assert set(asdict(receipt)) == {
        "disposition",
        "basis",
        "current_answers_complete",
        "current_answers_reviewed",
    }
    assert "invented" not in repr(receipt) and "invented" not in str(asdict(receipt))
    topic = _topic(_projection(fixture), "story")
    assert topic.answer == "An invented correction."
    assert topic.original_answer == _topic(before, "story").answer
    with get_session() as session:
        original = session.get(VisitPrepQuestionResponse, topic.original_response_id)
        prep = session.get(VisitPrep, fixture.prep_id)
        assert original is not None and prep is not None
        assert prep.lifecycle_state == "completed" and prep.completed_at is not None
        assert original.answer_ciphertext != _events(fixture)[0].answer_ciphertext


def test_correction_retry_is_exact_and_does_not_rewrite_later_review(monkeypatch):
    fixture = _prepared(monkeypatch, "command-replay")
    basis, key = _projection(fixture).basis, str(uuid4())
    _correct(fixture, basis=basis, key=key)
    _review(fixture)
    replay = _correct(fixture, basis=basis, key=key)
    assert replay.disposition == "replayed"
    assert replay.current_answers_reviewed and replay.basis.event_sequence == 2
    assert len(_events(fixture)) == 2
    for changes in (
        {"answer": "A divergent invented correction."},
        {"question": "reason"},
        {"basis": ContributionBasis(4, 1)},
    ):
        with pytest.raises(VisitPrepInterviewConflict):
            _correct(fixture, **({"basis": basis, "key": key} | changes))
    with pytest.raises(VisitPrepInterviewConflict):
        _review(fixture, basis=basis, key=key)
    assert len(_events(fixture)) == 2


def test_stale_basis_cannot_overwrite_another_patient_revision(monkeypatch):
    fixture = _prepared(monkeypatch, "command-stale")
    basis = _projection(fixture).basis
    _correct(fixture, basis=basis)
    with pytest.raises(VisitPrepInterviewConflict):
        _correct(fixture, basis=basis)
    with pytest.raises(VisitPrepInterviewConflict):
        _review(fixture, basis=basis)
    assert len(_events(fixture)) == 1


def test_review_retry_reports_current_staleness_without_adding_review(monkeypatch):
    fixture = _prepared(monkeypatch, "command-review-replay")
    basis, key = _projection(fixture).basis, str(uuid4())
    accepted = _review(fixture, basis=basis, key=key)
    assert accepted.current_answers_reviewed
    _correct(fixture)
    replay = _review(fixture, basis=basis, key=key)
    assert replay.disposition == "replayed" and not replay.current_answers_reviewed
    assert len(_events(fixture)) == 2
    with pytest.raises(VisitPrepInterviewConflict):
        _review(fixture, basis=_projection(fixture).basis, key=key)


def test_newly_applicable_topic_requires_response_before_review(monkeypatch):
    fixture = _prepared(monkeypatch, "command-reveal")
    _review(fixture)
    receipt = _correct(fixture, question="adherence", answer="some")
    assert not receipt.current_answers_complete and not receipt.current_answers_reviewed
    assert _projection(fixture).missing_question_keys == ("adherence_barrier",)
    with pytest.raises(VisitPrepInterviewConflict):
        _review(fixture)
    added = _correct(fixture, question="adherence_barrier")
    assert added.current_answers_complete and not added.current_answers_reviewed
    topic = _topic(_projection(fixture), "adherence_barrier")
    assert topic.original_response_id is None and topic.original_answer is None
    assert topic.additional_answer_source_id == topic.current_correction_id
    assert topic.additional_answer == "An invented correction."
    assert _review(fixture).current_answers_reviewed


def test_hidden_old_answer_cannot_be_edited_but_exact_retry_still_acknowledges(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "command-hide", adherence="some")
    basis, key = _projection(fixture).basis, str(uuid4())
    _correct(fixture, question="adherence_barrier", basis=basis, key=key)
    _correct(fixture, question="adherence", answer="yes")
    with pytest.raises(VisitPrepInterviewConflict):
        _correct(fixture, question="adherence_barrier")
    retry = _correct(fixture, question="adherence_barrier", basis=basis, key=key)
    assert retry.disposition == "replayed" and len(_events(fixture)) == 2


@pytest.mark.parametrize("command", [correct_contribution, review_contribution])
def test_fresh_confirmation_evidence_is_required_without_disclosing_answers(
    monkeypatch, command
):
    fixture = _prepared(monkeypatch, "command-confirm")
    kwargs: dict[str, Any] = dict(
        access_token=fixture.access_token,
        first_name="Wrong invented recipient",
        role="patient",
        expected_basis=_projection(fixture).basis,
        idempotency_key=str(uuid4()),
    )
    if command is correct_contribution:
        kwargs["response"] = ResponseInput("story", "skipped")
    with pytest.raises(VisitPrepInterviewUnavailable):
        command(**kwargs)
    kwargs["first_name"] = "José"
    kwargs["role"] = "caregiver"
    with pytest.raises(VisitPrepInterviewUnavailable):
        command(**kwargs)
    assert _events(fixture) == []


def test_caregiver_can_correct_and_review_with_original_attribution(monkeypatch):
    fixture = _prepared(monkeypatch, "command-caregiver", role="caregiver")
    _correct(fixture, role="caregiver")
    receipt = _review(fixture, role="caregiver")
    assert receipt.current_answers_reviewed
    assert {event.actor_role for event in _events(fixture)} == {"caregiver"}


def test_skipped_answers_count_as_reviewed_missing_information_not_invented_facts(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "command-skip")
    _correct(fixture, answer=None)
    receipt = _review(fixture)
    assert receipt.current_answers_reviewed
    assert _topic(_projection(fixture), "story").answer is None
    assert _topic(_projection(fixture), "story").response_kind == "skipped"


def test_partial_original_interview_cannot_be_reviewed_as_complete(monkeypatch):
    fixture = _prepared(monkeypatch, "command-partial", complete=False)
    receipt = _correct(fixture, question="reason")
    assert not receipt.current_answers_complete
    with pytest.raises(VisitPrepInterviewConflict):
        _review(fixture)
    assert len(_events(fixture)) == 1


def test_revoked_or_rotated_credential_cannot_replay_old_patient_authority(monkeypatch):
    fixture = _prepared(monkeypatch, "command-rotation", complete=False)
    key, basis = str(uuid4()), _projection(fixture).basis
    _correct(fixture, key=key, basis=basis, question="reason")
    rotated = rotate_visit_prep_credential(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        visit_prep_id=fixture.prep_id,
        expected_credential_generation=1,
    )
    assert rotated.access_token is not None
    with pytest.raises(VisitPrepInterviewUnavailable):
        _correct(fixture, key=key, basis=basis, question="reason")
    current_retry = _correct(
        fixture, key=key, basis=basis, token=rotated.access_token, question="reason"
    )
    assert current_retry.disposition == "replayed"
    interviews.report_wrong_recipient(access_token=rotated.access_token)
    with pytest.raises(VisitPrepInterviewUnavailable):
        _correct(
            fixture, key=key, basis=basis, token=rotated.access_token, question="reason"
        )
    assert len(_events(fixture)) == 1


def test_encryption_failure_does_not_append_or_change_original_interview(monkeypatch):
    import app.services.visit_prep_contributions as service

    fixture = _prepared(monkeypatch, "command-encryption")

    def fail(_):
        raise EncryptionError

    monkeypatch.setattr(service, "encrypt_value", fail)
    with pytest.raises(VisitPrepInterviewUnavailable):
        _correct(fixture)
    assert _events(fixture) == []
    assert _projection(fixture).original_interview_completed


@pytest.mark.parametrize("answer", ["", "x" * 2001, "invented\x00answer"])
def test_invalid_correction_is_rejected_before_persistence(monkeypatch, answer):
    fixture = _prepared(monkeypatch, "command-invalid")
    with pytest.raises(VisitPrepInterviewValidation):
        _correct(fixture, answer=answer)
    assert _events(fixture) == []


@pytest.mark.parametrize(
    "gate", ["cohort", "public", "baa", "entitlement", "membership", "doctor"]
)
@pytest.mark.parametrize("operation", ["correct", "review", "read"])
def test_current_practice_authority_is_rechecked_for_each_command(
    monkeypatch, gate, operation
):
    fixture = _prepared(monkeypatch, f"command-authority-{gate}")
    basis = _projection(fixture).basis
    if gate in {"cohort", "public"}:
        monkeypatch.setattr(
            interviews.visit_prep_access,
            "VISIT_PREP_SETTINGS",
            VisitPrepSettings(
                enabled=True,
                public_api_enabled=gate != "public",
                enabled_doctor_ids=frozenset()
                if gate == "cohort"
                else frozenset({str(fixture.doctor.id)}),
            ),
        )
    else:
        with get_session() as session:
            now = datetime.now(UTC)
            if gate == "membership":
                table = SQLModel.metadata.tables["practice_member"]
                session.execute(
                    update(table)
                    .where(table.c.id == fixture.owner.id)
                    .values(
                        access_revoked_at=now,
                        access_revoked_by_ref="synthetic-command-offboarding",
                        access_revocation_reason="Synthetic contribution authority check",
                        access_records_retain_until=now + timedelta(days=365 * 8),
                    )
                )
            elif gate == "doctor":
                table = SQLModel.metadata.tables["doctor"]
                session.execute(
                    update(table)
                    .where(table.c.id == fixture.doctor.id)
                    .values(deleted_at=now)
                )
            else:
                table = SQLModel.metadata.tables["practice"]
                values = (
                    {"negotiated_baa_on_file": False}
                    if gate == "baa"
                    else {"billing_exempt": False}
                )
                session.execute(
                    update(table)
                    .where(table.c.id == fixture.practice.id)
                    .values(**values)
                )
            session.commit()
    with pytest.raises(VisitPrepInterviewUnavailable):
        if operation == "read":
            get_patient_contribution(
                access_token=fixture.access_token, first_name="José", role="patient"
            )
        elif operation == "review":
            _review(fixture, basis=basis)
        else:
            _correct(fixture, basis=basis)
    assert _events(fixture) == []
