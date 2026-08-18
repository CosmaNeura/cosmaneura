"""add_ehr_sandbox_record_mapping

Revision ID: 91d2c4e5f6a7
Revises: 7c1d5e9a2b40
Create Date: 2026-08-17 09:00:00.000000
"""

from typing import Sequence, Union

import activemodel
import sqlalchemy as sa
from alembic import context, op


revision: str = "91d2c4e5f6a7"
down_revision: Union[str, None] = "7c1d5e9a2b40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL requires a referenced composite tuple to be unique. The
    # patient's id is already globally unique, but this explicit tuple lets the
    # designation enforce that its doctor_id is the patient's actual owner.
    op.create_unique_constraint(
        "uq_patient_id_doctor_id_ehr_sandbox",
        "patient",
        ["id", "doctor_id"],
    )
    op.create_table(
        "ehr_sandbox_patient_designation",
        sa.Column(
            "id",
            activemodel.types.typeid.TypeIDType(prefix="ehr_sandbox_designation"),
            nullable=False,
            comment="TypeID with prefix: ehr_sandbox_designation",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "doctor_id",
            activemodel.types.typeid.TypeIDType(prefix="doctor"),
            nullable=False,
        ),
        sa.Column(
            "patient_id",
            activemodel.types.typeid.TypeIDType(prefix="patient"),
            nullable=False,
        ),
        sa.Column(
            "adapter", sa.String(length=32), server_default="athena", nullable=False
        ),
        sa.Column(
            "environment",
            sa.String(length=32),
            server_default="preview",
            nullable=False,
        ),
        sa.Column(
            "practice_ref",
            sa.String(length=32),
            server_default="195900",
            nullable=False,
        ),
        sa.Column(
            "synthetic_only", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
        sa.Column("designated_by", sa.String(length=160), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(length=160), nullable=True),
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
            ["doctor_id"],
            ["doctor.id"],
            name=op.f("ehr_sandbox_patient_designation_doctor_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient.id"],
            name=op.f("ehr_sandbox_patient_designation_patient_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["patient_id", "doctor_id"],
            ["patient.id", "patient.doctor_id"],
            name="fk_ehr_sandbox_designation_patient_doctor_scope",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("ehr_sandbox_patient_designation_pkey")
        ),
        sa.UniqueConstraint(
            "id",
            "doctor_id",
            "patient_id",
            name="uq_ehr_sandbox_designation_id_doctor_patient",
        ),
        comment=(
            "Operator-controlled assertion that a platform patient contains "
            "synthetic-only athena Preview practice 195900 data."
        ),
    )
    op.create_index(
        "uq_ehr_sandbox_designation_active_patient",
        "ehr_sandbox_patient_designation",
        ["doctor_id", "patient_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "ehr_sandbox_record_mapping",
        sa.Column(
            "id",
            activemodel.types.typeid.TypeIDType(prefix="ehr_sandbox_mapping"),
            nullable=False,
            comment="TypeID with prefix: ehr_sandbox_mapping",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "doctor_id",
            activemodel.types.typeid.TypeIDType(prefix="doctor"),
            nullable=False,
        ),
        sa.Column(
            "patient_id",
            activemodel.types.typeid.TypeIDType(prefix="patient"),
            nullable=False,
        ),
        sa.Column(
            "designation_id",
            activemodel.types.typeid.TypeIDType(prefix="ehr_sandbox_designation"),
            nullable=False,
        ),
        sa.Column(
            "adapter", sa.String(length=32), server_default="athena", nullable=False
        ),
        sa.Column(
            "environment",
            sa.String(length=32),
            server_default="preview",
            nullable=False,
        ),
        sa.Column(
            "practice_ref",
            sa.String(length=32),
            server_default="195900",
            nullable=False,
        ),
        sa.Column("record_ref", sa.String(length=160), nullable=False),
        sa.Column(
            "synthetic_only",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column("provisioned_by", sa.String(length=160), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(length=160), nullable=True),
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
            ["doctor_id"],
            ["doctor.id"],
            name=op.f("ehr_sandbox_record_mapping_doctor_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient.id"],
            name=op.f("ehr_sandbox_record_mapping_patient_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["designation_id"],
            ["ehr_sandbox_patient_designation.id"],
            name=op.f("ehr_sandbox_record_mapping_designation_id_fkey"),
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
        sa.PrimaryKeyConstraint("id", name=op.f("ehr_sandbox_record_mapping_pkey")),
        comment=(
            "Operator-controlled binding to one synthetic sandbox EHR record. "
            "Restricted to athena Preview practice 195900."
        ),
    )
    op.create_index(
        "uq_ehr_sandbox_mapping_active_patient",
        "ehr_sandbox_record_mapping",
        ["doctor_id", "patient_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "uq_ehr_sandbox_mapping_record_history",
        "ehr_sandbox_record_mapping",
        ["adapter", "environment", "practice_ref", "record_ref"],
        unique=True,
    )
    op.create_index(
        op.f("ehr_sandbox_record_mapping_patient_id_idx"),
        "ehr_sandbox_record_mapping",
        ["patient_id"],
        unique=False,
    )


def downgrade() -> None:
    if not context.is_offline_mode():
        populated = (
            op.get_bind()
            .execute(
                sa.text(
                    "SELECT EXISTS(SELECT 1 FROM ehr_sandbox_record_mapping) "
                    "OR EXISTS(SELECT 1 FROM ehr_sandbox_patient_designation)"
                )
            )
            .scalar_one()
        )
        if populated:
            raise RuntimeError(
                "Refusing to discard populated sandbox EHR mapping history."
            )

    op.drop_index(
        op.f("ehr_sandbox_record_mapping_patient_id_idx"),
        table_name="ehr_sandbox_record_mapping",
    )
    op.drop_index(
        "uq_ehr_sandbox_mapping_record_history",
        table_name="ehr_sandbox_record_mapping",
    )
    op.drop_index(
        "uq_ehr_sandbox_mapping_active_patient",
        table_name="ehr_sandbox_record_mapping",
    )
    op.drop_table("ehr_sandbox_record_mapping")
    op.drop_index(
        "uq_ehr_sandbox_designation_active_patient",
        table_name="ehr_sandbox_patient_designation",
    )
    op.drop_table("ehr_sandbox_patient_designation")
    op.drop_constraint(
        "uq_patient_id_doctor_id_ehr_sandbox",
        "patient",
        type_="unique",
    )
