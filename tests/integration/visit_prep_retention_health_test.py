"""Committed retention observations during real custody and lifecycle races."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.configuration.visit_prep import VisitPrepSettings
from app.services import visit_prep_interviews as interviews
from app.services.visit_prep_retention_operations import runtime_health

from activemodel.session_manager import get_session, global_session
from app.models.practice import Practice
from app.models.visit_prep import VisitPrep, VisitPrepLegalHold
from sqlalchemy import func, text, update
from sqlmodel import col, select

from tests.integration.visit_prep_email_maintenance_test import (
    _retained as _email_retained,
)
from tests.services.visit_prep_contribution_commands_test import _correct, _review
from tests.services.visit_prep_email_enqueue_test import _rows
from tests.services.visit_prep_email_worker_test import _queued
from tests.services.visit_prep_interviews_test import (
    _confirm,
    _enable,
    _fixture,
    _respond,
)
from tests.services.visit_prep_model_test import _prep
from tests.services.visit_prep_retention_operations_test import (
    _clock,
    _delta,
    _hold,
    _retained,
)
from tests.services.visit_prep_synthesis_artifact_test import _artifact


def test_legal_hold_history_neither_duplicates_roots_nor_hides_overdue_cleanup(
    monkeypatch,
):
    appointment_at = datetime.now(UTC) + timedelta(days=20)
    _clock(monkeypatch, appointment_at + timedelta(days=91))
    before = runtime_health()
    fixture = _fixture(
        monkeypatch, "retention-held-unused", scheduled_at=appointment_at
    )
    hold = _hold(fixture)
    # This must be a new real transaction: a nested service-test transaction
    # started before placement cannot supply a legitimate later release time.
    with global_session() as session:
        session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id).with_for_update()
        ).one()
        session.execute(
            update(VisitPrepLegalHold)
            .where(col(VisitPrepLegalHold.id) == hold.id)
            .values(
                released_at=func.transaction_timestamp(),
                released_by_ref="actor:invented-retention-release",
                release_reason="synthetic_review_complete",
            )
        )
        session.commit()
    _hold(fixture, generation=2)
    health = runtime_health()
    _delta(
        before,
        health,
        preparations=1,
        unused=1,
        unused_expired_unrevoked=1,
        unused_cleanup_due=1,
        unused_hard_ceiling_reached=1,
        unused_cleanup_held=1,
        active_legal_holds=1,
    )
    with get_session() as session:
        assert (
            session.execute(
                text(
                    "SELECT count(*) FROM visit_prep_legal_hold WHERE visit_prep_id = :id"
                ),
                {"id": fixture.prep_id.uuid},
            ).scalar_one()
            == 2
        )
    assert health["status"] == "degraded"
    saved = _retained(fixture)
    assert len(saved["visit_prep_legal_hold"]) == 2
    assert runtime_health() == health and _retained(fixture) == saved


@pytest.mark.parametrize("commit", [True, False])
def test_health_does_not_wait_for_or_disclose_uncommitted_confirmation(
    monkeypatch, commit
):
    fixture = _fixture(monkeypatch, "retention-confirmation-transaction")
    before = runtime_health()
    with global_session() as parent:
        parent.exec(
            select(Practice).where(Practice.id == fixture.practice.id).with_for_update()
        ).one()
        parent.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id).with_for_update()
        ).one()
        # Existing guarded state transition, synthetic evidence only. Its durable
        # record is indistinguishable from a normal confirmation after commit.
        parent.execute(
            update(VisitPrep)
            .where(col(VisitPrep.id) == fixture.prep_id)
            .values(
                lifecycle_state="confirmed",
                confirmed_at=func.clock_timestamp(),
                confirmed_by_role="patient",
                retention_policy_version="clinical.v1",
                retain_until=fixture.scheduled_at.replace(
                    year=fixture.scheduled_at.year + 7
                ),
            )
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            observed = executor.submit(runtime_health).result(timeout=5)
        _delta(before, observed)
        parent.commit() if commit else parent.rollback()
    _delta(
        before,
        runtime_health(),
        unused=-1 if commit else 0,
        confirmed_retained=1 if commit else 0,
    )


@pytest.mark.parametrize("commit", [True, False])
def test_health_does_not_wait_for_or_disclose_uncommitted_hold(monkeypatch, commit):
    fixture = _fixture(monkeypatch, "retention-hold-transaction")
    before = runtime_health()
    with global_session() as parent:
        parent.exec(
            select(VisitPrep).where(VisitPrep.id == fixture.prep_id).with_for_update()
        ).one()
        parent.add(
            VisitPrepLegalHold(
                visit_prep_id=fixture.prep_id,
                owner_practice_member_id=fixture.owner.id,
                generation=1,
                placed_at=datetime.now(UTC),
                placed_by_ref="actor:invented-retention-transaction",
                placement_reason="synthetic_review",
            )
        )
        parent.flush()
        with ThreadPoolExecutor(max_workers=1) as executor:
            observed = executor.submit(runtime_health).result(timeout=5)
        _delta(before, observed)
        parent.commit() if commit else parent.rollback()
    _delta(before, runtime_health(), active_legal_holds=1 if commit else 0)


def test_actual_quarantine_while_custody_locked_is_atomic_to_health(monkeypatch):
    fixture = _fixture(monkeypatch, "retention-quarantine-atomic")
    _confirm(fixture)
    _respond(fixture, "reason", answer="Invented wrong-recipient words.")
    before = runtime_health()
    commit = interviews._commit
    observations = []

    def observe_before_real_commit(session):
        # The actual command has flushed its quarantine and holds custody. A
        # separate connection must see only prior committed clinical state.
        with ThreadPoolExecutor(max_workers=1) as executor:
            observed = executor.submit(runtime_health).result(timeout=5)
        _delta(before, observed)
        observations.append(observed)
        commit(session)

    monkeypatch.setattr(interviews, "_commit", observe_before_real_commit)
    interviews.report_wrong_recipient(access_token=fixture.access_token)
    assert len(observations) == 1
    _delta(
        before, runtime_health(), confirmed_retained=-1, quarantined_pending_review=1
    )
    saved = _retained(fixture)
    assert saved["visit_prep_question_response"]
    assert _retained(fixture) == saved


def test_report_preserves_completed_corrected_reviewed_brief_and_email_evidence(
    monkeypatch,
):
    baseline = runtime_health()
    fixture = _queued("retention-complete-evidence")
    scope, root, _, arguments = fixture
    _enable(monkeypatch, scope.doctor)
    patient = SimpleNamespace(
        prep_id=root.id,
        access_token=arguments["access_token"],
        owner=scope.owner,
        doctor=scope.doctor,
        practice=scope.practice,
    )
    interviews.confirm_interview(
        access_token=patient.access_token, first_name="José", role="patient"
    )
    for key, answer in (
        ("reason", "Invented priority."),
        ("medications", None),
        ("adherence", "yes"),
        ("story", "Invented original story."),
    ):
        interviews.submit_response(
            access_token=patient.access_token,
            idempotency_key=str(uuid4()),
            response=interviews.ResponseInput(
                key, "answered" if answer is not None else "skipped", answer
            ),
        )
    interviews.complete_interview(access_token=patient.access_token)
    _correct(patient, answer="Invented corrected story.")
    _review(patient)
    _artifact(patient).save()
    _hold(patient)
    before, email_before = _email_retained(fixture), _rows(fixture)[0].model_dump_json()
    scope.practice.negotiated_baa_on_file = False
    scope.practice.save()
    monkeypatch.setattr(
        interviews.visit_prep_access, "VISIT_PREP_SETTINGS", VisitPrepSettings()
    )
    health = runtime_health()
    _delta(baseline, health, preparations=1, confirmed_retained=1, active_legal_holds=1)
    assert all(
        before[key]
        for key in (
            "visit_prep_question_response",
            "visit_prep_contribution_event",
            "visit_prep_synthesis_artifact",
            "visit_prep_legal_hold",
            "visit_prep_email_consent_event",
            "approval",
        )
    )
    assert _email_retained(fixture) == before
    assert _rows(fixture)[0].model_dump_json() == email_before


def test_incomplete_committed_lineage_is_visible_not_an_invented_healthy_zero():
    before = runtime_health()
    # Existing real model permits an expand-layer aggregate before its schedule
    # and credential are installed. Do not fabricate a matching lineage or
    # silently drop it from operational counts.
    prep = _prep().save()
    _delta(
        before, runtime_health(), preparations=1, unused=1, incomplete_current_lineage=1
    )
    with get_session() as session:
        retained = session.get(VisitPrep, prep.id)
        assert (
            retained is not None
            and retained.recipient_snapshot_ciphertext
            == prep.recipient_snapshot_ciphertext
        )


def test_actual_incident_deadline_crossing_changes_observation_without_erasure(
    monkeypatch,
):
    fixture = _fixture(monkeypatch, "retention-real-incident-deadline")
    _confirm(fixture)
    _respond(fixture, "reason", answer="Invented quarantined words retained unchanged.")
    interviews.report_wrong_recipient(access_token=fixture.access_token)
    with global_session() as session:
        until = session.execute(
            text("SELECT clock_timestamp() + interval '2 seconds'")
        ).scalar_one()
        session.execute(
            update(VisitPrep)
            .where(col(VisitPrep.id) == fixture.prep_id)
            .values(
                misdirected_disposition="retain_until",
                misdirected_disposition_at=func.clock_timestamp(),
                misdirected_disposition_by_ref="actor:invented-incident-owner",
                misdirected_disposition_until=until,
            )
        )
        session.commit()
    before, saved = runtime_health(), _retained(fixture)
    with get_session() as session:
        now = session.execute(text("SELECT clock_timestamp()")).scalar_one()
        seconds = max(0, (until - now).total_seconds()) + 0.05
        assert seconds < 3
        session.execute(text("SELECT pg_sleep(:seconds)"), {"seconds": seconds})
        assert session.execute(text("SELECT clock_timestamp()")).scalar_one() >= until
    _delta(before, runtime_health(), quarantined_disposition_elapsed=1)
    assert _retained(fixture) == saved
