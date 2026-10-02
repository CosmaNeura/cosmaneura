"""Populated forward expansion; invoke only with migration target 65dee3dec2d0."""

import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

from app.services.visit_prep_contribution_contract import (
    ContributionBasis,
    contribution_payload_hmac,
)
from app.services.visit_prep_contributions import explain_contribution
from app.services.visit_prep_interview_contract import (
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

from tests.services.visit_prep_contribution_projection_test import (
    _prepared,
    _projection,
    _topic,
)


@pytest.mark.skipif(
    os.environ.get("VISIT_PREP_MIGRATION_TARGET") != "65dee3dec2d0",
    reason="Explicit owned populated-expand rehearsal only",
)
def test_existing_encrypted_answers_survive_forward_expand_and_new_explanation(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "populated-explanation-expand")
    with get_session() as session:
        before = {
            r.id: (r.answer_ciphertext, r.payload_hmac_sha256, r.recorded_at)
            for r in session.exec(
                select(VisitPrepQuestionResponse).where(
                    VisitPrepQuestionResponse.visit_prep_id == fixture.prep_id
                )
            ).all()
        }
        prep = session.get(VisitPrep, fixture.prep_id)
        question = session.exec(
            select(VisitPrepQuestion).where(
                VisitPrepQuestion.question_set_version_id
                == prep.question_set_version_id,
                VisitPrepQuestion.question_key == "story",
            )
        ).one()
        original = session.exec(
            select(VisitPrepQuestionResponse).where(
                VisitPrepQuestionResponse.visit_prep_id == prep.id,
                VisitPrepQuestionResponse.question_id == question.id,
            )
        ).one()
        table = VisitPrepContributionEvent.__table__
        old_columns = [c for c in table.columns if c.name != "target_revision_id"]
        # Use the actual pre-expansion schema. ORM reads would reference the
        # new nullable column before Alembic has added it.
        for sequence, kind in ((1, "correction"), (2, "review")):
            words = (
                "My retained invented story revision." if kind == "correction" else None
            )
            event = VisitPrepContributionEvent(
                visit_prep_id=prep.id,
                owner_practice_member_id=prep.owner_practice_member_id,
                question_set_version_id=prep.question_set_version_id,
                credential_generation=1,
                actor_role="patient",
                event_kind=kind,
                event_sequence=sequence,
                basis_event_sequence=sequence - 1,
                basis_response_count=4,
                response_id=original.id if words else None,
                response_kind="answered" if words else None,
                answer_ciphertext=encrypt_value(
                    _answer_envelope(kind="story", value=words)
                )
                if words
                else None,
                answer_format_version=1 if words else None,
                idempotency_key=str(uuid4()),
                payload_hmac_sha256=contribution_payload_hmac(
                    visit_prep_id=prep.id,
                    actor_role="patient",
                    event_kind=kind,
                    basis=ContributionBasis(4, sequence - 1),
                    original_response_id=original.id if words else None,
                    response=_CanonicalResponse("story", "answered", words)
                    if words
                    else None,
                ),
            )
            session.execute(
                table.insert().values(
                    **{
                        c.name: getattr(event, c.name)
                        for c in old_columns
                        if c.name
                        not in {"id", "created_at", "updated_at", "recorded_at"}
                    }
                )
            )
        session.commit()
        history_before = session.execute(
            select(*old_columns)
            .where(table.c.visit_prep_id == prep.id)
            .order_by(table.c.event_sequence)
        ).all()
    command.upgrade(Config("alembic.ini"), "head")
    with get_session() as session:
        after = {
            r.id: (r.answer_ciphertext, r.payload_hmac_sha256, r.recorded_at)
            for r in session.exec(
                select(VisitPrepQuestionResponse).where(
                    VisitPrepQuestionResponse.visit_prep_id == fixture.prep_id
                )
            ).all()
        }
        history_after = session.execute(
            select(*old_columns)
            .where(table.c.visit_prep_id == fixture.prep_id)
            .order_by(table.c.event_sequence)
        ).all()
    assert before == after and len(after) == 4
    assert history_before == history_after and len(history_after) == 2
    current = _projection(fixture)
    assert current.basis == ContributionBasis(4, 2) and current.current_answers_reviewed
    assert _topic(current, "story").answer == "My retained invented story revision."
    receipt = explain_contribution(
        access_token=fixture.access_token,
        first_name="José",
        role="patient",
        expected_basis=current.basis,
        idempotency_key=str(uuid4()),
        question_key="adherence",
        patient_words="Invented explanation after expansion.",
    )
    assert receipt.basis.event_sequence == 3
    topic = _topic(_projection(fixture), "adherence")
    assert topic.answer == topic.original_answer == "yes" and topic.explanation_words
