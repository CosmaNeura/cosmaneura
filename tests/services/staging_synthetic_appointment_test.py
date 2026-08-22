from datetime import UTC, datetime

import pytest

import app.services.staging_synthetic_appointment as staging_fixture_service
from app.services.staging_synthetic_appointment import (
    SYNTHETIC_APPOINTMENT_DATE,
    SYNTHETIC_APPOINTMENT_NAME,
    SYNTHETIC_APPOINTMENT_NOTES,
    SYNTHETIC_CREATED_BY_SYSTEM,
    SYNTHETIC_PATIENT_DATE_OF_BIRTH,
    SYNTHETIC_PATIENT_NAME,
    StagingSyntheticAppointmentConflict,
    StagingSyntheticAppointmentError,
    ensure_staging_synthetic_appointment,
)

from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.ehr_sandbox_record_mapping import (
    EHRSandboxPatientDesignation,
    EHRSandboxRecordMapping,
)
from app.models.patient import Patient
from app.models.synthetic_preview_appointment_fixture import (
    SYNTHETIC_PREVIEW_FIXTURE_VERSION,
    SyntheticPreviewAppointmentFixture,
)
from sqlalchemy import CheckConstraint, ForeignKeyConstraint
from sqlmodel import SQLModel


def _doctor(suffix: str = "owner") -> Doctor:
    return Doctor(
        clerk_id=f"user_test_synthetic_preview_{suffix}",
        email=f"synthetic-preview-{suffix}@example.test",
        biography=None,
        last_active_at=datetime.now(UTC),
    ).save()


def _enable_staging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(staging_fixture_service, "is_staging", lambda: True)


def test_ensure_creates_fixed_synthetic_rows_and_no_ehr_authority(monkeypatch):
    _enable_staging(monkeypatch)
    doctor = _doctor()

    result = ensure_staging_synthetic_appointment(
        doctor_id=doctor.id,
        doctor_email=doctor.email,
        operator_ref="operator_staging_fixture_creator",
    )

    assert result.created is True
    assert result.patient.doctor_id == doctor.id
    assert result.patient.name == SYNTHETIC_PATIENT_NAME
    assert result.patient.date_of_birth == SYNTHETIC_PATIENT_DATE_OF_BIRTH
    assert result.appointment.doctor_id == doctor.id
    assert result.appointment.patient_id == result.patient.id
    assert result.appointment.appointment_date == SYNTHETIC_APPOINTMENT_DATE
    assert result.appointment.appointment_name == SYNTHETIC_APPOINTMENT_NAME
    assert result.appointment.appointment_notes_preview == SYNTHETIC_APPOINTMENT_NOTES
    assert result.appointment.created_by_system == SYNTHETIC_CREATED_BY_SYSTEM
    assert result.appointment.external_id is None
    assert result.fixture.fixture_version == SYNTHETIC_PREVIEW_FIXTURE_VERSION
    assert result.fixture.synthetic_only is True
    assert result.fixture.provisioned_by == "operator_staging_fixture_creator"
    assert EHRSandboxPatientDesignation.count() == 0
    assert EHRSandboxRecordMapping.count() == 0


def test_ensure_is_idempotent_and_preserves_original_operator_audit(monkeypatch):
    _enable_staging(monkeypatch)
    doctor = _doctor("idempotent")

    first = ensure_staging_synthetic_appointment(
        doctor_id=doctor.id,
        doctor_email=doctor.email,
        operator_ref="operator_original_creator",
    )
    second = ensure_staging_synthetic_appointment(
        doctor_id=doctor.id,
        doctor_email=doctor.email,
        operator_ref="operator_retry_actor",
    )

    assert first.created is True
    assert second.created is False
    assert second.patient.id == first.patient.id
    assert second.appointment.id == first.appointment.id
    assert second.fixture.id == first.fixture.id
    assert second.fixture.provisioned_by == "operator_original_creator"
    assert (
        SyntheticPreviewAppointmentFixture.where(
            SyntheticPreviewAppointmentFixture.doctor_id == doctor.id
        ).count()
        == 1
    )
    assert Patient.where(Patient.doctor_id == doctor.id).count() == 1
    assert Appointment.where(Appointment.doctor_id == doctor.id).count() == 1


def test_ensure_requires_staging_exact_doctor_email_and_safe_operator(monkeypatch):
    doctor = _doctor("gates")
    patient_count = Patient.count()

    monkeypatch.setattr(staging_fixture_service, "is_staging", lambda: False)
    with pytest.raises(StagingSyntheticAppointmentError, match="restricted to staging"):
        ensure_staging_synthetic_appointment(
            doctor_id=doctor.id,
            doctor_email=doctor.email,
            operator_ref="operator_environment_refusal",
        )

    _enable_staging(monkeypatch)
    with pytest.raises(StagingSyntheticAppointmentError, match="did not match"):
        ensure_staging_synthetic_appointment(
            doctor_id=doctor.id,
            doctor_email="different-provider@example.test",
            operator_ref="operator_identity_refusal",
        )
    with pytest.raises(StagingSyntheticAppointmentError, match="did not match"):
        ensure_staging_synthetic_appointment(
            doctor_id=doctor.id,
            doctor_email=doctor.email.upper(),
            operator_ref="operator_email_case_refusal",
        )
    with pytest.raises(StagingSyntheticAppointmentError, match="safe email"):
        ensure_staging_synthetic_appointment(
            doctor_id=doctor.id,
            doctor_email=f" {doctor.email}",
            operator_ref="operator_email_whitespace_refusal",
        )
    with pytest.raises(StagingSyntheticAppointmentError, match="opaque audit"):
        ensure_staging_synthetic_appointment(
            doctor_id=doctor.id,
            doctor_email=doctor.email,
            operator_ref="operator contains spaces",
        )

    assert Patient.count() == patient_count
    assert (
        SyntheticPreviewAppointmentFixture.where(
            SyntheticPreviewAppointmentFixture.doctor_id == doctor.id
        ).count()
        == 0
    )


def test_ensure_refuses_doctor_who_has_not_signed_in(monkeypatch):
    _enable_staging(monkeypatch)
    doctor = Doctor(
        clerk_id="user_test_synthetic_preview_never_signed_in",
        email="never-signed-in@example.test",
        biography=None,
        last_active_at=None,
    ).save()

    with pytest.raises(StagingSyntheticAppointmentError, match="signed-in"):
        ensure_staging_synthetic_appointment(
            doctor_id=doctor.id,
            doctor_email=doctor.email,
            operator_ref="operator_unsigned_identity_refusal",
        )

    assert (
        SyntheticPreviewAppointmentFixture.where(
            SyntheticPreviewAppointmentFixture.doctor_id == doctor.id
        ).count()
        == 0
    )


def test_ensure_refuses_deleted_doctor(monkeypatch):
    _enable_staging(monkeypatch)
    doctor = _doctor("deleted")
    doctor.deleted_at = datetime.now(UTC)
    doctor.save()

    with pytest.raises(StagingSyntheticAppointmentError, match="signed-in"):
        ensure_staging_synthetic_appointment(
            doctor_id=doctor.id,
            doctor_email=doctor.email,
            operator_ref="operator_deleted_identity_refusal",
        )


def test_ensure_refuses_tampered_fixed_fixture(monkeypatch):
    _enable_staging(monkeypatch)
    doctor = _doctor("tampered")
    result = ensure_staging_synthetic_appointment(
        doctor_id=doctor.id,
        doctor_email=doctor.email,
        operator_ref="operator_tamper_fixture",
    )
    result.patient.name = "Not the governed synthetic fixture"
    result.patient.save()

    with pytest.raises(StagingSyntheticAppointmentConflict, match="integrity check"):
        ensure_staging_synthetic_appointment(
            doctor_id=doctor.id,
            doctor_email=doctor.email,
            operator_ref="operator_tamper_retry",
        )


def test_fixture_schema_enforces_synthetic_provenance_and_doctor_scope():
    table = SQLModel.metadata.tables["synthetic_preview_appointment_fixture"]
    constraints = {constraint.name: constraint for constraint in table.constraints}

    assert set(table.columns.keys()) == {
        "id",
        "created_at",
        "updated_at",
        "doctor_id",
        "patient_id",
        "appointment_id",
        "fixture_version",
        "synthetic_only",
        "provisioned_by",
    }
    version_constraint = constraints[
        "synthetic_preview_appointment_fixture_"
        "ck_synthetic_preview_fixture_version_check"
    ]
    synthetic_constraint = constraints[
        "synthetic_preview_appointment_fixture_"
        "ck_synthetic_preview_fixture_synthetic_only_check"
    ]
    assert isinstance(version_constraint, CheckConstraint)
    assert isinstance(synthetic_constraint, CheckConstraint)
    assert str(version_constraint.sqltext) == (
        "fixture_version = 'synthetic_preview_labs_v1'"
    )
    assert str(synthetic_constraint.sqltext) == "synthetic_only IS TRUE"
    assert constraints["uq_synthetic_preview_fixture_doctor"] is not None
    assert constraints["uq_synthetic_preview_fixture_appointment"] is not None
    scoped_fk = constraints["fk_synthetic_preview_fixture_patient_doctor_scope"]
    assert isinstance(scoped_fk, ForeignKeyConstraint)
    assert [element.parent.name for element in scoped_fk.elements] == [
        "patient_id",
        "doctor_id",
    ]
    assert [element.target_fullname for element in scoped_fk.elements] == [
        "patient.id",
        "patient.doctor_id",
    ]


def test_fixed_dates_are_timezone_explicit():
    assert SYNTHETIC_PATIENT_DATE_OF_BIRTH == datetime(2000, 1, 1, tzinfo=UTC).replace(
        tzinfo=None
    )
    assert SYNTHETIC_APPOINTMENT_DATE == datetime(
        2099, 1, 1, 15, 0, tzinfo=UTC
    ).replace(tzinfo=None)
