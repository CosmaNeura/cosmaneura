"""Actual populated expand rehearsal in an owned database started at 4ad34f59136c."""

import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

from activemodel.session_manager import get_session
from sqlalchemy import text

from tests.services.visit_prep_contribution_commands_test import _correct, _review
from tests.services.visit_prep_contribution_model_test import _event
from tests.services.visit_prep_contribution_projection_test import (
    _prepared,
    _projection,
)


@pytest.mark.skipif(
    os.environ.get("VISIT_PREP_MIGRATION_TARGET") != "4ad34f59136c",
    reason="requires the owned runner's explicit pre-expand migration target",
)
def test_populated_upgrade_retains_encrypted_v1_events_and_accepts_new_topic(
    monkeypatch,
):
    fixture = _prepared(monkeypatch, "revealed-populated-upgrade")
    # Current ORM has the new nullable column. Seed prior event rows explicitly
    # through the actual pre-expand table and its original guards, not new ORM.
    event = _event(fixture, responses=4)
    with get_session() as session:
        question_response = session.execute(
            text(
                "SELECT id FROM public.visit_prep_question_response "
                "WHERE visit_prep_id=:prep ORDER BY submission_sequence LIMIT 1"
            ),
            {"prep": fixture.prep_id.uuid},
        ).scalar_one()
        # Compute the existing v1 service fingerprint against the actual source.
        from app.services.visit_prep_contribution_contract import (
            ContributionBasis,
            contribution_payload_hmac,
        )
        from app.services.visit_prep_interview_contract import _CanonicalResponse

        fingerprint = contribution_payload_hmac(
            visit_prep_id=fixture.prep_id,
            actor_role="patient",
            event_kind="correction",
            basis=ContributionBasis(4, 0),
            original_response_id=event.response_id,
            response=_CanonicalResponse(
                "reason", "answered", "My corrected invented reason."
            ),
        )
        params = dict(
            prep=fixture.prep_id.uuid,
            owner=fixture.owner.id.uuid,
            version=event.question_set_version_id.uuid,
            response=question_response,
            event_id=uuid4(),
            key=str(uuid4()),
            fingerprint=fingerprint,
            ciphertext=event.answer_ciphertext,
        )
        session.execute(
            text("""
            INSERT INTO public.visit_prep_contribution_event
            (id, visit_prep_id, owner_practice_member_id, question_set_version_id,
             credential_generation, actor_role, event_kind, event_sequence,
             basis_event_sequence, basis_response_count, response_id, response_kind,
             answer_ciphertext, answer_format_version, idempotency_key, payload_hmac_sha256)
            VALUES (:event_id, :prep, :owner, :version, 1, 'patient', 'correction', 1,
              0, 4, :response, 'answered', :ciphertext, 1, :key, :fingerprint)
        """),
            params,
        )
        session.execute(
            text("""
            INSERT INTO public.visit_prep_contribution_event
            (id, visit_prep_id, owner_practice_member_id, question_set_version_id,
             credential_generation, actor_role, event_kind, event_sequence,
             basis_event_sequence, basis_response_count, idempotency_key, payload_hmac_sha256)
            VALUES (:event_id, :prep, :owner, :version, 1, 'patient', 'review', 2,
              1, 4, :key, :fingerprint)
        """),
            params
            | dict(
                event_id=uuid4(),
                key=str(uuid4()),
                fingerprint=contribution_payload_hmac(
                    visit_prep_id=fixture.prep_id,
                    actor_role="patient",
                    event_kind="review",
                    basis=ContributionBasis(4, 1),
                ),
            ),
        )
        session.commit()
        before_responses = session.execute(
            text(
                "SELECT * FROM public.visit_prep_question_response WHERE visit_prep_id=:prep ORDER BY submission_sequence"
            ),
            {"prep": fixture.prep_id.uuid},
        ).all()
        before_events = (
            session.execute(
                text(
                    "SELECT * FROM public.visit_prep_contribution_event WHERE visit_prep_id=:prep ORDER BY event_sequence"
                ),
                {"prep": fixture.prep_id.uuid},
            )
            .mappings()
            .all()
        )
        assert (
            session.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == "4ad34f59136c"
        )
    command.upgrade(Config("alembic.ini"), "fe53eb110e32")
    with get_session() as session:
        assert (
            session.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == "fe53eb110e32"
        )
        after = (
            session.execute(
                text(
                    "SELECT * FROM public.visit_prep_contribution_event WHERE visit_prep_id=:prep ORDER BY event_sequence"
                ),
                {"prep": fixture.prep_id.uuid},
            )
            .mappings()
            .all()
        )
        assert [dict(row) for row in before_events] == [
            {key: value for key, value in row.items() if key != "question_id"}
            for row in after
        ]
        assert all(row["question_id"] is None for row in after)
        assert (
            session.execute(
                text(
                    "SELECT * FROM public.visit_prep_question_response WHERE visit_prep_id=:prep ORDER BY submission_sequence"
                ),
                {"prep": fixture.prep_id.uuid},
            ).all()
            == before_responses
        )
    assert _projection(fixture).current_answers_reviewed
    _correct(fixture, question="adherence", answer="some")
    added = _correct(
        fixture,
        question="adherence_barrier",
        answer="An invented post-upgrade barrier.",
    )
    assert added.current_answers_complete and not added.current_answers_reviewed
    assert _review(fixture).current_answers_reviewed
