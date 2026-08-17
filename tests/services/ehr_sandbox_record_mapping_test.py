from datetime import UTC, datetime

import pytest

from app.services.ehr_sandbox_record_mapping import (
    SandboxRecordMappingConflict,
    SandboxRecordMappingError,
    SandboxRecordMappingNotFound,
    designate_athena_preview_patient,
    provision_athena_preview_mapping,
    revoke_athena_preview_mapping,
    revoke_athena_preview_patient_designation,
)

import sqlalchemy as sa
from app.models.doctor import Doctor
from app.models.ehr_sandbox_record_mapping import (
    ATHENA_ADAPTER,
    ATHENA_PREVIEW_ENVIRONMENT,
    ATHENA_PREVIEW_PRACTICE,
    EHRSandboxRecordMapping,
)
from app.models.patient import Patient
from sqlmodel import SQLModel

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


def _designate(doctor: Doctor, patient: Patient) -> None:
    designate_athena_preview_patient(
        doctor_id=doctor.id,
        patient_id=patient.id,
        operator_ref="operator_synthetic_designator",
    )


def test_provisioning_is_fixed_to_synthetic_athena_preview_195900_and_idempotent():
    doctor = _doctor("user_test_ehr_preview_owner")
    patient = _patient(doctor)
    _designate(doctor, patient)

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

    with pytest.raises(SandboxRecordMappingConflict, match="lacks an active"):
        provision_athena_preview_mapping(
            doctor_id=owner.id,
            patient_id=patient.id,
            record_ref=RECORD_REF,
            operator_ref=OPERATOR_REF,
        )
    _designate(owner, patient)

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
    _designate(doctor, first_patient)
    _designate(doctor, second_patient)
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

    with pytest.raises(SandboxRecordMappingConflict, match="mapping history"):
        provision_athena_preview_mapping(
            doctor_id=doctor.id,
            patient_id=second_patient.id,
            record_ref=RECORD_REF,
            operator_ref=OPERATOR_REF,
        )


def test_revocation_is_durable_and_allows_deliberate_reprovisioning():
    doctor = _doctor("user_test_ehr_preview_revoke")
    patient = _patient(doctor)
    _designate(doctor, patient)
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

    reassignment_target = _patient(doctor, "Synthetic Reassignment Target")
    _designate(doctor, reassignment_target)
    with pytest.raises(SandboxRecordMappingConflict, match="mapping history"):
        provision_athena_preview_mapping(
            doctor_id=doctor.id,
            patient_id=reassignment_target.id,
            record_ref=RECORD_REF,
            operator_ref="operator_unsafe_record_reuser",
        )

    designation = revoke_athena_preview_patient_designation(
        doctor_id=doctor.id,
        patient_id=patient.id,
        operator_ref="operator_designation_revoker",
    )
    assert designation.revoked_at is not None

    with pytest.raises(SandboxRecordMappingConflict, match="lacks an active"):
        provision_athena_preview_mapping(
            doctor_id=doctor.id,
            patient_id=patient.id,
            record_ref="a-195900.E-60179",
            operator_ref="operator_reprovisioner",
        )
    _designate(doctor, patient)

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


def test_model_metadata_matches_migration_safety_boundaries() -> None:
    designation = SQLModel.metadata.tables["ehr_sandbox_patient_designation"]
    mapping = SQLModel.metadata.tables["ehr_sandbox_record_mapping"]

    assert designation.comment == (
        "Operator-controlled assertion that a platform patient contains "
        "synthetic-only athena Preview practice 195900 data."
    )
    assert mapping.comment == (
        "Operator-controlled binding to one synthetic sandbox EHR record. "
        "Restricted to athena Preview practice 195900."
    )

    expected_checks = {
        "ehr_sandbox_patient_designation_ck_ehr_sandbox_designation_athena_only_check": (
            "adapter = 'athena'"
        ),
        "ehr_sandbox_patient_designation_ck_ehr_sandbox_designation_preview_only_check": (
            "environment = 'preview'"
        ),
        "ehr_sandbox_patient_designation_ck_ehr_sandbox_designation_practice_195900_only_check": (
            "practice_ref = '195900'"
        ),
        "ehr_sandbox_patient_designation_ck_ehr_sandbox_designation_synthetic_only_check": (
            "synthetic_only IS TRUE"
        ),
        "ehr_sandbox_patient_designation_ck_ehr_sandbox_designation_revocation_complete_check": (
            "(revoked_at IS NULL AND revoked_by IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_by IS NOT NULL)"
        ),
        "ehr_sandbox_record_mapping_ck_ehr_sandbox_mapping_athena_only_check": (
            "adapter = 'athena'"
        ),
        "ehr_sandbox_record_mapping_ck_ehr_sandbox_mapping_preview_only_check": (
            "environment = 'preview'"
        ),
        "ehr_sandbox_record_mapping_ck_ehr_sandbox_mapping_practice_195900_only_check": (
            "practice_ref = '195900'"
        ),
        "ehr_sandbox_record_mapping_ck_ehr_sandbox_mapping_synthetic_only_check": (
            "synthetic_only IS TRUE"
        ),
        "ehr_sandbox_record_mapping_ck_ehr_sandbox_mapping_revocation_complete_check": (
            "(revoked_at IS NULL AND revoked_by IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_by IS NOT NULL)"
        ),
    }
    actual_checks = {
        constraint.name: str(constraint.sqltext)
        for table in (designation, mapping)
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    assert actual_checks == expected_checks

    indexes: dict[str, sa.Index] = {
        str(index.name): index
        for table in (designation, mapping)
        for index in table.indexes
        if index.name is not None
    }
    assert set(indexes) == {
        "uq_ehr_sandbox_designation_active_patient",
        "uq_ehr_sandbox_mapping_active_patient",
        "uq_ehr_sandbox_mapping_record_history",
        "ehr_sandbox_record_mapping_patient_id_idx",
    }
    for name in (
        "uq_ehr_sandbox_designation_active_patient",
        "uq_ehr_sandbox_mapping_active_patient",
    ):
        assert indexes[name].unique is True
        assert (
            str(indexes[name].dialect_options["postgresql"]["where"])
            == "revoked_at IS NULL"
        )
    history = indexes["uq_ehr_sandbox_mapping_record_history"]
    assert history.unique is True
    assert [column.name for column in history.columns] == [
        "adapter",
        "environment",
        "practice_ref",
        "record_ref",
    ]
    assert history.dialect_options["postgresql"]["where"] is None

    expected_defaults = {
        "adapter": ATHENA_ADAPTER,
        "environment": ATHENA_PREVIEW_ENVIRONMENT,
        "practice_ref": ATHENA_PREVIEW_PRACTICE,
        "synthetic_only": "true",
    }
    for table in (designation, mapping):
        actual_defaults: dict[str, str] = {}
        for column_name in expected_defaults:
            default = table.c[column_name].server_default
            assert isinstance(default, sa.DefaultClause)
            actual_defaults[column_name] = str(default.arg)
        assert actual_defaults == expected_defaults
