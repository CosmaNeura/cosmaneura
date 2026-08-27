"""Add reversible clinician seat state.

Revision ID: d0a4c8e2f611
Revises: 89c31f0ae275
Create Date: 2026-08-27 14:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "d0a4c8e2f611"
down_revision: str | None = "89c31f0ae275"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "practice_member",
        sa.Column(
            "seat_state",
            sa.String(),
            server_default="active",
            nullable=False,
            comment=(
                "Active, non-revoked memberships occupy one clinician seat; "
                "inactive memberships do not."
            ),
        ),
    )
    op.create_check_constraint(
        "ck_practice_member_seat_state",
        "practice_member",
        "seat_state IN ('active', 'inactive')",
    )
    op.create_index(
        "practice_member_practice_seat_state_idx",
        "practice_member",
        ["practice_id", "seat_state"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "practice_member_practice_seat_state_idx",
        table_name="practice_member",
    )
    op.drop_constraint(
        "ck_practice_member_seat_state",
        "practice_member",
        type_="check",
    )
    op.drop_column("practice_member", "seat_state")
