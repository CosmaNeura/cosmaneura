from datetime import UTC, datetime

import pytest
from click import unstyle
from typer.testing import CliRunner

import app.services.staging_synthetic_appointment as staging_fixture_service
from app.cli import app

from app.models.doctor import Doctor
from app.models.ehr_sandbox_record_mapping import (
    EHRSandboxPatientDesignation,
    EHRSandboxRecordMapping,
)
from app.models.synthetic_preview_appointment_fixture import (
    SyntheticPreviewAppointmentFixture,
)


def _doctor(suffix: str = "cli") -> Doctor:
    return Doctor(
        clerk_id=f"user_test_synthetic_preview_{suffix}",
        email=f"synthetic-preview-{suffix}@example.test",
        biography=None,
        last_active_at=datetime.now(UTC),
    ).save()


@pytest.mark.parametrize(
    "confirmation_flags",
    [[], ["--confirm-staging-only"], ["--confirm-no-real-patient-data"]],
)
def test_cli_requires_both_explicit_confirmations_without_echoing_identity(
    confirmation_flags,
):
    email = "exact-provider@example.test"
    operator_ref = "operator_cli_confirmation"
    result = CliRunner().invoke(
        app,
        [
            "ensure-staging-synthetic-preview-appointment",
            "--doctor-id",
            "doctor_01j00000000000000000000000",
            "--doctor-email",
            email,
            "--operator-ref",
            operator_ref,
            *confirmation_flags,
        ],
    )

    assert result.exit_code == 2
    assert "--confirm-staging-only" in result.output
    assert "--confirm-no-real-patient-data" in result.output
    assert email not in result.output
    assert operator_ref not in result.output


def test_cli_rejects_non_doctor_typeid_before_database_access():
    result = CliRunner().invoke(
        app,
        [
            "ensure-staging-synthetic-preview-appointment",
            "--doctor-id",
            "patient_01j00000000000000000000000",
            "--doctor-email",
            "exact-provider@example.test",
            "--operator-ref",
            "operator_cli_wrong_prefix",
            "--confirm-staging-only",
            "--confirm-no-real-patient-data",
        ],
    )

    assert result.exit_code != 0
    assert "doctor_ prefix" in result.output


def test_cli_exposes_no_patient_vendor_or_environment_inputs():
    result = CliRunner().invoke(
        app, ["ensure-staging-synthetic-preview-appointment", "--help"]
    )
    output = unstyle(result.output)

    assert result.exit_code == 0
    assert "--doctor-id" in output
    assert "--doctor-email" in output
    assert "--operator-ref" in output
    assert "--patient-name" not in output
    assert "--date-of-birth" not in output
    assert "--appointment-date" not in output
    assert "--record-ref" not in output
    assert "--practice" not in output
    assert "--environment" not in output


def test_cli_creates_then_finds_fixture_without_designating_or_mapping(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(staging_fixture_service, "is_staging", lambda: True)
    doctor = _doctor()
    command = [
        "ensure-staging-synthetic-preview-appointment",
        "--doctor-id",
        str(doctor.id),
        "--doctor-email",
        doctor.email,
        "--operator-ref",
        "operator_cli_fixture_creator",
        "--confirm-staging-only",
        "--confirm-no-real-patient-data",
    ]
    runner = CliRunner()

    created = runner.invoke(app, command)
    found = runner.invoke(app, command)

    assert created.exit_code == 0
    assert "Synthetic staging fixture created" in created.output
    assert "patient_id=patient_" in created.output
    assert "appointment_id=appointment_" in created.output
    assert doctor.email not in created.output
    assert "operator_cli_fixture_creator" not in created.output
    assert found.exit_code == 0
    assert "Synthetic staging fixture already exists" in found.output
    assert (
        SyntheticPreviewAppointmentFixture.where(
            SyntheticPreviewAppointmentFixture.doctor_id == doctor.id
        ).count()
        == 1
    )
    assert EHRSandboxPatientDesignation.count() == 0
    assert EHRSandboxRecordMapping.count() == 0
