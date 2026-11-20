from datetime import UTC, datetime

import pytest
from click import unstyle
from typer.testing import CliRunner

import app.services.staging_zoom_join_test_appointments as zoom_join_service
from app.cli import app
from app.services.staging_zoom_join_test_appointments import LAB_A_DOCTOR_EMAIL

from app.models.doctor import Doctor


def _lab_a_doctor() -> Doctor:
    return Doctor(
        clerk_id="user_test_zoom_join_cli_lab_a",
        email=LAB_A_DOCTOR_EMAIL,
        biography=None,
        last_active_at=datetime.now(UTC),
    ).save()


@pytest.mark.parametrize(
    "confirmation_flags",
    [[], ["--confirm-staging-only"], ["--confirm-no-real-patient-data"]],
)
def test_cli_requires_both_explicit_confirmations(confirmation_flags):
    result = CliRunner().invoke(
        app,
        [
            "ensure-staging-zoom-join-test-appointments",
            *confirmation_flags,
        ],
    )

    assert result.exit_code == 2
    assert "--confirm-staging-only" in result.output
    assert "--confirm-no-real-patient-data" in result.output
    assert LAB_A_DOCTOR_EMAIL not in result.output
    assert "Synthetic Patient" not in result.output


def test_cli_help_exposes_run_date_and_start_time_only():
    result = CliRunner().invoke(
        app, ["ensure-staging-zoom-join-test-appointments", "--help"]
    )
    output = unstyle(result.output)

    assert result.exit_code == 0
    assert "--run-date" in output
    assert "--start-time" in output
    assert "--doctor-id" not in output
    assert "--patient-name" not in output
    assert "--date-of-birth" not in output


def test_cli_creates_then_finds_without_printing_names(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(zoom_join_service, "is_staging", lambda: True)
    _lab_a_doctor()
    command = [
        "ensure-staging-zoom-join-test-appointments",
        "--start-time",
        "2026-11-18T16:00:00",
        "--confirm-staging-only",
        "--confirm-no-real-patient-data",
    ]
    runner = CliRunner()

    created = runner.invoke(app, command)
    found = runner.invoke(app, command)

    assert created.exit_code == 0, created.output
    assert found.exit_code == 0, found.output
    assert created.output.count("created appointment_id=appointment_") == 4
    assert found.output.count("exists appointment_id=appointment_") == 4
    assert "patient_id=patient_" in created.output
    assert "external_id=zoom-synthetic-join-2026-11-17:" in created.output
    assert LAB_A_DOCTOR_EMAIL not in created.output
    assert "Synthetic Patient" not in created.output
    assert "NOT A REAL PERSON" not in created.output
    assert LAB_A_DOCTOR_EMAIL not in found.output
    assert "Synthetic Patient" not in found.output
