# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalOperand=false

"""PostgreSQL concurrency coverage for durable note-generation fences."""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

import pytest

from app.jobs import summarize
from app.services.ai_visit_note_versions import (
    VisitNoteRestoreReason,
    VisitNoteVersionError,
    plan_visit_note_restore,
    restore_visit_note_version,
)

from activemodel.session_manager import SessionManager, get_engine
from app.models.ai_visit_note import (
    HISTORICAL_DUPLICATE_SUPERSESSION,
    AIVisitNote,
)
from app.models.ai_visit_note_version_audit import AIVisitNoteVersionAudit
from app.models.ai_visit_transcript import AIVisitTranscript, TranscriptEntry
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.doctor_note_schema import DoctorNoteSchema
from app.models.patient import Patient
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError

from tests.factories import AppointmentFactory, DoctorFactory, PatientFactory


@pytest.fixture(autouse=True)
def _allow_doctor_access(monkeypatch):
    monkeypatch.setattr(summarize, "doctor_access_in_force", lambda _doctor: True)


@contextmanager
def _committed_visit() -> Iterator[
    tuple[Doctor, Patient, Appointment, DoctorNoteSchema, AIVisitTranscript]
]:
    """Create rows visible to independent worker sessions and clean them up."""

    manager = SessionManager.get_instance()
    test_connection = manager.session_connection
    manager.session_connection = None
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    appointment = AppointmentFactory.build(
        doctor_id=doctor.id,
        patient_id=patient.id,
    ).save()
    schema = DoctorNoteSchema(
        doctor_id=doctor.id,
        note_type=f"Concurrency {schema_suffix(appointment)}",
        fields={"CC": "Chief complaint", "Plan": "Treatment plan"},
    ).save()
    transcript = AIVisitTranscript(
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        duration=30,
        transcript_data=[
            TranscriptEntry(
                speaker="Doctor",
                content="initial segment",
                startTime=0,
                endTime=1000,
            )
        ],
    ).save()

    try:
        yield doctor, patient, appointment, schema, transcript
    finally:
        with get_engine().begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE ai_visit_note_version_audit DISABLE TRIGGER "
                "ai_visit_note_version_audit_reject_mutation"
            )
            connection.execute(
                text(
                    "DELETE FROM ai_visit_note_version_audit "
                    "WHERE operator_ref = 'summarize-restore-race'"
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
                delete(AIVisitNote).where(AIVisitNote.appointment_id == appointment.id)
            )
            connection.exec_driver_sql(
                "ALTER TABLE ai_visit_note ENABLE TRIGGER ai_visit_note_reject_delete"
            )
            connection.execute(
                delete(AIVisitTranscript).where(
                    AIVisitTranscript.appointment_id == appointment.id
                )
            )
            connection.execute(
                delete(DoctorNoteSchema).where(DoctorNoteSchema.id == schema.id)
            )
            connection.execute(
                delete(Appointment).where(Appointment.id == appointment.id)
            )
            connection.execute(delete(Patient).where(Patient.id == patient.id))
            connection.execute(delete(Doctor).where(Doctor.id == doctor.id))
        manager.session_connection = test_connection


def schema_suffix(appointment: Appointment) -> str:
    return str(appointment.id)[-10:]


def _identity_augment(*, original, transcript):
    del transcript
    return original


def test_database_rejects_duplicate_appointment_schema_notes() -> None:
    with _committed_visit() as (doctor, _patient, appointment, schema, transcript):
        AIVisitNote(
            doctor_id=doctor.id,
            appointment_id=appointment.id,
            ai_transcript_id=transcript.id,
            doctor_note_schema_id=schema.id,
            note={"CC": "first"},
        ).save()

        with pytest.raises(IntegrityError):
            AIVisitNote(
                doctor_id=doctor.id,
                appointment_id=appointment.id,
                ai_transcript_id=transcript.id,
                doctor_note_schema_id=schema.id,
                note={"CC": "duplicate"},
            ).save()


def test_concurrent_duplicate_schema_tasks_commit_one_note() -> None:
    with _committed_visit() as (_doctor, _patient, appointment, schema, transcript):
        generation_barrier = threading.Barrier(2)
        errors: list[Exception] = []

        def generate(*_args, **_kwargs):
            generation_barrier.wait(timeout=10)
            return {"CC": "converged", "Plan": "complete"}

        def run() -> None:
            try:
                summarize.perform(transcript.id, schema.id)
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        with (
            patch.object(summarize, "generate_clinical_note", side_effect=generate),
            patch.object(
                summarize,
                "augment_clinical_note",
                side_effect=_identity_augment,
            ),
        ):
            workers = [threading.Thread(target=run), threading.Thread(target=run)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=15)

        assert not any(worker.is_alive() for worker in workers)
        assert errors == []
        notes = AIVisitNote.where(
            (AIVisitNote.appointment_id == appointment.id)
            & (AIVisitNote.doctor_note_schema_id == schema.id)
            & (AIVisitNote.superseded_at == None)  # noqa: E711
        ).all()
        assert len(list(notes)) == 1


def test_older_slow_generation_cannot_replace_newer_transcript_note() -> None:
    with _committed_visit() as (doctor, patient, appointment, schema, first):
        old_generation_started = threading.Event()
        release_old_generation = threading.Event()
        errors: list[Exception] = []

        def generate(transcript, *_args, **_kwargs):
            contents = [entry.content for entry in transcript.transcript_data]
            if "new segment" not in contents:
                old_generation_started.set()
                assert release_old_generation.wait(timeout=15)
                return {"CC": "stale", "Plan": "stale"}
            return {"CC": "current", "Plan": "current"}

        def run_old() -> None:
            try:
                summarize.perform(first.id, schema.id)
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        with (
            patch.object(summarize, "generate_clinical_note", side_effect=generate),
            patch.object(
                summarize,
                "augment_clinical_note",
                side_effect=_identity_augment,
            ),
        ):
            old_worker = threading.Thread(target=run_old)
            old_worker.start()
            assert old_generation_started.wait(timeout=10)

            second = AIVisitTranscript(
                doctor_id=doctor.id,
                appointment_id=appointment.id,
                patient_id=patient.id,
                duration=45,
                transcript_data=[
                    TranscriptEntry(
                        speaker="Patient",
                        content="new segment",
                        startTime=0,
                        endTime=1000,
                    )
                ],
            ).save()
            second.created_at = first.created_at + timedelta(seconds=1)
            second.save()

            summarize.perform(second.id, schema.id)
            release_old_generation.set()
            old_worker.join(timeout=15)

        assert not old_worker.is_alive()
        assert errors == []
        note = AIVisitNote.where(
            (AIVisitNote.appointment_id == appointment.id)
            & (AIVisitNote.doctor_note_schema_id == schema.id)
            & (AIVisitNote.superseded_at == None)  # noqa: E711
        ).one()
        assert note.ai_transcript_id == second.id
        assert note.note["CC"] == "current"


def test_restore_and_generated_note_finalize_serialize_without_lost_choice() -> None:
    with _committed_visit() as (doctor, patient, appointment, schema, first):
        historical = AIVisitNote(
            doctor_id=doctor.id,
            appointment_id=appointment.id,
            ai_transcript_id=first.id,
            doctor_note_schema_id=schema.id,
            note={"CC": "operator target"},
            superseded_at=first.created_at + timedelta(seconds=1),
            supersession_reason=HISTORICAL_DUPLICATE_SUPERSESSION,
        ).save()
        original_current = AIVisitNote(
            doctor_id=doctor.id,
            appointment_id=appointment.id,
            ai_transcript_id=first.id,
            doctor_note_schema_id=schema.id,
            note={"CC": "old current"},
        ).save()
        second = AIVisitTranscript(
            doctor_id=doctor.id,
            appointment_id=appointment.id,
            patient_id=patient.id,
            duration=45,
            transcript_data=[
                TranscriptEntry(
                    speaker="Patient",
                    content="new segment",
                    startTime=0,
                    endTime=1000,
                )
            ],
        ).save()
        second.created_at = first.created_at + timedelta(seconds=2)
        second.save()
        plan = plan_visit_note_restore(
            target_note_id=historical.id,
            operator_ref="summarize-restore-race",
            reason=VisitNoteRestoreReason.incident_recovery,
        )

        start = threading.Barrier(2)
        restore_results = []
        summarize_results = []
        restore_errors: list[Exception] = []
        summarize_errors: list[Exception] = []

        def run_restore() -> None:
            try:
                start.wait(timeout=10)
                restore_results.append(
                    restore_visit_note_version(
                        target_note_id=historical.id,
                        operator_ref="summarize-restore-race",
                        reason=VisitNoteRestoreReason.incident_recovery,
                        confirm_plan_sha256=plan.plan_sha256,
                    )
                )
            except VisitNoteVersionError as exc:
                restore_errors.append(exc)
            except Exception as exc:  # pragma: no cover - asserted below
                restore_errors.append(exc)

        def run_summarize() -> None:
            try:
                start.wait(timeout=10)
                summarize_results.append(
                    summarize._finalize_generated_note(
                        trigger=second,
                        schema=schema,
                        generated_through_transcript_id=second.id,
                        note={"CC": "new generated note"},
                        appointment_name="Race-safe appointment",
                    )
                )
            except Exception as exc:  # pragma: no cover - asserted below
                summarize_errors.append(exc)

        workers = [
            threading.Thread(target=run_restore),
            threading.Thread(target=run_summarize),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=15)

        assert not any(worker.is_alive() for worker in workers)
        assert summarize_errors == []
        assert len(summarize_results) == 1
        assert len(restore_results) + len(restore_errors) == 1
        assert all(isinstance(exc, VisitNoteVersionError) for exc in restore_errors)

        all_versions = list(
            AIVisitNote.where(
                (AIVisitNote.appointment_id == appointment.id)
                & (AIVisitNote.doctor_note_schema_id == schema.id)
            ).all()
        )
        current = [note for note in all_versions if note.superseded_at is None]
        assert len(current) == 1
        assert {historical.id, original_current.id}.issubset(
            {note.id for note in all_versions}
        )
        audit_count = AIVisitNoteVersionAudit.where(
            AIVisitNoteVersionAudit.operator_ref == "summarize-restore-race"
        ).count()
        if restore_results:
            assert restore_results[0].changed
            assert summarize_results == [False]
            assert current[0].id == historical.id
            assert len(all_versions) == 2
            assert audit_count == 1
        else:
            assert summarize_results == [True]
            assert current[0].ai_transcript_id == second.id
            assert len(all_versions) == 3
            assert audit_count == 0
