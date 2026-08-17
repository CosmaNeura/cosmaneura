from datetime import UTC, datetime

from typer.testing import CliRunner

from app.cli import app

from app.models.doctor import Doctor
from app.models.ehr_sandbox_record_mapping import EHRSandboxRecordMapping
from app.models.patient import Patient


def test_provision_command_requires_explicit_synthetic_confirmation():
    result = CliRunner().invoke(
        app,
        [
            "provision-athena-preview-record",
            "--doctor-id",
            "doctor_01j00000000000000000000000",
            "--patient-id",
            "patient_01j00000000000000000000000",
            "--record-ref",
            "a-195900.E-60178",
            "--operator-ref",
            "operator_test",
        ],
    )

    assert result.exit_code == 2
    assert "--confirm-synthetic-only is required" in result.output
    assert "a-195900.E-60178" not in result.output

    designation = CliRunner().invoke(
        app,
        [
            "designate-athena-preview-patient",
            "--doctor-id",
            "doctor_01j00000000000000000000000",
            "--patient-id",
            "patient_01j00000000000000000000000",
            "--operator-ref",
            "operator_test",
        ],
    )
    assert designation.exit_code == 2
    assert "--confirm-no-real-patient-data is required" in designation.output


def test_operator_commands_do_not_accept_environment_or_practice_overrides():
    help_result = CliRunner().invoke(app, ["provision-athena-preview-record", "--help"])

    assert help_result.exit_code == 0
    assert "--environment" not in help_result.output
    assert "--practice" not in help_result.output
    assert "--confirm-synthetic-only" in help_result.output


def test_operator_can_provision_and_revoke_without_echoing_record_ref():
    doctor = Doctor(
        clerk_id="user_test_ehr_preview_cli",
        email="ehr-preview-cli@example.test",
        biography=None,
    ).save()
    patient = Patient(
        doctor_id=doctor.id,
        name="Synthetic CLI Fixture",
        date_of_birth=datetime(2000, 1, 1, tzinfo=UTC),
    ).save()
    record_ref = "a-195900.E-60999"
    runner = CliRunner()

    designated = runner.invoke(
        app,
        [
            "designate-athena-preview-patient",
            "--doctor-id",
            str(doctor.id),
            "--patient-id",
            str(patient.id),
            "--operator-ref",
            "operator_cli_designator",
            "--confirm-no-real-patient-data",
        ],
    )
    assert designated.exit_code == 0
    assert "Designated synthetic Preview patient" in designated.output

    provisioned = runner.invoke(
        app,
        [
            "provision-athena-preview-record",
            "--doctor-id",
            str(doctor.id),
            "--patient-id",
            str(patient.id),
            "--record-ref",
            record_ref,
            "--operator-ref",
            "operator_cli_fixture",
            "--confirm-synthetic-only",
        ],
    )

    assert provisioned.exit_code == 0
    assert "Provisioned synthetic Preview mapping" in provisioned.output
    assert record_ref not in provisioned.output
    mapping = EHRSandboxRecordMapping.get(
        doctor_id=doctor.id,
        patient_id=patient.id,
    )
    assert mapping is not None
    assert mapping.revoked_at is None

    revoked = runner.invoke(
        app,
        [
            "revoke-athena-preview-record",
            "--doctor-id",
            str(doctor.id),
            "--patient-id",
            str(patient.id),
            "--operator-ref",
            "operator_cli_revoker",
        ],
    )
    assert revoked.exit_code == 0
    assert "Revoked synthetic Preview mapping" in revoked.output
    assert record_ref not in revoked.output
    mapping.refresh()
    assert mapping.revoked_at is not None
    assert mapping.revoked_by == "operator_cli_revoker"

    designation_revoked = runner.invoke(
        app,
        [
            "revoke-athena-preview-patient-designation",
            "--doctor-id",
            str(doctor.id),
            "--patient-id",
            str(patient.id),
            "--operator-ref",
            "operator_cli_designation_revoker",
        ],
    )
    assert designation_revoked.exit_code == 0
    assert "Revoked synthetic patient designation" in designation_revoked.output
