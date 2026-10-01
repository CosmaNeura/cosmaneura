"""Revised conditional-topic capture with invented patient-reported text only."""

from dataclasses import replace
from typing import Any, Literal, cast
from uuid import uuid4

import pytest
from typeid import TypeID

from app.services import visit_prep_interviews as interviews
from app.services.visit_prep_contribution_contract import (
    ContributionBasis,
    contribution_payload_hmac,
)
from app.services.visit_prep_contributions import _load_contribution
from app.services.visit_prep_interview_contract import (
    VisitPrepInterviewConflict,
    VisitPrepInterviewUnavailable,
    VisitPrepInterviewValidation,
    _CanonicalResponse,
)
from app.utils.encryption import decrypt_value

from activemodel.session_manager import get_session
from activemodel.types import TypeIDType
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_contribution import VisitPrepContributionEvent
from app.models.visit_prep_question_set import VisitPrepQuestion
from app.models.visit_prep_response import VisitPrepQuestionResponse
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError
from sqlmodel import SQLModel, select

from tests.services.visit_prep_contribution_commands_test import (
    _correct,
    _events,
    _review,
)
from tests.services.visit_prep_contribution_projection_test import (
    _prepared,
    _projection,
    _topic,
)


def _revealed(monkeypatch, label, *, role: Literal["patient", "caregiver"] = "patient"):
    fixture = _prepared(monkeypatch, label, role=role)
    _correct(fixture, question="adherence", answer="some", role=role)
    return fixture


@pytest.mark.parametrize("answer", ["An invented newly reported barrier.", None])
def test_new_conditional_source_preserves_original_interview_and_can_be_reviewed(
    monkeypatch, answer
):
    fixture = _revealed(monkeypatch, "revealed-answer")
    with get_session() as session:
        prep = session.get(VisitPrep, fixture.prep_id)
        assert prep is not None
        completed_at = prep.completed_at
        originals = session.exec(
            select(VisitPrepQuestionResponse).where(
                VisitPrepQuestionResponse.visit_prep_id == fixture.prep_id
            )
        ).all()
        original_bytes = {row.id: row.answer_ciphertext for row in originals}
    receipt = _correct(fixture, question="adherence_barrier", answer=answer)
    assert receipt.basis == ContributionBasis(4, 2)
    current = _projection(fixture)
    topic = _topic(current, "adherence_barrier")
    assert topic.original_response_id is None and topic.original_recorded_at is None
    assert topic.original_answer is None and topic.original_response_kind is None
    assert topic.additional_answer_source_id == topic.current_correction_id
    assert topic.additional_answer == answer
    assert topic.additional_response_kind == ("answered" if answer else "skipped")
    assert topic.additional_recorded_at == topic.recorded_at
    assert current.current_answers_complete and current.original_interview_completed
    assert _review(fixture).current_answers_reviewed
    event = next(row for row in _events(fixture) if row.event_kind == "topic_answer")
    assert event.response_id is None and event.question_id is not None
    if answer:
        assert event.answer_ciphertext and answer not in event.answer_ciphertext
        assert answer in decrypt_value(event.answer_ciphertext)
    else:
        assert event.answer_ciphertext is None and event.answer_format_version is None
    with get_session() as session:
        prep = session.get(VisitPrep, fixture.prep_id)
        assert prep is not None and prep.completed_at == completed_at
        rows = session.exec(
            select(VisitPrepQuestionResponse).where(
                VisitPrepQuestionResponse.visit_prep_id == fixture.prep_id
            )
        ).all()
        assert {row.id: row.answer_ciphertext for row in rows} == original_bytes


def test_additional_wording_stays_immutable_through_revision_hide_and_reveal(
    monkeypatch,
):
    fixture = _revealed(monkeypatch, "revealed-revision")
    basis, key = _projection(fixture).basis, str(uuid4())
    _correct(
        fixture,
        question="adherence_barrier",
        key=key,
        basis=basis,
        answer="An invented first barrier.",
    )
    first = _topic(_projection(fixture), "adherence_barrier")
    _review(fixture)
    _correct(
        fixture, question="adherence_barrier", answer="An invented revised barrier."
    )
    revised = _topic(_projection(fixture), "adherence_barrier")
    assert revised.additional_answer_source_id == first.additional_answer_source_id
    assert revised.additional_answer == first.answer
    assert revised.additional_recorded_at == first.recorded_at
    assert revised.answer == "An invented revised barrier."
    assert revised.current_correction_id != first.current_correction_id
    _correct(fixture, question="adherence", answer="yes")
    hidden = _topic(_projection(fixture), "adherence_barrier")
    assert not hidden.applicable and hidden.answer == revised.answer
    with pytest.raises(VisitPrepInterviewConflict):
        _correct(fixture, question="adherence_barrier")
    replay = _correct(
        fixture,
        question="adherence_barrier",
        key=key,
        basis=basis,
        answer="An invented first barrier.",
    )
    assert replay.disposition == "replayed"
    _correct(fixture, question="adherence", answer="no")
    restored = _topic(_projection(fixture), "adherence_barrier")
    assert restored.applicable and restored.answer == revised.answer
    assert restored.additional_answer_source_id == first.additional_answer_source_id
    assert not _projection(fixture).current_answers_reviewed
    assert _review(fixture).current_answers_reviewed


def test_missing_original_topics_cannot_be_bypassed_as_additional_answers(monkeypatch):
    fixture = _prepared(monkeypatch, "revealed-unfinished", complete=False)
    for key in ("medications", "adherence", "adherence_barrier", "story"):
        with pytest.raises(VisitPrepInterviewConflict):
            _correct(fixture, question=key)
    assert not _events(fixture)


def test_additional_retry_binds_exact_payload_and_stale_basis(monkeypatch):
    fixture = _revealed(monkeypatch, "revealed-idempotency")
    basis, key = _projection(fixture).basis, str(uuid4())
    _correct(fixture, question="adherence_barrier", answer=None, basis=basis, key=key)
    first = _topic(_projection(fixture), "adherence_barrier")
    assert (
        first.additional_response_kind == "skipped" and first.additional_answer is None
    )
    for changes in (
        {"answer": "A divergent invented barrier."},
        {"basis": _projection(fixture).basis},
        {"question": "story"},
    ):
        with pytest.raises(VisitPrepInterviewConflict):
            args: dict[str, Any] = (
                dict(question="adherence_barrier", answer=None, basis=basis, key=key)
                | changes
            )
            _correct(
                fixture,
                **args,
            )
    with pytest.raises(VisitPrepInterviewConflict):
        _correct(fixture, question="adherence_barrier", basis=basis)
    _correct(fixture, question="adherence_barrier", answer="A later invented barrier.")
    current = _topic(_projection(fixture), "adherence_barrier")
    assert current.answer == "A later invented barrier."
    assert current.additional_answer_source_id == first.additional_answer_source_id
    assert (
        current.additional_response_kind == "skipped"
        and current.additional_answer is None
    )


def test_additional_encryption_failure_does_not_append_partial_source(monkeypatch):
    import app.services.visit_prep_contributions as contributions
    from app.utils.encryption import EncryptionError

    fixture = _revealed(monkeypatch, "revealed-encryption")

    def fail(_value):
        raise EncryptionError()

    monkeypatch.setattr(contributions, "encrypt_value", fail)
    with pytest.raises(VisitPrepInterviewUnavailable) as rejected:
        _correct(fixture, question="adherence_barrier")
    assert "private" not in str(rejected.value) and len(_events(fixture)) == 1


def test_caregiver_additional_report_retains_attribution_and_current_authority(
    monkeypatch,
):
    fixture = _revealed(monkeypatch, "revealed-caregiver", role="caregiver")
    basis, key = _projection(fixture).basis, str(uuid4())
    _correct(
        fixture, question="adherence_barrier", role="caregiver", basis=basis, key=key
    )
    assert _review(fixture, role="caregiver").current_answers_reviewed
    assert all(event.actor_role == "caregiver" for event in _events(fixture))
    with pytest.raises(VisitPrepInterviewUnavailable):
        _correct(fixture, question="adherence_barrier", basis=basis, key=key)
    interviews.report_wrong_recipient(access_token=fixture.access_token)
    with pytest.raises(VisitPrepInterviewUnavailable):
        _correct(
            fixture,
            question="adherence_barrier",
            role="caregiver",
            basis=basis,
            key=key,
        )
    with get_session() as session:
        assert (
            len(
                session.exec(
                    select(VisitPrepContributionEvent).where(
                        VisitPrepContributionEvent.visit_prep_id == fixture.prep_id
                    )
                ).all()
            )
            == 3
        )


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"question_id": None}, "additional topic lineage"),
        ({"response_id": TypeID("visit_prep_response")}, "event_shape"),
        ({"response_kind": "skipped"}, "event_shape"),
        ({"answer_format_version": None}, "event_shape"),
        ({"answer_ciphertext": ""}, "event_shape"),
    ],
)
def test_additional_storage_rejects_malformed_event_without_appending(
    monkeypatch, changes, match
):
    fixture = _revealed(monkeypatch, "revealed-shape")
    _correct(fixture, question="adherence_barrier")
    saved = next(row for row in _events(fixture) if row.event_kind == "topic_answer")
    values = (
        saved.model_dump()
        | {
            "id": TypeID("visit_prep_contribution"),
            "event_sequence": 3,
            "basis_event_sequence": 2,
            "idempotency_key": str(uuid4()),
            "payload_hmac_sha256": "a" * 64,
            "answer_ciphertext": saved.answer_ciphertext,
        }
        | changes
    )
    with get_session() as session:
        with pytest.raises(DBAPIError, match=match):
            with session.begin_nested():
                session.add(VisitPrepContributionEvent(**values))
                session.flush()
    assert len(_events(fixture)) == 2


def test_additional_topic_must_belong_to_exact_version_and_have_no_original_row(
    monkeypatch,
):
    fixture = _revealed(monkeypatch, "revealed-lineage")
    _correct(fixture, question="adherence_barrier")
    saved = next(row for row in _events(fixture) if row.event_kind == "topic_answer")
    # Fixture cohort configuration changes per owner; capture the first source
    # before creating a second owner, then inspect direct DB lineage only.
    other = _prepared(monkeypatch, "revealed-other", adherence="some")
    with get_session() as session:
        prep = session.get(VisitPrep, other.prep_id)
        assert prep is not None
        wrong = session.exec(
            select(VisitPrepQuestion).where(
                VisitPrepQuestion.question_set_version_id
                == prep.question_set_version_id,
                VisitPrepQuestion.question_key == "adherence_barrier",
            )
        ).one()
        own_prep = session.get(VisitPrep, fixture.prep_id)
        assert own_prep is not None
        own = session.exec(
            select(VisitPrepQuestion).where(
                VisitPrepQuestion.question_set_version_id
                == own_prep.question_set_version_id,
                VisitPrepQuestion.question_key == "reason",
            )
        ).one()
    for question_id in (wrong.id, own.id):
        values = saved.model_dump() | {
            "id": TypeID("visit_prep_contribution"),
            "question_id": question_id,
            "event_sequence": 3,
            "basis_event_sequence": 2,
            "idempotency_key": str(uuid4()),
            "payload_hmac_sha256": "a" * 64,
            "answer_ciphertext": saved.answer_ciphertext,
        }
        with get_session() as session:
            with pytest.raises(DBAPIError, match="additional topic lineage"):
                with session.begin_nested():
                    session.add(VisitPrepContributionEvent(**values))
                    session.flush()


@pytest.mark.parametrize(
    "corruption", ["target", "operation", "ciphertext", "fingerprint"]
)
def test_additional_source_corruption_fails_closed(monkeypatch, corruption):
    fixture = _revealed(monkeypatch, "revealed-corrupt")
    _correct(fixture, question="adherence_barrier")
    saved = next(row for row in _events(fixture) if row.event_kind == "topic_answer")
    with get_session() as session:
        authorized = interviews._lock_authorized_interview(
            session, access_token=fixture.access_token
        )
        event = session.get(VisitPrepContributionEvent, saved.id)
        assert event is not None
        if corruption == "target":
            event.question_id = cast(TypeIDType, TypeID("visit_prep_question"))
        elif corruption == "operation":
            event.event_kind = "review"
        elif corruption == "ciphertext":
            event.answer_ciphertext = "invalid invented envelope"
        else:
            event.payload_hmac_sha256 = "0" * 64
        with session.no_autoflush, pytest.raises(VisitPrepInterviewUnavailable):
            _load_contribution(session, visit_prep=authorized.visit_prep)
        session.rollback()


def test_additional_event_remains_append_only_under_direct_update_delete(monkeypatch):
    fixture = _revealed(monkeypatch, "revealed-immutable")
    _correct(fixture, question="adherence_barrier")
    saved = next(row for row in _events(fixture) if row.event_kind == "topic_answer")
    table = SQLModel.metadata.tables["visit_prep_contribution_event"]
    for statement in (
        update(table).where(table.c.id == saved.id).values(question_id=None),
        delete(table).where(table.c.id == saved.id),
    ):
        with get_session() as session:
            with pytest.raises(DBAPIError):
                with session.begin_nested():
                    session.execute(statement)
    assert len(_events(fixture)) == 2


def test_additional_fingerprint_separates_target_and_preserves_old_v1_hash_shape():
    import json

    from app.utils.encryption import pseudonymous_digest

    kwargs: dict[str, Any] = dict(
        visit_prep_id=TypeID("visit_prep"),
        actor_role="patient",
        event_kind="topic_answer",
        basis=ContributionBasis(4, 1),
        question_id=TypeID("visit_prep_question"),
        response=_CanonicalResponse(
            "adherence_barrier", "answered", "An invented barrier."
        ),
    )
    first = contribution_payload_hmac(**kwargs)
    assert first != contribution_payload_hmac(
        **(kwargs | {"question_id": TypeID("visit_prep_question")})
    )
    assert first != contribution_payload_hmac(
        **(
            kwargs
            | {
                "response": replace(
                    kwargs["response"], answer="Another invented barrier."
                )
            }
        )
    )
    with pytest.raises(VisitPrepInterviewValidation):
        contribution_payload_hmac(
            **(kwargs | {"original_response_id": TypeID("visit_prep_response")})
        )
    original_id = TypeID("visit_prep_response")
    correction = kwargs | {
        "event_kind": "correction",
        "question_id": None,
        "original_response_id": original_id,
    }
    old_payload = dict(
        version=1,
        visit_prep_id=str(kwargs["visit_prep_id"]),
        actor_role="patient",
        event_kind="correction",
        basis_response_count=4,
        basis_event_sequence=1,
        original_response_id=str(original_id),
        question_key="adherence_barrier",
        response_kind="answered",
        answer="An invented barrier.",
    )
    assert contribution_payload_hmac(**correction) == pseudonymous_digest(
        json.dumps(
            old_payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ),
        context="visit-prep-contribution-payload-v1",
    )
