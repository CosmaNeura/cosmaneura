"""add_self_delete_account_fields

Additive, reversible columns for Apple 5.1.1(v) self-delete:
- doctor.account_deletion_status tracks deletion_pending / deleted
- practice.owner_reassignment_needed_at flags a practice left ownerless

Revision ID: c764298c7590
Revises: 312b217ebba4
Create Date: 2026-11-02 12:20:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c764298c7590"
down_revision: Union[str, None] = "312b217ebba4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "doctor",
        sa.Column(
            "account_deletion_status",
            sa.String(length=32),
            nullable=True,
            comment="Self-delete progress: null, deletion_pending, or deleted",
        ),
    )
    op.create_check_constraint(
        "ck_doctor_account_deletion_status",
        "doctor",
        "account_deletion_status IS NULL OR "
        "account_deletion_status IN ('deletion_pending', 'deleted')",
    )
    op.add_column(
        "practice",
        sa.Column(
            "owner_reassignment_needed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Set when self-delete leaves the practice without an owner",
        ),
    )


def downgrade() -> None:
    op.drop_column("practice", "owner_reassignment_needed_at")
    op.drop_constraint(
        "ck_doctor_account_deletion_status",
        "doctor",
        type_="check",
    )
    op.drop_column("doctor", "account_deletion_status")
