from datetime import datetime

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field

from .doctor import Doctor
from .patient import Patient

ATHENA_ADAPTER = "athena"
ATHENA_PREVIEW_ENVIRONMENT = "preview"
ATHENA_PREVIEW_PRACTICE = "195900"


class EHRSandboxPatientDesignation(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("ehr_sandbox_designation"),
    table=True,
):
    """Operator-controlled assertion that a platform patient contains synthetic-only athena Preview practice 195900 data."""

    __table_args__ = (
        sa.CheckConstraint(
            "adapter = 'athena'",
            name="ck_ehr_sandbox_designation_athena_only",
        ),
        sa.CheckConstraint(
            "environment = 'preview'",
            name="ck_ehr_sandbox_designation_preview_only",
        ),
        sa.CheckConstraint(
            "practice_ref = '195900'",
            name="ck_ehr_sandbox_designation_practice_195900_only",
        ),
        sa.CheckConstraint(
            "synthetic_only IS TRUE",
            name="ck_ehr_sandbox_designation_synthetic_only",
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL AND revoked_by IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_by IS NOT NULL)",
            name="ck_ehr_sandbox_designation_revocation_complete",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id", "doctor_id"],
            ["patient.id", "patient.doctor_id"],
            name="fk_ehr_sandbox_designation_patient_doctor_scope",
        ),
        sa.UniqueConstraint(
            "id",
            "doctor_id",
            "patient_id",
            name="uq_ehr_sandbox_designation_id_doctor_patient",
        ),
        sa.Index(
            "uq_ehr_sandbox_designation_active_patient",
            "doctor_id",
            "patient_id",
            unique=True,
            postgresql_where=sa.text("revoked_at IS NULL"),
        ),
    )

    doctor_id: TypeIDType = Doctor.foreign_key()
    patient_id: TypeIDType = Patient.foreign_key()
    adapter: str = Field(
        default=ATHENA_ADAPTER,
        sa_column=Column(sa.String(32), nullable=False, server_default=ATHENA_ADAPTER),
    )
    environment: str = Field(
        default=ATHENA_PREVIEW_ENVIRONMENT,
        sa_column=Column(
            sa.String(32),
            nullable=False,
            server_default=ATHENA_PREVIEW_ENVIRONMENT,
        ),
    )
    practice_ref: str = Field(
        default=ATHENA_PREVIEW_PRACTICE,
        sa_column=Column(
            sa.String(32), nullable=False, server_default=ATHENA_PREVIEW_PRACTICE
        ),
    )
    synthetic_only: bool = Field(
        default=True,
        sa_column=Column(sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    designated_by: str = Field(sa_column=Column(sa.String(160), nullable=False))
    revoked_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    revoked_by: str | None = Field(
        default=None,
        sa_column=Column(sa.String(160), nullable=True),
    )

    def before_save(self) -> None:
        if (
            self.adapter != ATHENA_ADAPTER
            or self.environment != ATHENA_PREVIEW_ENVIRONMENT
            or self.practice_ref != ATHENA_PREVIEW_PRACTICE
            or self.synthetic_only is not True
        ):
            raise ValueError(
                "synthetic designations are restricted to athena Preview "
                "practice 195900"
            )
        if (self.revoked_at is None) != (self.revoked_by is None):
            raise ValueError("revocation timestamp and actor must be set together")


class EHRSandboxRecordMapping(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("ehr_sandbox_mapping"),
    table=True,
):
    """Operator-controlled binding to one synthetic sandbox EHR record. Restricted to athena Preview practice 195900."""

    __table_args__ = (
        sa.CheckConstraint(
            "adapter = 'athena'",
            name="ck_ehr_sandbox_mapping_athena_only",
        ),
        sa.CheckConstraint(
            "environment = 'preview'",
            name="ck_ehr_sandbox_mapping_preview_only",
        ),
        sa.CheckConstraint(
            "practice_ref = '195900'",
            name="ck_ehr_sandbox_mapping_practice_195900_only",
        ),
        sa.CheckConstraint(
            "synthetic_only IS TRUE",
            name="ck_ehr_sandbox_mapping_synthetic_only",
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL AND revoked_by IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_by IS NOT NULL)",
            name="ck_ehr_sandbox_mapping_revocation_complete",
        ),
        sa.ForeignKeyConstraint(
            ["designation_id", "doctor_id", "patient_id"],
            [
                "ehr_sandbox_patient_designation.id",
                "ehr_sandbox_patient_designation.doctor_id",
                "ehr_sandbox_patient_designation.patient_id",
            ],
            name="fk_ehr_sandbox_mapping_designation_scope",
        ),
        sa.Index(
            "uq_ehr_sandbox_mapping_active_patient",
            "doctor_id",
            "patient_id",
            unique=True,
            postgresql_where=sa.text("revoked_at IS NULL"),
        ),
        sa.Index(
            "uq_ehr_sandbox_mapping_record_history",
            "adapter",
            "environment",
            "practice_ref",
            "record_ref",
            unique=True,
        ),
        sa.Index(
            "ehr_sandbox_record_mapping_patient_id_idx",
            "patient_id",
        ),
    )

    doctor_id: TypeIDType = Doctor.foreign_key()
    patient_id: TypeIDType = Patient.foreign_key()
    designation_id: TypeIDType = EHRSandboxPatientDesignation.foreign_key()

    adapter: str = Field(
        default=ATHENA_ADAPTER,
        sa_column=Column(sa.String(32), nullable=False, server_default=ATHENA_ADAPTER),
    )
    environment: str = Field(
        default=ATHENA_PREVIEW_ENVIRONMENT,
        sa_column=Column(
            sa.String(32),
            nullable=False,
            server_default=ATHENA_PREVIEW_ENVIRONMENT,
        ),
    )
    practice_ref: str = Field(
        default=ATHENA_PREVIEW_PRACTICE,
        sa_column=Column(
            sa.String(32), nullable=False, server_default=ATHENA_PREVIEW_PRACTICE
        ),
    )
    record_ref: str = Field(sa_column=Column(sa.String(160), nullable=False))
    synthetic_only: bool = Field(
        default=True,
        sa_column=Column(sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    provisioned_by: str = Field(sa_column=Column(sa.String(160), nullable=False))
    revoked_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    revoked_by: str | None = Field(
        default=None,
        sa_column=Column(sa.String(160), nullable=True),
    )

    def before_save(self) -> None:
        if (
            self.adapter != ATHENA_ADAPTER
            or self.environment != ATHENA_PREVIEW_ENVIRONMENT
            or self.practice_ref != ATHENA_PREVIEW_PRACTICE
            or self.synthetic_only is not True
        ):
            raise ValueError(
                "sandbox mappings are restricted to synthetic athena Preview "
                "practice 195900 records"
            )
        if (self.revoked_at is None) != (self.revoked_by is None):
            raise ValueError("revocation timestamp and actor must be set together")
