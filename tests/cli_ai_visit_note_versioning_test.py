"""Operator CLI coverage for content-free, confirmed note restoration."""

import re
from datetime import UTC, datetime, timedelta

from click import unstyle
from typer.testing import CliRunner

from app.cli import app

from app.models.ai_visit_note import AIVisitNote
from app.models.ai_visit_note_version_audit import AIVisitNoteVersionAudit
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.doctor_note_schema import DoctorNoteSchema
from sqlalchemy.exc import SQLAlchemyError

from tests.factories import AppointmentFactory, DoctorFactory, PatientFactory


def _normalized(output: str) -> str:
    return " ".join(unstyle(output).split())


def _note_pair() -> tuple[AIVisitNote, AIVisitNote, str]:
    doctor = DoctorFactory.build().save()
    patient = PatientFactory.build(doctor_id=doctor.id).save()
    appointment = AppointmentFactory.build(
        doctor_id=doctor.id,
        patient_id=patient.id,
    ).save()
    schema = DoctorNoteSchema(
        doctor_id=doctor.id,
        note_type=f"CLI restore {str(appointment.id)[-8:]}",
        fields={"Plan": "Synthetic"},
    ).save()
    transcript = AIVisitTranscript(
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        duration=1,
        transcript_data=[],
    ).save()
    sensitive_sentinel = "private-synthetic-note-sentinel"
    historical = AIVisitNote(
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        ai_transcript_id=transcript.id,
        doctor_note_schema_id=schema.id,
        note={"Plan": sensitive_sentinel},
        superseded_at=datetime.now(UTC) - timedelta(days=1),
        supersession_reason="migration:a81c9d7e4f20",
    ).save()
    current = AIVisitNote(
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        ai_transcript_id=transcript.id,
        doctor_note_schema_id=schema.id,
        note={"Plan": "current synthetic note"},
    ).save()
    return historical, current, sensitive_sentinel


def _command() -> list[str]:
    return [
        "restore-visit-note-version",
        "--operator-ref",
        "change-149-reviewer",
        "--reason-code",
        "clinical_correction",
    ]


def test_restore_cli_hidden_dry_run_then_confirmed_apply_is_content_free() -> None:
    historical, current, sensitive = _note_pair()
    runner = CliRunner()

    dry_run = runner.invoke(app, _command(), input=f"{historical.id}\n")

    assert dry_run.exit_code == 0, dry_run.output
    normalized = _normalized(dry_run.output)
    assert "DRY RUN" in normalized
    assert "no records changed" in normalized
    match = re.search(r"plan_sha256=([0-9a-f]{64})", normalized)
    assert match
    assert str(historical.id) not in dry_run.output
    assert str(current.id) not in dry_run.output
    assert sensitive not in dry_run.output
    assert AIVisitNoteVersionAudit.count() == 0

    applied = runner.invoke(
        app,
        _command() + ["--confirm-plan-sha256", match.group(1), "--apply"],
        input=f"{historical.id}\n",
    )

    assert applied.exit_code == 0, applied.output
    normalized = _normalized(applied.output)
    assert "restore applied" in normalized.lower()
    assert "no note versions were deleted" in normalized
    assert str(historical.id) not in applied.output
    assert str(current.id) not in applied.output
    assert sensitive not in applied.output
    assert AIVisitNoteVersionAudit.count() == 1


def test_restore_cli_refusal_does_not_echo_note_identifier_or_content() -> None:
    historical, current, sensitive = _note_pair()
    result = CliRunner().invoke(
        app,
        _command() + ["--confirm-plan-sha256", "0" * 64, "--apply"],
        input=f"{historical.id}\n",
    )

    assert result.exit_code == 1
    normalized = _normalized(result.output)
    assert "Restore refused" in normalized
    assert "no clinical identifiers were printed" in normalized
    assert str(historical.id) not in result.output
    assert str(current.id) not in result.output
    assert sensitive not in result.output
    assert AIVisitNoteVersionAudit.count() == 0


def test_restore_cli_sanitizes_unexpected_dry_run_database_failure(
    monkeypatch,
) -> None:
    historical, current, sensitive = _note_pair()
    leaked_error = (
        f"statement failed params note={historical.id} current={current.id} "
        f"body={sensitive}"
    )

    def fail_plan(**_kwargs):
        raise SQLAlchemyError(leaked_error)

    monkeypatch.setattr(
        "app.services.ai_visit_note_versions.plan_visit_note_restore",
        fail_plan,
    )
    result = CliRunner().invoke(
        app,
        _command(),
        input=f"{historical.id}\n",
    )

    normalized = _normalized(result.output)
    assert result.exit_code == 1
    assert "Restore plan unavailable" in normalized
    assert "database details were printed" in normalized
    assert str(historical.id) not in result.output
    assert str(current.id) not in result.output
    assert sensitive not in result.output
    assert "statement failed" not in result.output


def test_restore_cli_treats_unexpected_apply_failure_as_ambiguous(
    monkeypatch,
) -> None:
    historical, current, sensitive = _note_pair()
    leaked_error = (
        f"commit failed params note={historical.id} current={current.id} "
        f"body={sensitive}"
    )

    def fail_apply(**_kwargs):
        raise SQLAlchemyError(leaked_error)

    monkeypatch.setattr(
        "app.services.ai_visit_note_versions.restore_visit_note_version",
        fail_apply,
    )
    result = CliRunner().invoke(
        app,
        _command() + ["--confirm-plan-sha256", "0" * 64, "--apply"],
        input=f"{historical.id}\n",
    )

    normalized = _normalized(result.output)
    assert result.exit_code == 1
    assert "Restore outcome unknown" in normalized
    assert "verify the append-only audit and current note state" in normalized
    assert "database details were printed" in normalized
    assert str(historical.id) not in result.output
    assert str(current.id) not in result.output
    assert sensitive not in result.output
    assert "commit failed" not in result.output
