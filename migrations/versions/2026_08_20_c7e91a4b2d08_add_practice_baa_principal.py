"""add practice as BAA principal

Revision ID: c7e91a4b2d08
Revises: 91d2c4e5f6a7
Create Date: 2026-08-20 03:30:00.000000
"""

from typing import Sequence, Union

import activemodel
import sqlalchemy as sa
from alembic import op


revision: str = "c7e91a4b2d08"
down_revision: Union[str, None] = "91d2c4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "practice",
        sa.Column(
            "id",
            activemodel.types.typeid.TypeIDType(prefix="practice"),
            nullable=False,
            comment="TypeID with prefix: practice",
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
        sa.Column("legal_name", sa.Text(), nullable=False),
        sa.Column("npi", sa.String(length=10), nullable=True),
        sa.Column("city", sa.String(length=80), nullable=True),
        sa.Column("region", sa.String(length=40), nullable=True),
        sa.Column("is_solo", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("join_code", sa.String(length=16), nullable=False),
        sa.Column(
            "email_domain",
            sa.String(length=253),
            nullable=False,
            comment="Workforce must sign in from this domain (or a subdomain) to join.",
        ),
        sa.Column(
            "origin_channel",
            sa.String(length=16),
            server_default="direct",
            nullable=False,
        ),
        sa.Column(
            "pastoral_pack", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("baa_version", sa.String(length=64), nullable=True),
        sa.Column("baa_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "baa_accepted_by_doctor_id",
            activemodel.types.typeid.TypeIDType(prefix="doctor"),
            nullable=True,
        ),
        sa.Column("baa_signature_name", sa.String(length=120), nullable=True),
        sa.Column("baa_signature_title", sa.String(length=120), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("practice_pkey")),
        sa.UniqueConstraint("join_code", name="practice_join_code_key"),
        comment="Covered entity. The BAA principal for Cosmas.",
    )
    op.create_index("practice_join_code_idx", "practice", ["join_code"], unique=True)

    op.create_table(
        "practice_member",
        sa.Column(
            "id",
            activemodel.types.typeid.TypeIDType(prefix="practice_member"),
            nullable=False,
            comment="TypeID with prefix: practice_member",
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
            "practice_id",
            activemodel.types.typeid.TypeIDType(prefix="practice"),
            nullable=False,
        ),
        sa.Column(
            "doctor_id",
            activemodel.types.typeid.TypeIDType(prefix="doctor"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("attested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attestation_name", sa.String(length=120), nullable=True),
        sa.ForeignKeyConstraint(
            ["practice_id"],
            ["practice.id"],
            name=op.f("practice_member_practice_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["doctor_id"],
            ["doctor.id"],
            name=op.f("practice_member_doctor_id_fkey"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("practice_member_pkey")),
        sa.UniqueConstraint("doctor_id", name="practice_member_doctor_id_key"),
        comment="One Cosmas account belongs to at most one practice.",
    )


def downgrade() -> None:
    op.drop_table("practice_member")
    op.drop_index("practice_join_code_idx", table_name="practice")
    op.drop_table("practice")
