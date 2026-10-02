"""Committed storage races and populated migration, not a model/UI exercise."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event

import pytest

from app.services import visit_prep_interviews as interviews
from app.services.visit_prep_synthesis_worker import run_one_build
from app.services.visit_preps import rotate_visit_prep_credential

from activemodel.session_manager import get_session, global_session
from app.models.visit_prep import VisitPrep
from app.models.visit_prep_clarification import (
    VisitPrepClarificationProposal,
)
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import select

from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.integration.visit_prep_interviews_lock_test import _fixture as lock_fixture
from tests.services.visit_prep_clarification_evidence_test import _decision, _proposal
from tests.services.visit_prep_contribution_commands_test import _correct
from tests.services.visit_prep_contribution_projection_test import _prepared
from tests.services.visit_prep_email_authority_model_test import _approval, _consent
from tests.services.visit_prep_email_payload_test import _stored_fixture
from tests.services.visit_prep_interviews_test import _Fixture
from tests.services.visit_prep_synthesis_builds_test import _request
from tests.services.visit_prep_synthesis_publication_test import _selected
from tests.services.visit_prep_synthesis_test import _brief, _fake


@pytest.mark.parametrize("stream", ["proposal", "decision"])
def test_concurrent_exact_replay_key_can_insert_only_once(monkeypatch, stream):
    fixture, proposal, row = _proposal(monkeypatch)
    if stream == "decision":
        row.save()
        _, first = _decision(proposal)
        _, second = _decision(proposal)
    else:
        first = row
        _, _, second = _proposal(monkeypatch, fixture)
        second.operation_id = first.operation_id
    barrier = Barrier(2)

    def insert(candidate):
        with get_session() as session:
            barrier.wait(timeout=5)
            try:
                session.add(candidate)
                session.commit()
                return "recorded"
            except DBAPIError as error:
                session.rollback()
                assert "uq_vp_clarification" in str(error.orig)
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(insert, candidate) for candidate in (first, second)]
        assert sorted(future.result(timeout=10) for future in futures) == [
            "conflict",
            "recorded",
        ]
    with get_session() as session:
        assert (
            len(
                session.exec(
                    select(type(first)).where(
                        type(first).visit_prep_id == fixture.prep_id
                    )
                ).all()
            )
            == 1
        )


@pytest.mark.parametrize("stream", ["proposal", "decision"])
@pytest.mark.parametrize("change", ["correction", "wrong_recipient", "rotate"])
def test_insert_wait_rechecks_committed_current_source_and_credential(
    monkeypatch, stream, change
):
    fixture = _prepared(monkeypatch, "clarification-wait", complete=False)
    fixture, proposal, row = _proposal(monkeypatch, fixture, question="reason")
    if stream == "decision":
        row.save()
        _, row = _decision(proposal)
    started = Event()
    name = "vp_clarification_wait"

    def insert():
        with get_session() as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_clarification_wait'")
            )
            started.set()
            session.add(row)
            session.commit()

    with global_session() as parent:
        parent.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id).with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(insert)
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(parent, name)
                if change == "correction":
                    _correct(
                        fixture,
                        question="reason",
                        answer="Invented intervening correction.",
                    )
                elif change == "rotate":
                    rotate_visit_prep_credential(
                        owner_practice_member_id=fixture.owner.id,
                        owner_doctor_id=fixture.doctor.id,
                        practice_id=fixture.practice.id,
                        visit_prep_id=fixture.prep_id,
                        expected_credential_generation=1,
                    )
                else:
                    interviews.report_wrong_recipient(access_token=fixture.access_token)
            finally:
                parent.commit()
            with pytest.raises(
                DBAPIError,
                match="basis is not current|stale|lineage|active current authority",
            ):
                future.result(timeout=10)
    with get_session() as session:
        assert (
            session.exec(
                select(type(row)).where(type(row).visit_prep_id == fixture.prep_id)
            ).all()
            == []
        )


@pytest.mark.parametrize("stream", ["proposal", "decision"])
def test_committed_truncate_is_rejected_by_named_storage_guard(monkeypatch, stream):
    _, proposal, row = _proposal(monkeypatch)
    row.save()
    if stream == "decision":
        _, row = _decision(proposal)
        row.save()
    with get_session() as session:
        session.commit()
        with pytest.raises(
            DBAPIError, match="clarification evidence cannot be truncated"
        ):
            session.execute(text(f"TRUNCATE public.{type(row).__tablename__} CASCADE"))
        session.rollback()
        assert session.get(type(row), row.id) is not None


def test_transaction_rollback_discards_proposal_without_modifying_original(monkeypatch):
    fixture, _, row = _proposal(monkeypatch)
    selected = _selected(fixture)
    with get_session() as session:
        session.add(row)
        session.flush()
        with get_session() as observer:
            assert observer.get(VisitPrepClarificationProposal, row.id) is None
        session.rollback()
    with get_session() as session:
        assert session.get(VisitPrepClarificationProposal, row.id) is None
    assert _selected(fixture) == selected


@pytest.mark.parametrize("stream", ["proposal", "decision"])
def test_expiry_after_actual_row_lock_uses_fresh_database_clock(monkeypatch, stream):
    expiry = datetime.now(UTC) + timedelta(seconds=6)
    scheduled = expiry - timedelta(hours=72)
    source = lock_fixture(
        monkeypatch,
        "clarification-expiry",
        created_at=scheduled - timedelta(minutes=1),
        scheduled_at=scheduled,
    )
    interviews.confirm_interview(
        access_token=source.access_token, first_name="Synthetic", role="patient"
    )
    interviews.submit_response(
        access_token=source.access_token,
        idempotency_key="4ef95047-3c3d-40f6-8aca-1fa4eea39492",
        response=interviews.ResponseInput(
            "reason", "answered", "Invented expiry answer."
        ),
    )
    fixture = _Fixture(
        source.doctor,
        source.practice,
        source.owner,
        source.visit_prep_id,
        source.access_token,
        scheduled,
    )
    _, proposal, row = _proposal(monkeypatch, fixture, question="reason")
    if stream == "decision":
        row.save()
        _, row = _decision(proposal)
    started = Event()

    def insert():
        with get_session() as session:
            session.execute(
                text("SET LOCAL application_name = 'vp_clarification_expiry'")
            )
            started.set()
            session.add(row)
            session.commit()

    with get_session() as parent:
        parent.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id).with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(insert)
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(parent, "vp_clarification_expiry")
                assert parent.execute(
                    text("SELECT clock_timestamp() < :expiry"), {"expiry": expiry}
                ).scalar_one()
                parent.execute(
                    text(
                        "SELECT pg_sleep(GREATEST(0, EXTRACT(EPOCH FROM (:expiry - clock_timestamp()))) + 0.1)"
                    ),
                    {"expiry": expiry},
                )
            finally:
                parent.rollback()
            with pytest.raises(DBAPIError, match="active current authority"):
                future.result(timeout=10)


def test_approved_synthetic_aggregate_cleanup_removes_evidence_together(monkeypatch):
    fixture, proposal, row = _proposal(monkeypatch)
    row.save()
    _, decision = _decision(proposal)
    decision.save()
    interviews.report_wrong_recipient(access_token=fixture.access_token)
    with get_session() as session:
        root = session.get(VisitPrep, fixture.prep_id)
        assert root is not None
        root.misdirected_disposition = "purge_approved"
        root.misdirected_disposition_at = datetime.now(UTC)
        root.misdirected_disposition_by_ref = "synthetic-security-operator"
        root.misdirected_disposition_until = root.misdirected_disposition_at
        session.add(root)
        session.commit()
    with get_session() as session:
        root = session.get(VisitPrep, fixture.prep_id)
        assert root is not None
        session.delete(root)
        session.commit()
    with get_session() as session:
        assert session.get(VisitPrep, fixture.prep_id) is None
        assert session.get(type(row), row.id) is None
        assert session.get(type(decision), decision.id) is None


def test_populated_expand_preserves_all_patient_synthesis_and_email_columns(
    monkeypatch,
):
    if os.environ.get("VISIT_PREP_MIGRATION_TARGET") != "10b7993b911d":
        pytest.skip("requires explicit owned pre-expand rehearsal")
    from alembic import command
    from alembic.config import Config

    fixture = _prepared(monkeypatch, "clarification-populated")
    selected = _selected(fixture)
    _fake(monkeypatch, _brief(selected).model_dump_json())
    _request(fixture)
    assert run_one_build() == "ready"
    _, root, _, _ = _stored_fixture()
    approval, _ = _approval(root)
    consent, _ = _consent(root)
    approval.save()
    consent.save()
    tables = (
        "visit_prep",
        "visit_prep_schedule_generation",
        "visit_prep_credential_generation",
        "visit_prep_question_response",
        "visit_prep_contribution_event",
        "visit_prep_synthesis_artifact",
        "visit_prep_synthesis_build",
        "agent_model_usage_event",
        "visit_prep_email_delivery",
        "visit_prep_email_approval_event",
        "visit_prep_email_consent_event",
    )

    def snapshot():
        with get_session() as session:
            return {
                name: session.execute(
                    text(f"SELECT to_jsonb(t)::text FROM public.{name} t ORDER BY id")
                )
                .scalars()
                .all()
                for name in tables
            }

    before = snapshot()
    command.upgrade(Config("alembic.ini"), "4f6c6893dbe7")
    assert snapshot() == before
    _, proposal, row = _proposal(monkeypatch, fixture)
    # Exercise this rung's original writer, not the latest ORM: source_turn_id
    # is deliberately absent until the separate patient-turn expand.
    assert row.source_turn_id is None
    with get_session() as session:
        session.execute(
            text("""
                INSERT INTO public.visit_prep_clarification_proposal
                (id, visit_prep_id, owner_practice_member_id,
                 question_set_version_id, question_id, credential_generation,
                 actor_role, basis_response_count, basis_event_sequence,
                 source_version, source_response_id, source_contribution_id,
                 operation_id, envelope_format_version, evidence_ciphertext,
                 payload_hmac_sha256)
                VALUES
                (:id, :visit_prep_id, :owner_practice_member_id,
                 :question_set_version_id, :question_id, :credential_generation,
                 :actor_role, :basis_response_count, :basis_event_sequence,
                 :source_version, :source_response_id, :source_contribution_id,
                 :operation_id, :envelope_format_version, :evidence_ciphertext,
                 :payload_hmac_sha256)
            """),
            {
                "id": row.id.uuid,
                "visit_prep_id": row.visit_prep_id.uuid,
                "owner_practice_member_id": row.owner_practice_member_id.uuid,
                "question_set_version_id": row.question_set_version_id.uuid,
                "question_id": row.question_id.uuid,
                "credential_generation": row.credential_generation,
                "actor_role": row.actor_role,
                "basis_response_count": row.basis_response_count,
                "basis_event_sequence": row.basis_event_sequence,
                "source_version": row.source_version,
                "source_response_id": row.source_response_id.uuid
                if row.source_response_id is not None
                else None,
                "source_contribution_id": row.source_contribution_id.uuid
                if row.source_contribution_id is not None
                else None,
                "operation_id": row.operation_id,
                "envelope_format_version": row.envelope_format_version,
                "evidence_ciphertext": row.evidence_ciphertext,
                "payload_hmac_sha256": row.payload_hmac_sha256,
            },
        )
        session.commit()
    _, decision = _decision(proposal)
    decision.save()
    assert _selected(fixture) == selected
