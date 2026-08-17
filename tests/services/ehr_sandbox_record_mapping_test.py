from datetime import UTC, datetime

import pytest

from app.services.ehr_sandbox_record_mapping import (
    SandboxRecordMappingConflict,
    SandboxRecordMappingError,
    SandboxRecordMappingNotFound,
    provision_athena_preview_mapping,
    revoke_athena_preview_mapping,
)

from app.models.doctor import Doctor
from app.models.ehr_sandbox_record_mapping import (
    ATHENA_ADAPTER,
    ATHENA_PREVIEW_ENVIRONMENT,
    ATHENA_PREVIEW_PRACTICE,
    EHRSandboxRecordMapping,
)
from app.models.patient import Patient

RECORD_REF = "a-195900.E-60178"
OPERATOR_REF = "operator_preview_provisioner"


def _doctor(clerk_id: str) -> Doctor:
    return Doctor(
        clerk_id=clerk_id,
        email=f"{clerk_id}@example.test",
        biography=None,
    ).save()


def _patient(doctor: Doctor, name: str = "Synthetic Preview Record") -> Patient:
    return Patient(
        doctor_id=doctor.id,
        name=name,
        date_of_birth=datetime(2000, 1, 1, tzinfo=UTC),
    ).save()


def test_provisioning_is_fixed_to_synthetic_athena_preview_195900_and_idempotent():
    doctor = _doctor("user_test_ehr_preview_owner")
    patient = _patient(doctor)

    first = provision_athena_preview_mapping(
        doctor_id=doctor.id,
        patient_id=patient.id,
        record_ref=RECORD_REF,
        operator_ref=OPERATOR_REF,
    )
    second = provision_athena_preview_mapping(
        doctor_id=doctor.id,
        patient_id=patient.id,
        record_ref=RECORD_REF,
        operator_ref="operator_retry",
    )

    assert first.id == second.id
    assert first.adapter == ATHENA_ADAPTER
    assert first.environment == ATHENA_PREVIEW_ENVIRONMENT
    assert first.practice_ref == ATHENA_PREVIEW_PRACTICE
    assert first.synthetic_only is True
    assert first.provisioned_by == OPERATOR_REF
    assert first.revoked_at is None
    assert EHRSandboxRecordMapping.count() == 1


def test_provisioning_refuses_cross_doctor_patient_and_nonopaque_references():
    owner = _doctor("user_test_ehr_preview_real_owner")
    other = _doctor("user_test_ehr_preview_other_owner")
    patient = _patient(owner)

    with pytest.raises(SandboxRecordMappingNotFound):
        provision_athena_preview_mapping(
            doctor_id=other.id,
            patient_id=patient.id,
            record_ref=RECORD_REF,
            operator_ref=OPERATOR_REF,
        )

    for unsafe in ("Jane Smith", " record_123", "record/123", "x"):
        with pytest.raises(SandboxRecordMappingError):
            provision_athena_preview_mapping(
                doctor_id=owner.id,
                patient_id=patient.id,
                record_ref=unsafe,
                operator_ref=OPERATOR_REF,
            )


def test_active_scope_and_record_are_unique_and_race_failures_are_safe():
    doctor = _doctor("user_test_ehr_preview_unique")
    first_patient = _patient(doctor, "Synthetic One")
    second_patient = _patient(doctor, "Synthetic Two")
    provision_athena_preview_mapping(
        doctor_id=doctor.id,
        patient_id=first_patient.id,
        record_ref=RECORD_REF,
        operator_ref=OPERATOR_REF,
    )

    with pytest.raises(SandboxRecordMappingConflict, match="scope already"):
        provision_athena_preview_mapping(
            doctor_id=doctor.id,
            patient_id=first_patient.id,
            record_ref="a-195900.E-60179",
            operator_ref=OPERATOR_REF,
        )

    with pytest.raises(SandboxRecordMappingConflict, match="already mapped"):
        provision_athena_preview_mapping(
            doctor_id=doctor.id,
            patient_id=second_patient.id,
            record_ref=RECORD_REF,
            operator_ref=OPERATOR_REF,
        )


def test_revocation_is_durable_and_allows_deliberate_reprovisioning():
    doctor = _doctor("user_test_ehr_preview_revoke")
    patient = _patient(doctor)
    original = provision_athena_preview_mapping(
        doctor_id=doctor.id,
        patient_id=patient.id,
        record_ref=RECORD_REF,
        operator_ref=OPERATOR_REF,
    )

    revoked = revoke_athena_preview_mapping(
        doctor_id=doctor.id,
        patient_id=patient.id,
        operator_ref="operator_revoker",
    )
    assert revoked.id == original.id
    assert revoked.revoked_at is not None
    assert revoked.revoked_by == "operator_revoker"

    replacement = provision_athena_preview_mapping(
        doctor_id=doctor.id,
        patient_id=patient.id,
        record_ref="a-195900.E-60179",
        operator_ref="operator_reprovisioner",
    )
    assert replacement.id != original.id
    assert replacement.revoked_at is None
    assert EHRSandboxRecordMapping.count() == 2

    with pytest.raises(SandboxRecordMappingNotFound):
        revoke_athena_preview_mapping(
            doctor_id=doctor.id,
            patient_id=_patient(doctor, "Never Mapped").id,
            operator_ref="operator_revoker",
        )
