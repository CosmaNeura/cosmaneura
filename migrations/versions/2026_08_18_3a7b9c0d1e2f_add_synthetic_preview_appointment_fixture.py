"""add synthetic preview appointment fixture

Revision ID: 3a7b9c0d1e2f
Revises: 2f6a7b8c9d10
Create Date: 2026-08-18 21:00:00.000000
"""

from typing import Sequence, Union

import activemodel
import sqlalchemy as sa
from alembic import context, op


revision: str = "3a7b9c0d1e2f"
down_revision: Union[str, None] = "2f6a7b8c9d10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "synthetic_preview_appointment_fixture",
        sa.Column(
            "id",
            activemodel.types.typeid.TypeIDType(prefix="synthetic_preview_fixture"),
            nullable=False,
            comment="TypeID with prefix: synthetic_preview_fixture",
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
            "appointment_id",
            activemodel.types.typeid.TypeIDType(prefix="appointment"),
            nullable=False,
        ),
        sa.Column(
            "fixture_version",
            sa.String(length=64),
            server_default="synthetic_preview_labs_v1",
            nullable=False,
        ),
        sa.Column(
            "synthetic_only",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column("provisioned_by", sa.String(length=160), nullable=False),
        sa.CheckConstraint(
            "fixture_version = 'synthetic_preview_labs_v1'",
            name="ck_synthetic_preview_fixture_version",
        ),
        sa.CheckConstraint(
            "synthetic_only IS TRUE",
            name="ck_synthetic_preview_fixture_synthetic_only",
        ),
        sa.ForeignKeyConstraint(
            ["doctor_id"],
            ["doctor.id"],
            name=op.f("synthetic_preview_appointment_fixture_doctor_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient.id"],
            name=op.f("synthetic_preview_appointment_fixture_patient_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointment.id"],
            name=op.f("synthetic_preview_appointment_fixture_appointment_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["patient_id", "doctor_id"],
            ["patient.id", "patient.doctor_id"],
            name="fk_synthetic_preview_fixture_patient_doctor_scope",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("synthetic_preview_appointment_fixture_pkey"),
        ),
        sa.UniqueConstraint(
            "doctor_id",
            name="uq_synthetic_preview_fixture_doctor",
        ),
        sa.UniqueConstraint(
            "appointment_id",
            name="uq_synthetic_preview_fixture_appointment",
        ),
        comment=(
            "Durable provenance for one operator-created synthetic staging "
            "encounter.\n\n"
            "This model is deliberately not exposed by an API route. It lets the\n"
            "staging-only operator command be idempotent without searching for a "
            "patient\n"
            "by name or overloading an external-system identifier."
        ),
    )


def downgrade() -> None:
    if not context.is_offline_mode():
        populated = (
            op.get_bind()
            .execute(
                sa.text(
                    "SELECT EXISTS(SELECT 1 FROM "
                    "synthetic_preview_appointment_fixture)"
                )
            )
            .scalar_one()
        )
        if populated:
            raise RuntimeError(
                "Refusing to discard synthetic fixture provisioning history."
            )

    op.drop_table("synthetic_preview_appointment_fixture")
