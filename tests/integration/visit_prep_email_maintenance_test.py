"""Actual secret maintenance with retained synthetic evidence and owner fences."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.services.visit_prep_email_worker as worker
from app.services import visit_prep_interviews as interviews
from app.services.visit_prep_email_enqueue import enqueue_email_invitation
from app.services.visit_prep_email_worker import (
    EmailSubmissionClaim,
    claim_email_invitation,
    dispatch_email_claim,
    maintain_email_secrets,
)
from app.services.visit_preps import reschedule_visit_prep

from activemodel.session_manager import get_session, global_session
from app.models.authority import AuthorityAuditEvent
from app.models.practice import Practice
from app.models.visit_prep import (
    VisitPrep,
    VisitPrepCredentialGeneration,
    VisitPrepLegalHold,
)
from sqlalchemy import func, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import col, select

from tests.integration.visit_prep_contribution_lock_test import _observe_waiting_lock
from tests.services.visit_prep_contribution_commands_test import _correct, _review
from tests.services.visit_prep_contribution_projection_test import _projection
from tests.services.visit_prep_email_enqueue_test import _ready, _rows
from tests.services.visit_prep_email_evidence_test import _evidence, _store_consent
from tests.services.visit_prep_email_worker_test import _events, _options, _queued
from tests.services.visit_prep_interviews_test import _enable
from tests.services.visit_prep_synthesis_artifact_test import _artifact


def _short_appointment(label):
    fixture = _ready(label)
    scope, root, _, arguments = fixture
    at = datetime.now(UTC) + timedelta(seconds=3)
    result = reschedule_visit_prep(
        owner_practice_member_id=scope.owner.id,
        owner_doctor_id=scope.doctor.id,
        practice_id=scope.practice.id,
        visit_prep_id=root.id,
        expected_credential_generation=1,
        expected_schedule_generation=1,
        scheduled_at=at,
        timezone_name="UTC",
    )
    assert result.access_token is not None
    arguments.update(
        access_token=result.access_token,
        expected_credential_generation=2,
        operation_id=str(uuid4()),
    )
    enqueue_email_invitation(**arguments)
    return fixture, at


def _wait_until(at):
    with get_session() as session:
        now = session.execute(text("SELECT clock_timestamp()")).scalar_one()
        seconds = max(0, (at - now).total_seconds()) + 0.05
        assert seconds < 5
        session.execute(text("SELECT pg_sleep(:seconds)"), {"seconds": seconds})
        assert session.execute(text("SELECT clock_timestamp()")).scalar_one() >= at


def _retained(fixture):
    # Fixed test-owned table names; compare every column, including ciphertext.
    tables = (
        "visit_prep",
        "visit_prep_schedule_generation",
        "visit_prep_credential_generation",
        "visit_prep_question_response",
        "visit_prep_contribution_event",
        "visit_prep_email_consent_event",
        "visit_prep_legal_hold",
        "visit_prep_synthesis_artifact",
    )
    with get_session() as session:
        snapshots = {}
        for table in tables:
            key = "id" if table == "visit_prep" else "visit_prep_id"
            snapshots[table] = (
                session.execute(
                    text(
                        f"SELECT to_jsonb(t)::text FROM public.{table} AS t "
                        f"WHERE {key} = :id ORDER BY id"
                    ),
                    {"id": fixture[1].id.uuid},
                )
                .scalars()
                .all()
            )
        snapshots["approval"] = (
            session.execute(
                text(
                    "SELECT to_jsonb(t)::text FROM public.visit_prep_email_approval_event AS t "
                    "WHERE owner_practice_member_id = :id ORDER BY id"
                ),
                {"id": fixture[1].owner_practice_member_id.uuid},
            )
            .scalars()
            .all()
        )
        return snapshots


def test_passed_appointment_clears_queued_secret_before_payload_expiry(monkeypatch):
    fixture, appointment_at = _short_appointment("maintenance-past-appointment")
    scope, root, _, arguments = fixture
    _enable(monkeypatch, scope.doctor)
    interviews.confirm_interview(
        access_token=arguments["access_token"], first_name="José", role="patient"
    )
    interviews.submit_response(
        access_token=arguments["access_token"],
        idempotency_key=str(uuid4()),
        response=interviews.ResponseInput("reason", "answered", "Invented concern."),
    )
    VisitPrepLegalHold(
        visit_prep_id=root.id,
        owner_practice_member_id=root.owner_practice_member_id,
        generation=1,
        placed_at=datetime.now(UTC),
        placed_by_ref="actor:synthetic-maintenance-test",
        placement_reason="synthetic_review",
    ).save()
    scope.practice.negotiated_baa_on_file = False
    scope.practice.save()
    before, original = _retained(fixture), _rows(fixture)[0]
    assert original.payload_ciphertext is not None
    _wait_until(appointment_at)
    assert original.expires_at > datetime.now(UTC)
    assert maintain_email_secrets() == {"suppressed": 1, "uncertain": 0, "status": "ok"}
    row = _rows(fixture)[0]
    assert row.state == "suppressed" and row.outcome_code == "obsolete_context"
    assert row.payload_ciphertext is None and row.submitted_at is None
    assert row.authorization_ciphertext == original.authorization_ciphertext
    assert _retained(fixture) == before
    assert maintain_email_secrets() == {"suppressed": 0, "uncertain": 0, "status": "ok"}


def test_future_quiet_invitation_is_not_erased_by_maintenance():
    fixture = _queued("maintenance-quiet-future")
    options = _options(fixture)
    hour = datetime.now(UTC).hour
    options["quiet_hours"] = (hour, (hour + 1) % 24)
    assert claim_email_invitation(**options) == "wait"
    before, original, events = _retained(fixture), _rows(fixture)[0], _events(fixture)
    assert maintain_email_secrets() == {"suppressed": 0, "uncertain": 0, "status": "ok"}
    row = _rows(fixture)[0]
    assert row.model_dump_json() == original.model_dump_json()
    assert row.payload_ciphertext == original.payload_ciphertext
    assert _retained(fixture) == before and len(_events(fixture)) == len(events)


def test_older_active_row_does_not_starve_passed_appointment_with_small_batch():
    active = _queued("maintenance-active-first")
    expired, appointment_at = _short_appointment("maintenance-past-second")
    _wait_until(appointment_at)
    assert maintain_email_secrets(limit=1) == {
        "suppressed": 1,
        "uncertain": 0,
        "status": "ok",
    }
    assert (
        _rows(active)[0].state == "queued"
        and _rows(active)[0].payload_ciphertext is not None
    )
    assert (
        _rows(expired)[0].state == "suppressed"
        and _rows(expired)[0].payload_ciphertext is None
    )


def test_past_appointment_does_not_reclassify_a_live_irreversible_submission():
    fixture, appointment_at = _short_appointment("maintenance-past-submitting")
    claim = claim_email_invitation(**_options(fixture))
    assert isinstance(claim, EmailSubmissionClaim)
    _wait_until(appointment_at)
    row, before = _rows(fixture)[0], _retained(fixture)
    assert row.lease_until is not None and row.lease_until > datetime.now(UTC)
    assert row.expires_at > datetime.now(UTC)
    assert maintain_email_secrets() == {"suppressed": 0, "uncertain": 0, "status": "ok"}
    assert _rows(fixture)[0].state == "submitting" and _retained(fixture) == before
    calls = []
    # Its original claimant rechecks cadence: no effect occurs after the visit.
    assert (
        dispatch_email_claim(
            claim,
            **_options(fixture),
            transport=lambda invitation: calls.append(invitation) or "accepted",
        )
        == "suppressed"
    )
    assert calls == [] and _rows(fixture)[0].payload_ciphertext is None
    assert (
        dispatch_email_claim(
            claim, **_options(fixture), transport=lambda _: pytest.fail("resent")
        )
        == "discarded"
    )


def test_credential_only_revocation_is_selected_without_waiting_for_secret_expiry():
    fixture = _queued("maintenance-credential-revoked")
    original = _rows(fixture)[0]
    # Actual guarded metadata revocation, independent of root revocation.
    # No fabricated expiry, token mutation or guard disablement is involved.
    with global_session() as session:
        session.exec(
            select(Practice)
            .where(Practice.id == fixture[0].practice.id)
            .with_for_update()
        ).one()
        session.exec(
            select(VisitPrep).where(VisitPrep.id == fixture[1].id).with_for_update()
        ).one()
        session.execute(
            update(VisitPrepCredentialGeneration)
            .where(
                col(VisitPrepCredentialGeneration.visit_prep_id) == fixture[1].id,
                col(VisitPrepCredentialGeneration.generation) == 1,
            )
            .values(
                revoked_at=func.clock_timestamp(),
                revoked_by_ref="actor:synthetic-maintenance",
                revocation_reason="physician_revoked",
            )
        )
        session.commit()
    before = _retained(fixture)
    assert maintain_email_secrets() == {"suppressed": 1, "uncertain": 0, "status": "ok"}
    row = _rows(fixture)[0]
    assert row.state == "suppressed" and row.outcome_code == "obsolete_context"
    assert (
        row.payload_ciphertext is None
        and row.authorization_ciphertext == original.authorization_ciphertext
    )
    assert _retained(fixture) == before


def test_completed_corrected_reviewed_artifact_and_hold_survive_secret_erasure(
    monkeypatch,
):
    fixture = _queued("maintenance-completed-evidence")
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
        ("reason", "Invented concern."),
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
    VisitPrepLegalHold(
        visit_prep_id=root.id,
        owner_practice_member_id=root.owner_practice_member_id,
        generation=1,
        placed_at=datetime.now(UTC),
        placed_by_ref="actor:synthetic-maintenance",
        placement_reason="synthetic_review",
    ).save()
    before, original = _retained(fixture), _rows(fixture)[0]
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
    projection = _projection(patient)
    assert projection.current_answers_reviewed
    scope.practice.negotiated_baa_on_file = False
    scope.practice.save()
    assert maintain_email_secrets() == {"suppressed": 1, "uncertain": 0, "status": "ok"}
    assert _rows(fixture)[0].payload_ciphertext is None
    assert (
        _rows(fixture)[0].authorization_ciphertext == original.authorization_ciphertext
    )
    assert _retained(fixture) == before


def test_competing_maintainers_commit_one_secret_erasure_and_one_audit():
    fixture, appointment_at = _short_appointment("maintenance-competing")
    _wait_until(appointment_at)
    barrier = Barrier(2)

    def run():
        barrier.wait(timeout=5)
        return maintain_email_secrets()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result(timeout=10)
            for future in [executor.submit(run) for _ in range(2)]
        ]
    assert all(
        result["status"] == "ok" and result["uncertain"] == 0 for result in results
    )
    assert sum(int(result["suppressed"]) for result in results) == 1
    assert _rows(fixture)[0].payload_ciphertext is None
    assert [event.event_type for event in _events(fixture)].count(
        "visit_prep.email_suppressed"
    ) == 1


def test_cleanup_waiting_on_fence_rechecks_reschedule_without_touching_new_intent(
    monkeypatch,
):
    fixture, appointment_at = _short_appointment("maintenance-reschedule-wait")
    _wait_until(appointment_at)
    old_id, scope, root = _rows(fixture)[0].id, fixture[0], fixture[1]
    original, started = worker._lock_rows, Event()

    def marked(session, identifier):
        session.execute(
            text("SET LOCAL application_name = 'vp_email_maintenance_wait'")
        )
        started.set()
        return original(session, identifier)

    monkeypatch.setattr(worker, "_lock_rows", marked)
    with global_session() as parent:
        parent.exec(
            select(Practice).where(Practice.id == scope.practice.id).with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(maintain_email_secrets)
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(parent, "vp_email_maintenance_wait")
                changed = reschedule_visit_prep(
                    owner_practice_member_id=scope.owner.id,
                    owner_doctor_id=scope.doctor.id,
                    practice_id=scope.practice.id,
                    visit_prep_id=root.id,
                    expected_credential_generation=2,
                    expected_schedule_generation=2,
                    scheduled_at=datetime.now(UTC) + timedelta(days=15),
                    timezone_name="UTC",
                )
                assert changed.access_token is not None
                fixture[3].update(
                    access_token=changed.access_token,
                    expected_credential_generation=3,
                    operation_id=str(uuid4()),
                )
                enqueue_email_invitation(**fixture[3])
            finally:
                parent.commit()
            assert future.result(timeout=10) == {
                "suppressed": 1,
                "uncertain": 0,
                "status": "ok",
            }
    rows = _rows(fixture)
    old, new = (
        next(row for row in rows if row.id == old_id),
        next(row for row in rows if row.id != old_id),
    )
    assert old.state == "suppressed" and old.payload_ciphertext is None
    assert new.state == "queued" and new.payload_ciphertext is not None
    assert maintain_email_secrets() == {"suppressed": 0, "uncertain": 0, "status": "ok"}


@pytest.mark.parametrize("committed", [False, True])
def test_uncertain_maintenance_commit_never_reopens_submission_or_loses_evidence(
    monkeypatch, committed
):
    fixture, appointment_at = _short_appointment("maintenance-commit-ack")
    _wait_until(appointment_at)
    before, original = _retained(fixture), _rows(fixture)[0]
    from sqlmodel import Session

    commit = Session.commit

    def fail(session):
        if committed:
            commit(session)
        raise SQLAlchemyError("Synthetic private maintenance acknowledgment")

    with monkeypatch.context() as patch:
        patch.setattr(Session, "commit", fail)
        assert maintain_email_secrets() == {
            "suppressed": 0,
            "uncertain": 0,
            "status": "storage_unavailable",
        }
    row = _rows(fixture)[0]
    assert row.state == ("suppressed" if committed else "queued")
    assert row.payload_ciphertext == (
        None if committed else original.payload_ciphertext
    )
    assert row.authorization_ciphertext == original.authorization_ciphertext
    assert _retained(fixture) == before
    assert maintain_email_secrets() == {
        "suppressed": 0 if committed else 1,
        "uncertain": 0,
        "status": "ok",
    }


def test_ambient_pending_audit_is_not_flushed_committed_or_rolled_back():
    fixture, appointment_at = _short_appointment("maintenance-ambient")
    _wait_until(appointment_at)
    with global_session() as parent:
        parent.execute(text("SELECT 1"))
        transaction = parent.get_transaction()
        pending = AuthorityAuditEvent(
            event_type="visit_prep.synthetic_pending",
            outcome="pending",
            occurred_at=datetime.now(UTC),
            resource_type="visit_prep",
            resource_ref=str(fixture[1].id),
            route="synthetic_test",
            intent="synthetic_test",
        )
        parent.add(pending)
        assert maintain_email_secrets()["status"] == "storage_unavailable"
        assert pending in parent.new and parent.get_transaction() is transaction
        assert transaction is not None and transaction.is_active
        parent.rollback()
    assert _rows(fixture)[0].state == "queued"
    assert maintain_email_secrets()["suppressed"] == 1


def test_cleanup_selection_is_only_a_hint_when_consent_is_regranted_during_wait(
    monkeypatch,
):
    fixture = _queued("maintenance-regranted")
    _, _, withdrawal = _evidence(fixture[1], sequence=2, kind="withdrawn")
    _store_consent(withdrawal)
    original, started = worker._lock_rows, Event()

    def marked(session, identifier):
        session.execute(text("SET LOCAL application_name = 'vp_maintenance_regrant'"))
        started.set()
        return original(session, identifier)

    monkeypatch.setattr(worker, "_lock_rows", marked)
    with global_session() as parent:
        parent.exec(
            select(Practice)
            .where(Practice.id == fixture[0].practice.id)
            .with_for_update()
        ).one()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(maintain_email_secrets)
            try:
                assert started.wait(timeout=5)
                _observe_waiting_lock(parent, "vp_maintenance_regrant")
                _, _, regrant = _evidence(fixture[1], sequence=3)
                _store_consent(regrant)
            finally:
                parent.commit()
            assert future.result(timeout=10) == {
                "suppressed": 0,
                "uncertain": 0,
                "status": "ok",
            }
    row = _rows(fixture)[0]
    assert row.state == "queued" and row.payload_ciphertext is not None
    assert len(_events(fixture)) == 1
    assert maintain_email_secrets() == {"suppressed": 0, "uncertain": 0, "status": "ok"}
