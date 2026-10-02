"""Owned PostgreSQL recovery signals; no sender, PHI or cleanup side effects."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.services.visit_prep_email_operations as operations
import app.services.visit_prep_interviews as interviews
from app.services.visit_prep_email_operations import runtime_health
from app.services.visit_prep_email_worker import (
    EmailSubmissionClaim,
    claim_email_invitation,
    dispatch_email_claim,
    maintain_email_secrets,
    run_one_email,
)
from app.services.visit_preps import (
    reschedule_visit_prep,
    revoke_visit_prep,
    rotate_visit_prep_credential,
)

from activemodel.session_manager import get_engine, get_session, global_session
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep import VisitPrep, VisitPrepLegalHold
from app.models.visit_prep_delivery import VisitPrepEmailDelivery
from sqlalchemy import DateTime, event, func, literal, text
from sqlmodel import col, select

from tests.services.visit_prep_email_evidence_test import (
    _evidence,
    _store_approval,
    _store_consent,
)
from tests.services.visit_prep_email_worker_test import _options, _queued, _rows
from tests.services.visit_prep_interviews_test import _enable

_COUNTS = (
    "queued",
    "submitting",
    "accepted",
    "failed",
    "suppressed",
    "uncertain",
    "expired_queued",
    "obsolete_queued",
    "overdue_submitting",
    "recent_failed",
)


def _assert_closed(health):
    assert set(health) == {*_COUNTS, "oldest_queued_seconds", "queue_timing", "status"}
    assert all(type(health[name]) is int and health[name] >= 0 for name in _COUNTS)
    assert type(health["oldest_queued_seconds"]) is int
    assert health["oldest_queued_seconds"] >= 0
    assert health["queue_timing"] == "unqualified"
    assert health["status"] == (
        "degraded"
        if any(
            health[name]
            for name in (
                "expired_queued",
                "obsolete_queued",
                "overdue_submitting",
                "recent_failed",
                "uncertain",
            )
        )
        else "ok"
    )


def _assert_delta(before, after, **changes):
    _assert_closed(after)
    for name in _COUNTS:
        assert after[name] == before[name] + changes.get(name, 0), name


def _snapshot(fixture):
    with get_session() as session:
        row = session.get(VisitPrepEmailDelivery, _rows(fixture)[0].id)
        prep = session.get(VisitPrep, fixture[1].id)
        assert row is not None and prep is not None
        return (
            row.model_dump_json(),
            row.payload_ciphertext,
            row.authorization_ciphertext,
            prep.model_dump_json(),
            prep.recipient_snapshot_ciphertext,
            tuple(
                record.model_dump_json()
                for record in session.exec(
                    select(AuthorityAuditEvent).where(
                        AuthorityAuditEvent.client_event_id == row.operation_id
                    )
                ).all()
            ),
        )


def _diagnostic_clock(monkeypatch, at):
    # TEST ONLY: real stored metadata at exact boundaries without rewriting its
    # immutable timestamps or waiting ninety seconds/fifteen minutes. Other
    # tests below exercise real database time and actual expiry/worker effects.
    monkeypatch.setattr(
        operations,
        "func",
        SimpleNamespace(
            clock_timestamp=lambda: literal(at, type_=DateTime(timezone=True)),
            count=func.count,
            max=func.max,
            min=func.min,
        ),
    )


def test_actual_database_counts_and_empty_snapshot_shape():
    health = runtime_health()
    _assert_closed(health)
    with get_session() as session:
        actual = dict(
            session.exec(
                select(VisitPrepEmailDelivery.state, func.count()).group_by(
                    VisitPrepEmailDelivery.state
                )
            ).all()
        )
    for state in _COUNTS[:6]:
        assert health[state] == actual.get(state, 0)
    if health["queued"] == 0:
        assert health["oldest_queued_seconds"] == 0


def test_real_queued_claim_and_accepted_counts_never_infer_delivery():
    before = runtime_health()
    fixture = _queued("health-lifecycle")
    _assert_delta(before, runtime_health(), queued=1)
    claim = claim_email_invitation(**_options(fixture))
    assert isinstance(claim, EmailSubmissionClaim)
    _assert_delta(before, runtime_health(), submitting=1)
    calls = []

    def transport(invitation):
        calls.append(invitation)
        return "accepted"

    assert (
        dispatch_email_claim(claim, **_options(fixture), transport=transport)
        == "accepted"
    )
    health = runtime_health()
    _assert_delta(before, health, accepted=1)
    assert "delivered" not in health and len(calls) == 1
    saved = _snapshot(fixture)
    _assert_delta(before, runtime_health(), accepted=1)
    assert _snapshot(fixture) == saved
    assert run_one_email(**_options(fixture), transport=transport) == "idle"
    assert len(calls) == 1


def test_real_expired_queued_signal_and_secret_maintenance():
    before = runtime_health()
    fixture = _queued("health-expired", expiry=datetime.now(UTC) + timedelta(seconds=2))
    with get_session() as session:
        session.execute(text("SELECT pg_sleep(2.1)"))
    saved = _snapshot(fixture)
    _assert_delta(before, runtime_health(), queued=1, expired_queued=1)
    assert _snapshot(fixture) == saved
    assert maintain_email_secrets() == {"suppressed": 1, "uncertain": 0, "status": "ok"}
    _assert_delta(before, runtime_health(), suppressed=1)
    assert _rows(fixture)[0].authorization_ciphertext == saved[2]


def test_real_expired_secret_with_live_lease_is_overdue_not_retryable():
    before = runtime_health()
    fixture = _queued("health-crash", expiry=datetime.now(UTC) + timedelta(seconds=2))
    claim = claim_email_invitation(**_options(fixture))
    assert isinstance(claim, EmailSubmissionClaim)
    with get_session() as session:
        session.execute(text("SELECT pg_sleep(2.1)"))
        assert (
            session.execute(text("SELECT clock_timestamp()")).scalar_one()
            < claim.lease_until
        )
    saved = _snapshot(fixture)
    _assert_delta(before, runtime_health(), submitting=1, overdue_submitting=1)
    assert _snapshot(fixture) == saved
    assert maintain_email_secrets() == {"suppressed": 0, "uncertain": 1, "status": "ok"}
    _assert_delta(before, runtime_health(), uncertain=1)
    assert (
        dispatch_email_claim(
            claim, **_options(fixture), transport=lambda _: pytest.fail("re-sent")
        )
        == "discarded"
    )


@pytest.mark.parametrize("offset,overdue", [(-1, 0), (0, 1), (1, 1)])
def test_exact_lease_boundary_clock_diagnostic(monkeypatch, offset, overdue):
    before = runtime_health()
    fixture = _queued("health-lease-boundary")
    claim = claim_email_invitation(**_options(fixture))
    assert isinstance(claim, EmailSubmissionClaim)
    _diagnostic_clock(monkeypatch, claim.lease_until + timedelta(microseconds=offset))
    health = runtime_health()
    _assert_delta(before, health, submitting=1, overdue_submitting=overdue)
    assert _rows(fixture)[0].state == "submitting"


@pytest.mark.parametrize("change", ["revocation", "rotation", "consent", "approval"])
def test_actual_obsolete_context_and_withdrawal_need_cleanup_not_send(change):
    before = runtime_health()
    fixture = _queued("health-" + change)
    scope, root, _, _ = fixture
    if change in {"consent", "approval"}:
        _, approval, consent = _evidence(root, sequence=2, kind="withdrawn")
        _store_consent(consent) if change == "consent" else _store_approval(approval)
    elif change == "rotation":
        rotate_visit_prep_credential(
            owner_practice_member_id=scope.owner.id,
            owner_doctor_id=scope.doctor.id,
            practice_id=scope.practice.id,
            visit_prep_id=root.id,
            expected_credential_generation=1,
        )
    else:
        revoke_visit_prep(
            owner_practice_member_id=scope.owner.id,
            owner_doctor_id=scope.doctor.id,
            practice_id=scope.practice.id,
            visit_prep_id=root.id,
            expected_credential_generation=1,
            reason="physician_revoked",
        )
    saved = _snapshot(fixture)
    _assert_delta(before, runtime_health(), queued=1, obsolete_queued=1)
    assert _snapshot(fixture) == saved
    assert maintain_email_secrets()["suppressed"] == 1
    _assert_delta(before, runtime_health(), suppressed=1)


def test_old_withdrawal_is_not_current_after_actual_regrant():
    before = runtime_health()
    fixture = _queued("health-regrant")
    for sequence, kind in ((2, "withdrawn"), (3, None)):
        _, approval, consent = _evidence(fixture[1], sequence=sequence, kind=kind)
        _store_approval(approval)
        _store_consent(consent)
    _assert_delta(before, runtime_health(), queued=1)


@pytest.mark.parametrize("change", ["completed", "wrong_recipient", "rescheduled"])
def test_actual_patient_and_schedule_changes_are_obsolete(monkeypatch, change):
    before = runtime_health()
    fixture = _queued("health-" + change)
    scope, root, _, arguments = fixture
    if change == "rescheduled":
        reschedule_visit_prep(
            owner_practice_member_id=scope.owner.id,
            owner_doctor_id=scope.doctor.id,
            practice_id=scope.practice.id,
            visit_prep_id=root.id,
            expected_schedule_generation=1,
            expected_credential_generation=1,
            scheduled_at=datetime.now(UTC) + timedelta(days=15),
            timezone_name="UTC",
        )
    else:
        _enable(monkeypatch, scope.doctor)
        interviews.confirm_interview(
            access_token=arguments["access_token"], first_name="José", role="patient"
        )
        if change == "wrong_recipient":
            interviews.report_wrong_recipient(access_token=arguments["access_token"])
        else:
            for key in ("reason", "medications", "adherence", "story"):
                interviews.submit_response(
                    access_token=arguments["access_token"],
                    idempotency_key=str(uuid4()),
                    response=interviews.ResponseInput(
                        question_key=key, response_kind="skipped"
                    ),
                )
            interviews.complete_interview(access_token=arguments["access_token"])
    saved = _snapshot(fixture)
    _assert_delta(before, runtime_health(), queued=1, obsolete_queued=1)
    assert _snapshot(fixture) == saved
    assert maintain_email_secrets()["suppressed"] == 1
    _assert_delta(before, runtime_health(), suppressed=1)


def test_past_appointment_is_obsolete_independently_of_secret_expiry(monkeypatch):
    before = runtime_health()
    fixture = _queued("health-past-appointment")
    with get_session() as session:
        from app.models.visit_prep import VisitPrepScheduleGeneration

        schedule = session.get(
            VisitPrepScheduleGeneration, _rows(fixture)[0].schedule_generation_id
        )
        assert schedule is not None
        after_appointment = schedule.scheduled_at + timedelta(seconds=1)
    # Diagnostic time, not a claim that the real short-lived secret survives
    # fourteen days. The independent obsolete count must still include it.
    _diagnostic_clock(monkeypatch, after_appointment)
    health = runtime_health()
    assert health["obsolete_queued"] == int(before["obsolete_queued"]) + 1
    assert health["expired_queued"] == int(before["expired_queued"]) + 1
    assert _rows(fixture)[0].state == "queued"


def test_health_is_available_with_gates_off_baa_withdrawn_and_legal_hold():
    before = runtime_health()
    fixture = _queued("health-held")
    scope, root, _, _ = fixture
    scope.practice.negotiated_baa_on_file = False
    scope.practice.save()
    hold = VisitPrepLegalHold(
        visit_prep_id=root.id,
        owner_practice_member_id=root.owner_practice_member_id,
        generation=1,
        placed_at=datetime.now(UTC),
        placed_by_ref="actor:synthetic-health-test",
        placement_reason="synthetic_review",
    ).save()
    saved = _snapshot(fixture)
    _assert_delta(before, runtime_health(), queued=1)
    assert _snapshot(fixture) == saved
    with get_session() as session:
        retained = session.get(VisitPrepLegalHold, hold.id)
        assert retained is not None and retained.released_at is None
    # Aggregate recovery visibility is not current permission to send.
    assert (
        run_one_email(**_options(fixture), transport=lambda _: pytest.fail("sent"))
        == "suppressed"
    )


def test_quiet_hour_wait_and_queue_age_are_not_delivery_failure(monkeypatch):
    before = runtime_health()
    fixture = _queued("health-quiet")
    options = _options(fixture)
    hour = _rows(fixture)[0].requested_at.hour
    options["quiet_hours"] = (hour, (hour + 1) % 24)
    assert claim_email_invitation(**options) == "wait"
    row = _rows(fixture)[0]
    _diagnostic_clock(monkeypatch, row.requested_at + timedelta(minutes=10))
    health = runtime_health()
    _assert_delta(before, health, queued=1)
    age = health["oldest_queued_seconds"]
    assert isinstance(age, int) and age >= 600
    assert health["queue_timing"] == "unqualified"
    assert row.state == "queued" and row.submitted_at is None


def test_actual_transport_failure_and_historical_failure_window(monkeypatch):
    before = runtime_health()
    fixture = _queued("health-failure")
    assert run_one_email(**_options(fixture)) == "failed"
    health = runtime_health()
    _assert_delta(before, health, failed=1, recent_failed=1)
    row = _rows(fixture)[0]
    assert row.finalized_at is not None
    _diagnostic_clock(monkeypatch, row.finalized_at + timedelta(minutes=16))
    later = runtime_health()
    assert later["failed"] == int(before["failed"]) + 1
    assert later["recent_failed"] == 0
    assert row.state == "failed" and row.outcome_code == "transport_unavailable"


@pytest.mark.parametrize("offset,recent", [(-1, 1), (0, 1), (1, 0)])
def test_exact_recent_failure_window_clock_diagnostic(monkeypatch, offset, recent):
    before = runtime_health()
    fixture = _queued("health-failure-boundary")
    assert run_one_email(**_options(fixture)) == "failed"
    row = _rows(fixture)[0]
    assert row.finalized_at is not None
    at = row.finalized_at + timedelta(minutes=15, microseconds=offset)
    _diagnostic_clock(monkeypatch, at)
    health = runtime_health()
    with get_session() as session:
        retained_recent = session.exec(
            select(func.count())
            .select_from(VisitPrepEmailDelivery)
            .where(
                VisitPrepEmailDelivery.id != row.id,
                VisitPrepEmailDelivery.state == "failed",
                col(VisitPrepEmailDelivery.finalized_at) >= at - timedelta(minutes=15),
            )
        ).one()
    assert health["failed"] == int(before["failed"]) + 1
    assert health["recent_failed"] == retained_recent + recent


def test_actual_lost_provider_ack_stays_uncertain_without_recovery_write():
    before = runtime_health()
    fixture = _queued("health-lost-ack")
    calls = []

    def transport(invitation):
        calls.append(invitation)
        raise TimeoutError(invitation.recipient + invitation.access_token)

    assert run_one_email(**_options(fixture), transport=transport) == "uncertain"
    saved = _snapshot(fixture)
    _assert_delta(before, runtime_health(), uncertain=1)
    _assert_delta(before, runtime_health(), uncertain=1)
    assert _snapshot(fixture) == saved
    assert run_one_email(**_options(fixture), transport=transport) == "idle"
    assert len(calls) == 1


def test_one_readonly_metadata_query_never_selects_envelopes_or_identifiers():
    fixture = _queued("health-private-query")
    saved = _snapshot(fixture)
    statements = []
    selected = []
    transaction_controls = []

    def inspect(_connection, _cursor, statement, _parameters, context, _many):
        compiled = context.compiled
        if compiled is not None and getattr(compiled.statement, "is_select", False):
            statements.append(statement)
            selected.append(
                tuple(column.key for column in compiled.statement.selected_columns)
            )
        else:
            transaction_controls.append(statement.lower())

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", inspect)
    try:
        health = runtime_health()
    finally:
        event.remove(engine, "before_cursor_execute", inspect)
    _assert_closed(health)
    assert len(statements) == 1
    assert selected == [("sampled_at", *_COUNTS, "oldest_queued_at")]
    # The isolated service-test fixture creates/rolls back SAVEPOINTs. They
    # are transaction controls, not extra queue reads or runtime mutations.
    assert all(
        statement.startswith(
            ("savepoint ", "rollback to savepoint ", "release savepoint ")
        )
        for statement in transaction_controls
    )
    statement = statements[0].lower()
    assert "materialized" in statement and statement.count("clock_timestamp()") == 1
    assert "for update" not in statement
    for private_column in (
        "payload_ciphertext",
        "authorization_ciphertext",
        "evidence_ciphertext",
        "recipient_snapshot_ciphertext",
        "recipient_digest",
        "token_hash",
    ):
        assert private_column not in statement
    for private in (
        fixture[3]["access_token"],
        "patient@example.com",
        str(fixture[1].id),
        str(fixture[0].owner.id),
        fixture[3]["expected_clerk_subject"],
    ):
        assert private not in json.dumps(health)
    assert _snapshot(fixture) == saved


def test_ambient_transaction_is_not_flushed_committed_or_rolled_back():
    fixture = _queued("health-ambient")
    with global_session() as session:
        session.execute(text("SELECT 1"))
        transaction = session.get_transaction()
        assert transaction is not None
        pending = AuthorityAuditEvent(
            event_type="synthetic.health_pending",
            outcome="ok",
            occurred_at=datetime.now(UTC),
            resource_type="synthetic",
            resource_ref="synthetic-health-pending",
        )
        session.add(pending)
        assert runtime_health() == {"status": "storage_unavailable"}
        assert session.get_transaction() is transaction and transaction.is_active
        assert pending in session.new
        session.rollback()
    assert _rows(fixture)[0].state == "queued"


def test_storage_failure_is_private_and_never_fabricates_zero_counts(
    monkeypatch, caplog
):
    fixture = _queued("health-storage-failure")

    def unavailable():
        raise RuntimeError(fixture[3]["access_token"] + "patient@example.com")

    monkeypatch.setattr(operations, "global_session", unavailable)
    assert runtime_health() == {"status": "storage_unavailable"}
    assert fixture[3]["access_token"] not in caplog.text
    assert "patient@example.com" not in caplog.text


def test_actual_query_failure_is_private_and_preserves_retained_data(caplog):
    fixture = _queued("health-query-failure")
    saved = _snapshot(fixture)

    def fail_query(_connection, _cursor, statement, _parameters, _context, _many):
        if "email_health_clock" in statement:
            raise RuntimeError(fixture[3]["access_token"] + "patient@example.com")

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", fail_query)
    try:
        assert runtime_health() == {"status": "storage_unavailable"}
    finally:
        event.remove(engine, "before_cursor_execute", fail_query)
    assert fixture[3]["access_token"] not in caplog.text
    assert "patient@example.com" not in caplog.text
    assert _snapshot(fixture) == saved
