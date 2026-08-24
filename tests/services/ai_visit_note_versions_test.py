# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportCallIssue=false

"""Non-destructive current-version selection for generated visit notes."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.services import ai_visit_note_versions as versions
from app.services.ai_visit_note_versions import (
    VisitNoteRestoreReason,
    VisitNoteVersionError,
)

from activemodel.session_manager import SessionManager, get_engine, get_session
from app.models.ai_visit_note import (
    ADMIN_RESTORE_SUPERSESSION,
    HISTORICAL_DUPLICATE_SUPERSESSION,
    AIVisitNote,
)
from app.models.ai_visit_note_version_audit import AIVisitNoteVersionAudit
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.patient import Patient
from sqlalchemy import delete, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlmodel import Session, select

from tests.factories import AppointmentFactory, DoctorFactory, PatientFactory


@dataclass(frozen=True)
class _Family:
    appointment: Appointment
    schema: DoctorNoteSchema
    historical: tuple[AIVisitNote, ...]
    current: AIVisitNote


def _family(*, historical_count: int = 2) -> _Family:
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    appointment = AppointmentFactory.build(
        doctor_id=doctor.id,
        patient_id=patient.id,
    ).save()
    schema = DoctorNoteSchema(
        doctor_id=doctor.id,
        note_type=f"Version restore {str(appointment.id)[-8:]}",
        fields={"Plan": "Synthetic plan"},
    ).save()
    transcript = AIVisitTranscript(
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        duration=1,
        transcript_data=[],
    ).save()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    historical = tuple(
        AIVisitNote(
            doctor_id=doctor.id,
            appointment_id=appointment.id,
            ai_transcript_id=transcript.id,
            doctor_note_schema_id=schema.id,
            note={"Plan": f"synthetic retained version {index}"},
            created_at=base + timedelta(minutes=index),
            updated_at=base + timedelta(hours=1, minutes=index),
            superseded_at=base + timedelta(days=1, minutes=index),
            supersession_reason=HISTORICAL_DUPLICATE_SUPERSESSION,
        ).save()
        for index in range(historical_count)
    )
    current = AIVisitNote(
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        ai_transcript_id=transcript.id,
        doctor_note_schema_id=schema.id,
        note={"Plan": "synthetic current version"},
        created_at=base + timedelta(minutes=historical_count),
        updated_at=base + timedelta(hours=1, minutes=historical_count),
    ).save()
    return _Family(appointment, schema, historical, current)


@contextmanager
def _committed_family() -> Iterator[_Family]:
    """Make a family visible to independent restore-worker sessions."""

    manager = SessionManager.get_instance()
    test_connection = manager.session_connection
    manager.session_connection = None
    family = _family(historical_count=1)
    try:
        yield family
    finally:
        with get_engine().begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE ai_visit_note_version_audit DISABLE TRIGGER "
                "ai_visit_note_version_audit_reject_mutation"
            )
            connection.execute(
                text(
                    "DELETE FROM ai_visit_note_version_audit "
                    "WHERE operator_ref = 'concurrent-restore-test'"
                )
            )
            connection.exec_driver_sql(
                "ALTER TABLE ai_visit_note_version_audit ENABLE TRIGGER "
                "ai_visit_note_version_audit_reject_mutation"
            )
            connection.exec_driver_sql(
                "ALTER TABLE ai_visit_note DISABLE TRIGGER ai_visit_note_reject_delete"
            )
            connection.execute(
                delete(AIVisitNote).where(
                    AIVisitNote.appointment_id == family.appointment.id
                )
            )
            connection.exec_driver_sql(
                "ALTER TABLE ai_visit_note ENABLE TRIGGER ai_visit_note_reject_delete"
            )
            connection.execute(
                delete(AIVisitTranscript).where(
                    AIVisitTranscript.appointment_id == family.appointment.id
                )
            )
            connection.execute(
                delete(DoctorNoteSchema).where(DoctorNoteSchema.id == family.schema.id)
            )
            connection.execute(
                delete(Appointment).where(Appointment.id == family.appointment.id)
            )
            connection.execute(
                delete(Patient).where(Patient.id == family.appointment.patient_id)
            )
            connection.execute(
                delete(Doctor).where(Doctor.id == family.current.doctor_id)
            )
        manager.session_connection = test_connection


def _rows(family: _Family) -> list[AIVisitNote]:
    with get_session() as session:
        return list(
            session.exec(
                select(AIVisitNote).where(
                    AIVisitNote.appointment_id == family.appointment.id,
                    AIVisitNote.doctor_note_schema_id == family.schema.id,
                )
            ).all()
        )


def _clinical_snapshot(rows: list[AIVisitNote]) -> dict[str, dict[str, Any]]:
    return {
        str(row.id): {
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "doctor_id": row.doctor_id,
            "appointment_id": row.appointment_id,
            "ai_transcript_id": row.ai_transcript_id,
            "doctor_note_schema_id": row.doctor_note_schema_id,
            "note": row.note,
        }
        for row in rows
    }


def test_restore_plan_is_read_only_and_apply_preserves_every_clinical_field() -> None:
    family = _family(historical_count=4)
    target = family.historical[1]
    before_rows = _rows(family)
    before = _clinical_snapshot(before_rows)

    plan = versions.plan_visit_note_restore(
        target_note_id=target.id,
        operator_ref="change-149-reviewer",
        reason=VisitNoteRestoreReason.clinical_correction,
    )
    after_plan = _rows(family)
    assert _clinical_snapshot(after_plan) == before
    assert sum(row.superseded_at is None for row in after_plan) == 1
    assert AIVisitNoteVersionAudit.count() == 0

    effective_at = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    result = versions.restore_visit_note_version(
        target_note_id=target.id,
        operator_ref="change-149-reviewer",
        reason=VisitNoteRestoreReason.clinical_correction,
        confirm_plan_sha256=plan.plan_sha256,
        now=effective_at,
    )

    after = _rows(family)
    assert result.changed
    assert len(after) == len(before_rows) == 5
    assert _clinical_snapshot(after) == before
    assert [row.id for row in after if row.superseded_at is None] == [target.id]
    prior_current = next(row for row in after if row.id == family.current.id)
    assert prior_current.superseded_at == effective_at
    assert prior_current.supersession_reason == ADMIN_RESTORE_SUPERSESSION
    restored = next(row for row in after if row.id == target.id)
    assert restored.superseded_at is None
    assert restored.supersession_reason is None

    audit = AIVisitNoteVersionAudit.one(result.audit_id)
    assert audit.plan_sha256 == plan.plan_sha256
    assert audit.reason_code == VisitNoteRestoreReason.clinical_correction.value
    assert audit.transition_sequence == plan.transition_sequence == 1
    assert (
        audit.restore_frontier_transcript_ref_sha256
        == plan.restore_frontier_transcript_ref
    )
    assert restored.restore_frontier_transcript_id == family.current.ai_transcript_id
    rendered = audit.model_dump_json()
    assert "synthetic retained" not in rendered
    assert str(family.appointment.id) not in rendered
    assert str(family.schema.id) not in rendered
    assert str(family.current.id) not in rendered
    assert str(target.id) not in rendered


def test_restore_is_idempotent_for_an_applied_plan() -> None:
    family = _family(historical_count=1)
    target = family.historical[0]
    plan = versions.plan_visit_note_restore(
        target_note_id=target.id,
        operator_ref="incident-restore-001",
        reason=VisitNoteRestoreReason.incident_recovery,
    )
    first = versions.restore_visit_note_version(
        target_note_id=target.id,
        operator_ref="incident-restore-001",
        reason=VisitNoteRestoreReason.incident_recovery,
        confirm_plan_sha256=plan.plan_sha256,
    )
    second = versions.restore_visit_note_version(
        target_note_id=target.id,
        operator_ref="incident-restore-001",
        reason=VisitNoteRestoreReason.incident_recovery,
        confirm_plan_sha256=plan.plan_sha256,
    )

    assert first.changed
    assert not second.changed
    assert second.audit_id == first.audit_id
    assert (
        AIVisitNoteVersionAudit.where(
            AIVisitNoteVersionAudit.plan_sha256 == plan.plan_sha256
        ).count()
        == 1
    )
    assert sum(row.superseded_at is None for row in _rows(family)) == 1


def test_restore_cycle_generates_a_new_plan_for_each_transition() -> None:
    family = _family(historical_count=1)
    historical = family.historical[0]
    first_plan = versions.plan_visit_note_restore(
        target_note_id=historical.id,
        operator_ref="cycle-test-reviewer",
        reason=VisitNoteRestoreReason.operator_selected_prior_version,
    )
    versions.restore_visit_note_version(
        target_note_id=historical.id,
        operator_ref="cycle-test-reviewer",
        reason=VisitNoteRestoreReason.operator_selected_prior_version,
        confirm_plan_sha256=first_plan.plan_sha256,
    )
    reverse_plan = versions.plan_visit_note_restore(
        target_note_id=family.current.id,
        operator_ref="cycle-test-reviewer",
        reason=VisitNoteRestoreReason.operator_selected_prior_version,
    )
    versions.restore_visit_note_version(
        target_note_id=family.current.id,
        operator_ref="cycle-test-reviewer",
        reason=VisitNoteRestoreReason.operator_selected_prior_version,
        confirm_plan_sha256=reverse_plan.plan_sha256,
    )

    with pytest.raises(VisitNoteVersionError, match="not the latest"):
        versions.restore_visit_note_version(
            target_note_id=historical.id,
            operator_ref="cycle-test-reviewer",
            reason=VisitNoteRestoreReason.operator_selected_prior_version,
            confirm_plan_sha256=first_plan.plan_sha256,
        )

    second_plan = versions.plan_visit_note_restore(
        target_note_id=historical.id,
        operator_ref="cycle-test-reviewer",
        reason=VisitNoteRestoreReason.operator_selected_prior_version,
    )

    assert first_plan.transition_sequence == 1
    assert second_plan.transition_sequence == 3
    assert second_plan.plan_sha256 != first_plan.plan_sha256
    second = versions.restore_visit_note_version(
        target_note_id=historical.id,
        operator_ref="cycle-test-reviewer",
        reason=VisitNoteRestoreReason.operator_selected_prior_version,
        confirm_plan_sha256=second_plan.plan_sha256,
    )
    assert second.changed
    assert [row.id for row in _rows(family) if row.superseded_at is None] == [
        historical.id
    ]
    with pytest.raises(VisitNoteVersionError, match="not the latest"):
        versions.restore_visit_note_version(
            target_note_id=historical.id,
            operator_ref="cycle-test-reviewer",
            reason=VisitNoteRestoreReason.operator_selected_prior_version,
            confirm_plan_sha256=first_plan.plan_sha256,
        )
    assert [
        audit.transition_sequence
        for audit in AIVisitNoteVersionAudit.select()
        .order_by(AIVisitNoteVersionAudit.transition_sequence)
        .all()
    ] == [1, 2, 3]


def test_concurrent_exact_restore_retries_commit_once_and_replay_once() -> None:
    with _committed_family() as family:
        target = family.historical[0]
        plan = versions.plan_visit_note_restore(
            target_note_id=target.id,
            operator_ref="concurrent-restore-test",
            reason=VisitNoteRestoreReason.incident_recovery,
        )

        def apply_plan():
            return versions.restore_visit_note_version(
                target_note_id=target.id,
                operator_ref="concurrent-restore-test",
                reason=VisitNoteRestoreReason.incident_recovery,
                confirm_plan_sha256=plan.plan_sha256,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: apply_plan(), range(2)))

        assert sorted(result.changed for result in results) == [False, True]
        assert results[0].audit_id == results[1].audit_id
        assert (
            AIVisitNoteVersionAudit.where(
                AIVisitNoteVersionAudit.plan_sha256 == plan.plan_sha256
            ).count()
            == 1
        )
        assert [row.id for row in _rows(family) if row.superseded_at is None] == [
            target.id
        ]


def test_restore_rejects_current_target_and_stale_confirmation() -> None:
    family = _family(historical_count=2)
    with pytest.raises(VisitNoteVersionError, match="already current"):
        versions.plan_visit_note_restore(
            target_note_id=family.current.id,
            operator_ref="change-149-reviewer",
            reason=VisitNoteRestoreReason.operator_selected_prior_version,
        )

    first_target, second_target = family.historical
    stale = versions.plan_visit_note_restore(
        target_note_id=first_target.id,
        operator_ref="change-149-reviewer",
        reason=VisitNoteRestoreReason.operator_selected_prior_version,
    )
    fresh = versions.plan_visit_note_restore(
        target_note_id=second_target.id,
        operator_ref="change-149-reviewer",
        reason=VisitNoteRestoreReason.operator_selected_prior_version,
    )
    versions.restore_visit_note_version(
        target_note_id=second_target.id,
        operator_ref="change-149-reviewer",
        reason=VisitNoteRestoreReason.operator_selected_prior_version,
        confirm_plan_sha256=fresh.plan_sha256,
    )

    before = _clinical_snapshot(_rows(family))
    with pytest.raises(VisitNoteVersionError, match="no longer matches"):
        versions.restore_visit_note_version(
            target_note_id=first_target.id,
            operator_ref="change-149-reviewer",
            reason=VisitNoteRestoreReason.operator_selected_prior_version,
            confirm_plan_sha256=stale.plan_sha256,
        )
    assert _clinical_snapshot(_rows(family)) == before


def test_restore_rolls_back_swap_when_audit_cannot_be_written(monkeypatch) -> None:
    family = _family(historical_count=1)
    target = family.historical[0]
    plan = versions.plan_visit_note_restore(
        target_note_id=target.id,
        operator_ref="change-149-reviewer",
        reason=VisitNoteRestoreReason.clinical_correction,
    )
    before = {
        str(row.id): (row.superseded_at, row.supersession_reason)
        for row in _rows(family)
    }
    original_add = Session.add

    def reject_audit(self: Session, instance: object, *args, **kwargs) -> None:
        if isinstance(instance, AIVisitNoteVersionAudit):
            raise RuntimeError("synthetic audit write failure")
        original_add(self, instance, *args, **kwargs)

    monkeypatch.setattr(Session, "add", reject_audit)
    with pytest.raises(RuntimeError, match="synthetic audit write failure"):
        versions.restore_visit_note_version(
            target_note_id=target.id,
            operator_ref="change-149-reviewer",
            reason=VisitNoteRestoreReason.clinical_correction,
            confirm_plan_sha256=plan.plan_sha256,
        )

    after = {
        str(row.id): (row.superseded_at, row.supersession_reason)
        for row in _rows(family)
    }
    assert after == before
    assert AIVisitNoteVersionAudit.count() == 0


def test_restore_revalidates_every_cross_scope_reference_under_lock() -> None:
    family = _family(historical_count=1)
    target = family.historical[0]
    plan = versions.plan_visit_note_restore(
        target_note_id=target.id,
        operator_ref="cross-scope-reviewer",
        reason=VisitNoteRestoreReason.incident_recovery,
    )

    other_doctor = DoctorFactory.build().save()
    other_patient = PatientFactory.build(doctor_id=other_doctor.id).save()
    other_appointment = AppointmentFactory.build(
        doctor_id=other_doctor.id,
        patient_id=other_patient.id,
    ).save()
    other_transcript = AIVisitTranscript(
        doctor_id=other_doctor.id,
        appointment_id=other_appointment.id,
        patient_id=other_patient.id,
        duration=1,
        transcript_data=[],
    ).save()
    with get_session() as session:
        session.execute(
            text(
                "UPDATE ai_visit_note SET ai_transcript_id = :transcript_id "
                "WHERE id = :note_id"
            ),
            {
                "transcript_id": str(other_transcript.id.uuid),
                "note_id": str(target.id.uuid),
            },
        )
        session.commit()

    before = {
        str(row.id): (row.superseded_at, row.supersession_reason)
        for row in _rows(family)
    }
    with pytest.raises(VisitNoteVersionError, match="scope validation failed"):
        versions.restore_visit_note_version(
            target_note_id=target.id,
            operator_ref="cross-scope-reviewer",
            reason=VisitNoteRestoreReason.incident_recovery,
            confirm_plan_sha256=plan.plan_sha256,
        )
    assert {
        str(row.id): (row.superseded_at, row.supersession_reason)
        for row in _rows(family)
    } == before
    assert AIVisitNoteVersionAudit.count() == 0


def test_restore_rejects_cross_doctor_patient_ownership_under_lock() -> None:
    family = _family(historical_count=1)
    target = family.historical[0]
    plan = versions.plan_visit_note_restore(
        target_note_id=target.id,
        operator_ref="patient-scope-reviewer",
        reason=VisitNoteRestoreReason.incident_recovery,
    )
    other_doctor = DoctorFactory.build().save()
    with get_session() as session:
        session.execute(
            text("UPDATE patient SET doctor_id = :doctor_id WHERE id = :patient_id"),
            {
                "doctor_id": str(other_doctor.id.uuid),
                "patient_id": str(family.appointment.patient_id.uuid),
            },
        )
        session.commit()

    before = {
        str(row.id): (row.superseded_at, row.supersession_reason)
        for row in _rows(family)
    }
    with pytest.raises(VisitNoteVersionError, match="scope validation failed"):
        versions.restore_visit_note_version(
            target_note_id=target.id,
            operator_ref="patient-scope-reviewer",
            reason=VisitNoteRestoreReason.incident_recovery,
            confirm_plan_sha256=plan.plan_sha256,
        )
    assert {
        str(row.id): (row.superseded_at, row.supersession_reason)
        for row in _rows(family)
    } == before
    assert AIVisitNoteVersionAudit.count() == 0


def test_restore_one_schema_does_not_change_another_schema_family() -> None:
    family = _family(historical_count=1)
    second_schema = DoctorNoteSchema(
        doctor_id=family.current.doctor_id,
        note_type=f"Second schema {str(family.appointment.id)[-8:]}",
        fields={"Assessment": "Synthetic assessment"},
    ).save()
    base = datetime(2026, 1, 2, tzinfo=UTC)
    second_historical = AIVisitNote(
        doctor_id=family.current.doctor_id,
        appointment_id=family.appointment.id,
        ai_transcript_id=family.current.ai_transcript_id,
        doctor_note_schema_id=second_schema.id,
        note={"Assessment": "retained second-schema version"},
        created_at=base,
        updated_at=base,
        superseded_at=base + timedelta(days=1),
        supersession_reason=HISTORICAL_DUPLICATE_SUPERSESSION,
    ).save()
    second_current = AIVisitNote(
        doctor_id=family.current.doctor_id,
        appointment_id=family.appointment.id,
        ai_transcript_id=family.current.ai_transcript_id,
        doctor_note_schema_id=second_schema.id,
        note={"Assessment": "current second-schema version"},
        created_at=base + timedelta(minutes=1),
        updated_at=base + timedelta(minutes=1),
    ).save()
    second_before = _clinical_snapshot([second_historical, second_current])

    plan = versions.plan_visit_note_restore(
        target_note_id=family.historical[0].id,
        operator_ref="two-schema-reviewer",
        reason=VisitNoteRestoreReason.clinical_correction,
    )
    versions.restore_visit_note_version(
        target_note_id=family.historical[0].id,
        operator_ref="two-schema-reviewer",
        reason=VisitNoteRestoreReason.clinical_correction,
        confirm_plan_sha256=plan.plan_sha256,
    )

    with get_session() as session:
        second_after = list(
            session.exec(
                select(AIVisitNote).where(
                    AIVisitNote.appointment_id == family.appointment.id,
                    AIVisitNote.doctor_note_schema_id == second_schema.id,
                )
            ).all()
        )
    assert _clinical_snapshot(second_after) == second_before
    assert [note.id for note in second_after if note.superseded_at is None] == [
        second_current.id
    ]


def test_restore_audit_rejects_update_and_delete() -> None:
    family = _family(historical_count=1)
    target = family.historical[0]
    plan = versions.plan_visit_note_restore(
        target_note_id=target.id,
        operator_ref="immutable-audit-reviewer",
        reason=VisitNoteRestoreReason.incident_recovery,
    )
    versions.restore_visit_note_version(
        target_note_id=target.id,
        operator_ref="immutable-audit-reviewer",
        reason=VisitNoteRestoreReason.incident_recovery,
        confirm_plan_sha256=plan.plan_sha256,
    )

    with get_session() as session:
        with pytest.raises(DBAPIError, match="append-only"):
            with session.begin_nested():
                session.execute(
                    text(
                        "UPDATE ai_visit_note_version_audit "
                        "SET operator_ref = operator_ref "
                        "WHERE plan_sha256 = :plan"
                    ),
                    {"plan": plan.plan_sha256},
                )
        with pytest.raises(DBAPIError, match="append-only"):
            with session.begin_nested():
                session.execute(
                    text(
                        "DELETE FROM ai_visit_note_version_audit "
                        "WHERE plan_sha256 = :plan"
                    ),
                    {"plan": plan.plan_sha256},
                )

    assert (
        AIVisitNoteVersionAudit.where(
            AIVisitNoteVersionAudit.plan_sha256 == plan.plan_sha256
        ).count()
        == 1
    )


def test_partial_unique_index_allows_history_but_not_two_current_rows() -> None:
    family = _family(historical_count=2)
    assert len(_rows(family)) == 3
    with pytest.raises(IntegrityError):
        with get_session() as session:
            session.add(
                AIVisitNote(
                    doctor_id=family.current.doctor_id,
                    appointment_id=family.current.appointment_id,
                    ai_transcript_id=family.current.ai_transcript_id,
                    doctor_note_schema_id=family.current.doctor_note_schema_id,
                    note={"Plan": "synthetic duplicate current"},
                )
            )
            session.flush()
