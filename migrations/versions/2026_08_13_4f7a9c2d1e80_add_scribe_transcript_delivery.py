"""add_scribe_transcript_delivery

Revision ID: 4f7a9c2d1e80
Revises: b4f9d1c73a20
Create Date: 2026-08-13 18:00:00.000000
"""

from typing import Sequence, Union

import activemodel
import sqlalchemy as sa
from alembic import op


revision: str = "4f7a9c2d1e80"
down_revision: Union[str, None] = "b4f9d1c73a20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scribe_transcript_delivery",
        sa.Column(
            "id",
            activemodel.types.typeid.TypeIDType(prefix="scribe_delivery"),
            nullable=False,
            comment="TypeID with prefix: scribe_delivery",
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
        sa.Column("caller_client_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("scribe_session_id", sa.Uuid(), nullable=False),
        sa.Column(
            "appointment_id",
            activemodel.types.typeid.TypeIDType(prefix="appointment"),
            nullable=False,
        ),
        sa.Column(
            "transcript_id",
            activemodel.types.typeid.TypeIDType(prefix="ai_visit_transcript"),
            nullable=False,
        ),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "pipeline_dispatched_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointment.id"],
            name=op.f("scribe_transcript_delivery_appointment_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["transcript_id"],
            ["ai_visit_transcript.id"],
            name=op.f("scribe_transcript_delivery_transcript_id_fkey"),
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("scribe_transcript_delivery_pkey")
        ),
        sa.UniqueConstraint(
            "caller_client_id",
            "idempotency_key",
            name="uq_scribe_delivery_caller_idempotency_key",
        ),
        sa.UniqueConstraint(
            "caller_client_id",
            "scribe_session_id",
            name="uq_scribe_delivery_caller_session",
        ),
        comment="Non-PHI idempotency receipt for Scribe transcript delivery.",
    )
    op.create_index(
        op.f("scribe_transcript_delivery_caller_client_id_idx"),
        "scribe_transcript_delivery",
        ["caller_client_id"],
        unique=False,
    )
    op.create_index(
        op.f("scribe_transcript_delivery_scribe_session_id_idx"),
        "scribe_transcript_delivery",
        ["scribe_session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("scribe_transcript_delivery_scribe_session_id_idx"),
        table_name="scribe_transcript_delivery",
    )
    op.drop_index(
        op.f("scribe_transcript_delivery_caller_client_id_idx"),
        table_name="scribe_transcript_delivery",
    )
    op.drop_table("scribe_transcript_delivery")
