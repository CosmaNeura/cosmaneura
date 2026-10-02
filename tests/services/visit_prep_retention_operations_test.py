"""Owned retention metadata; diagnostic future clocks are not purge evidence."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import app.services.visit_prep_retention_operations as operations
from app.configuration.visit_prep import VisitPrepSettings
from app.services import visit_prep_interviews as interviews
from app.services.visit_prep_retention_operations import runtime_health
from app.services.visit_preps import (
    reschedule_visit_prep,
    revoke_visit_prep,
    rotate_visit_prep_credential,
)

from activemodel.session_manager import get_engine, get_session, global_session
from app.models.authority import AuthorityAuditEvent
from app.models.visit_prep import (
    VisitPrep,
    VisitPrepCredentialGeneration,
    VisitPrepLegalHold,
)
from sqlalchemy import DateTime, event, func, literal, text, update
from sqlmodel import col, select

from tests.services.visit_prep_interviews_test import _confirm, _fixture, _respond

_COUNTS = (
    "preparations",
    "unused",
    "unused_expired_unrevoked",
    "unused_cleanup_due",
    "unused_hard_ceiling_reached",
    "unused_cleanup_held",
    "confirmed_retained",
    "confirmed_retention_elapsed",
    "quarantined_pending_review",
    "quarantined_disposition_elapsed",
    "active_legal_holds",
    "incomplete_current_lineage",
)


def _closed(health):
    assert set(health) == {*_COUNTS, "retention_qualification", "status"}
    assert all(type(health[key]) is int and health[key] >= 0 for key in _COUNTS)
    assert health["retention_qualification"] == "not_qualified"
    attention: set[str] = set(_COUNTS)
    attention -= {
        "preparations",
        "unused",
        "confirmed_retained",
        "active_legal_holds",
        "unused_cleanup_held",
    }
    assert health["status"] == (
        "degraded" if any(health[key] for key in attention) else "ok"
    )


def _delta(before, after, **changes):
    _closed(after)
    for key in _COUNTS:
        assert after[key] == before[key] + changes.get(key, 0), key


def _clock(monkeypatch, at):
    # TEST ONLY: evaluate actual immutable metadata at exact future deadlines.
    # This neither ages records nor proves the real cleanup service runs.
    monkeypatch.setattr(
        operations,
        "func",
        SimpleNamespace(
            clock_timestamp=lambda: literal(at, type_=DateTime(timezone=True)),
            count=func.count,
            max=func.max,
        ),
    )


def _retained(fixture):
    tables = (
        "visit_prep",
        "visit_prep_schedule_generation",
        "visit_prep_credential_generation",
        "visit_prep_question_response",
        "visit_prep_contribution_event",
        "visit_prep_legal_hold",
    )
    with get_session() as session:
        return {
            table: session.execute(
                text(
                    f"SELECT to_jsonb(t)::text FROM public.{table} AS t "
                    f"WHERE {'id' if table == 'visit_prep' else 'visit_prep_id'} = :id "
                    "ORDER BY id"
                ),
                {"id": fixture.prep_id.uuid},
            )
            .scalars()
            .all()
            for table in tables
        }


def _hold(fixture, *, generation=1):
    return VisitPrepLegalHold(
        visit_prep_id=fixture.prep_id,
        owner_practice_member_id=fixture.owner.id,
        generation=generation,
        placed_at=datetime.now(UTC),
        placed_by_ref="actor:invented-retention-test",
        placement_reason="synthetic_review",
    ).save()


def test_empty_and_actual_counts_have_a_closed_value_free_shape(monkeypatch):
    before = runtime_health()
    _closed(before)
    with get_session() as session:
        assert (
            before["preparations"]
            == session.exec(select(func.count()).select_from(VisitPrep)).one()
        )
    fixture = _fixture(monkeypatch, "retention-invited")
    saved = _retained(fixture)
    _delta(before, runtime_health(), preparations=1, unused=1)
    assert _retained(fixture) == saved


@pytest.mark.parametrize("days", [3, 30, 90])
@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_exact_unused_expiry_cleanup_and_hard_ceiling_diagnostics(
    monkeypatch, days, offset
):
    appointment_at = datetime.now(UTC) + timedelta(days=20)
    at = appointment_at + timedelta(days=days, microseconds=offset)
    _clock(monkeypatch, at)
    before = runtime_health()
    fixture = _fixture(monkeypatch, "retention-boundary", scheduled_at=appointment_at)
    health = runtime_health()
    _delta(
        before,
        health,
        preparations=1,
        unused=1,
        unused_expired_unrevoked=int(days > 3 or offset >= 0),
        unused_cleanup_due=int(days > 30 or (days == 30 and offset >= 0)),
        unused_hard_ceiling_reached=int(days == 90 and offset >= 0),
    )
    saved = _retained(fixture)
    assert runtime_health() == health and _retained(fixture) == saved


@pytest.mark.parametrize(
    "reason", ["wrong_recipient", "appointment_cancelled", "physician_revoked"]
)
def test_actual_unused_revocation_distinguishes_immediate_cleanup_reasons(
    monkeypatch, reason
):
    before = runtime_health()
    fixture = _fixture(monkeypatch, "retention-revoked")
    revoke_visit_prep(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        visit_prep_id=fixture.prep_id,
        expected_credential_generation=1,
        reason=reason,
    )
    saved = _retained(fixture)
    _delta(
        before,
        runtime_health(),
        preparations=1,
        unused=1,
        unused_cleanup_due=int(reason != "physician_revoked"),
    )
    assert _retained(fixture) == saved


def test_real_reschedule_supersedes_old_cleanup_deadlines_and_expired_credential(
    monkeypatch,
):
    appointment_at = datetime.now(UTC) + timedelta(days=20)
    _clock(monkeypatch, appointment_at + timedelta(days=91))
    before = runtime_health()
    fixture = _fixture(monkeypatch, "retention-reschedule", scheduled_at=appointment_at)
    new_appointment = fixture.scheduled_at + timedelta(days=120)
    reschedule_visit_prep(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        visit_prep_id=fixture.prep_id,
        expected_schedule_generation=1,
        expected_credential_generation=1,
        scheduled_at=new_appointment,
        timezone_name="UTC",
    )
    health = runtime_health()
    _delta(before, health, preparations=1, unused=1)
    saved = _retained(fixture)
    assert len(saved["visit_prep_schedule_generation"]) == 2
    assert len(saved["visit_prep_credential_generation"]) == 2
    assert runtime_health() == health and _retained(fixture) == saved


def test_rotation_history_never_duplicates_root_or_counts_old_revoked_tokens(
    monkeypatch,
):
    before = runtime_health()
    fixture = _fixture(monkeypatch, "retention-rotated-token")
    rotate_visit_prep_credential(
        owner_practice_member_id=fixture.owner.id,
        owner_doctor_id=fixture.doctor.id,
        practice_id=fixture.practice.id,
        visit_prep_id=fixture.prep_id,
        expected_credential_generation=1,
    )
    _delta(before, runtime_health(), preparations=1, unused=1)
    saved = _retained(fixture)
    assert len(saved["visit_prep_credential_generation"]) == 2
    _delta(before, runtime_health(), preparations=1, unused=1)
    assert _retained(fixture) == saved


def test_current_credential_revocation_is_not_an_unrevoked_expiry(monkeypatch):
    appointment_at = datetime.now(UTC) + timedelta(days=20)
    _clock(monkeypatch, appointment_at + timedelta(days=4))
    before = runtime_health()
    fixture = _fixture(
        monkeypatch, "retention-token-only-revoked", scheduled_at=appointment_at
    )
    # Existing credential-only guard can revoke a token while a root remains
    # unrevoked. This read must not demand another revocation of that token.
    with get_session() as session:
        session.execute(
            update(VisitPrepCredentialGeneration)
            .where(col(VisitPrepCredentialGeneration.visit_prep_id) == fixture.prep_id)
            .values(
                revoked_at=func.clock_timestamp(),
                revoked_by_ref="actor:invented-retention-revocation",
                revocation_reason="physician_revoked",
            )
        )
        session.commit()
    _delta(before, runtime_health(), preparations=1, unused=1)


@pytest.mark.parametrize("phase", ["confirmed", "partial", "all_skipped"])
def test_confirmation_not_answer_count_defines_clinical_retention(monkeypatch, phase):
    appointment_at = datetime.now(UTC) + timedelta(days=20)
    _clock(monkeypatch, appointment_at + timedelta(days=91))
    before = runtime_health()
    fixture = _fixture(monkeypatch, "retention-clinical", scheduled_at=appointment_at)
    _confirm(fixture)
    if phase == "partial":
        _respond(fixture, "reason", answer="Invented patient priority.")
    elif phase == "all_skipped":
        for key in ("reason", "medications", "adherence", "story"):
            _respond(fixture, key)
        interviews.complete_interview(access_token=fixture.access_token)
    health = runtime_health()
    _delta(before, health, preparations=1, confirmed_retained=1)
    saved = _retained(fixture)
    assert runtime_health() == health and _retained(fixture) == saved


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_clinical_deadline_is_review_evidence_not_purge_permission(monkeypatch, offset):
    appointment_at = datetime.now(UTC) + timedelta(days=20)
    until = appointment_at.replace(year=appointment_at.year + 7)
    _clock(monkeypatch, until + timedelta(microseconds=offset))
    before = runtime_health()
    fixture = _fixture(
        monkeypatch, "retention-clinical-deadline", scheduled_at=appointment_at
    )
    _confirm(fixture)
    _hold(fixture)
    with get_session() as session:
        prep = session.get(VisitPrep, fixture.prep_id)
        assert prep is not None and prep.retain_until is not None
        assert prep.retain_until == until
    health = runtime_health()
    _delta(
        before,
        health,
        preparations=1,
        confirmed_retained=int(offset < 0),
        confirmed_retention_elapsed=int(offset >= 0),
        active_legal_holds=1,
    )
    assert health["retention_qualification"] == "not_qualified"
    saved = _retained(fixture)
    assert runtime_health() == health and _retained(fixture) == saved


def test_quarantine_excludes_ordinary_clinical_deadline_and_needs_disposition(
    monkeypatch,
):
    appointment_at = datetime.now(UTC) + timedelta(days=20)
    _clock(monkeypatch, appointment_at.replace(year=appointment_at.year + 8))
    before = runtime_health()
    fixture = _fixture(monkeypatch, "retention-quarantine", scheduled_at=appointment_at)
    _confirm(fixture)
    _respond(fixture, "reason", answer="Invented misdirected contribution.")
    interviews.report_wrong_recipient(access_token=fixture.access_token)
    health = runtime_health()
    _delta(before, health, preparations=1, quarantined_pending_review=1)
    saved = _retained(fixture)
    assert runtime_health() == health and _retained(fixture) == saved


@pytest.mark.parametrize("disposition", ["retain_until", "purge_approved"])
@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_exact_incident_disposition_deadline_remains_separate_and_readonly(
    monkeypatch, disposition, offset
):
    until = datetime.now(UTC) + timedelta(days=2)
    _clock(monkeypatch, until + timedelta(microseconds=offset))
    before = runtime_health()
    fixture = _fixture(monkeypatch, "retention-disposition")
    _confirm(fixture)
    interviews.report_wrong_recipient(access_token=fixture.access_token)
    # TEST ONLY: exercise an existing guarded incident-state transition with
    # invented owner evidence; the runtime read cannot choose a disposition.
    with get_session() as session:
        session.execute(
            update(VisitPrep)
            .where(col(VisitPrep.id) == fixture.prep_id)
            .values(
                misdirected_disposition=disposition,
                misdirected_disposition_at=func.clock_timestamp(),
                misdirected_disposition_by_ref="actor:invented-incident-owner",
                misdirected_disposition_until=until,
            )
        )
        session.commit()
    health = runtime_health()
    _delta(
        before, health, preparations=1, quarantined_disposition_elapsed=int(offset >= 0)
    )
    saved = _retained(fixture)
    assert runtime_health() == health and _retained(fixture) == saved


def test_metadata_recovery_works_with_release_off_and_baa_withdrawn(monkeypatch):
    before = runtime_health()
    fixture = _fixture(monkeypatch, "retention-withdrawn-authority")
    _confirm(fixture)
    _respond(fixture, "reason", answer="Invented saved concern.")
    _hold(fixture)
    fixture.practice.negotiated_baa_on_file = False
    fixture.practice.save()
    monkeypatch.setattr(
        interviews.visit_prep_access, "VISIT_PREP_SETTINGS", VisitPrepSettings()
    )
    saved = _retained(fixture)
    health = runtime_health()
    _delta(before, health, preparations=1, confirmed_retained=1, active_legal_holds=1)
    assert _retained(fixture) == saved


def test_incident_legal_hold_never_becomes_elapsed_ordinary_retention(monkeypatch):
    appointment_at = datetime.now(UTC) + timedelta(days=20)
    _clock(monkeypatch, appointment_at.replace(year=appointment_at.year + 8))
    before = runtime_health()
    fixture = _fixture(
        monkeypatch, "retention-incident-held", scheduled_at=appointment_at
    )
    _confirm(fixture)
    interviews.report_wrong_recipient(access_token=fixture.access_token)
    _hold(fixture)
    with get_session() as session:
        session.execute(
            update(VisitPrep)
            .where(col(VisitPrep.id) == fixture.prep_id)
            .values(
                misdirected_disposition="legal_hold",
                misdirected_disposition_at=func.clock_timestamp(),
                misdirected_disposition_by_ref="actor:invented-incident-owner",
            )
        )
        session.commit()
    _delta(before, runtime_health(), preparations=1, active_legal_holds=1)
    saved = _retained(fixture)
    assert _retained(fixture) == saved


def test_one_statement_selects_only_aggregates_without_private_values_or_locks(
    monkeypatch,
):
    fixture = _fixture(monkeypatch, "retention-private-query")
    _confirm(fixture)
    _respond(fixture, "reason", answer="Invented sensitive saved wording.")
    _hold(fixture)
    saved = _retained(fixture)
    statements, selected, controls = [], [], []

    def inspect(_connection, _cursor, statement, _parameters, context, _many):
        compiled = context.compiled
        if compiled is not None and getattr(compiled.statement, "is_select", False):
            statements.append(statement)
            selected.append(
                tuple(column.key for column in compiled.statement.selected_columns)
            )
        else:
            controls.append(statement.lower())

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", inspect)
    try:
        health = runtime_health()
    finally:
        event.remove(engine, "before_cursor_execute", inspect)
    _closed(health)
    assert len(statements) == 1 and selected == [("sampled_at", *_COUNTS)]
    assert all(
        statement.startswith(
            ("savepoint ", "rollback to savepoint ", "release savepoint ")
        )
        for statement in controls
    )
    statement = statements[0].lower()
    assert "materialized" in statement and statement.count("clock_timestamp()") == 1
    assert "for update" not in statement and "for key share" not in statement
    for forbidden in (
        "ciphertext",
        "token_hash",
        "first_name",
        "physician_display_name",
        "practice_display_name",
        "misdirected_incident_ref",
        "retention_policy_version",
    ):
        assert forbidden not in statement
    serialized = json.dumps(health)
    for private in (
        fixture.access_token,
        "patient@example.com",
        "Invented sensitive saved wording.",
        str(fixture.prep_id),
        str(fixture.owner.id),
        str(fixture.practice.id),
    ):
        assert private not in serialized
    assert _retained(fixture) == saved


def test_ambient_pending_audit_remains_unflushed_and_transaction_unchanged():
    with global_session() as session:
        session.execute(text("SELECT 1"))
        transaction = session.get_transaction()
        assert transaction is not None
        pending = AuthorityAuditEvent(
            event_type="synthetic.retention_pending",
            outcome="ok",
            occurred_at=datetime.now(UTC),
            resource_type="synthetic",
            resource_ref="invented-retention-pending",
        )
        session.add(pending)
        assert runtime_health() == {"status": "storage_unavailable"}
        assert pending in session.new
        assert session.get_transaction() is transaction and transaction.is_active
        session.rollback()


def test_storage_failure_is_private_and_never_invents_healthy_zeroes(
    monkeypatch, caplog
):
    def unavailable():
        raise RuntimeError("patient@example.com invented private SQL or token")

    monkeypatch.setattr(operations, "global_session", unavailable)
    assert runtime_health() == {"status": "storage_unavailable"}
    assert not caplog.records
