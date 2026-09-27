"""Add the scheduled end of an owner-revoked member's access.

Revision ID: 9a23eb377df7
Revises: 71d6e1ac9b04
Create Date: 2026-09-27 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "9a23eb377df7"
down_revision: str | None = "71d6e1ac9b04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _refuse_populated_downgrade() -> None:
    """Never turn a scheduled access end into an immediate or missing one."""

    if op.get_context().as_sql:
        raise RuntimeError("practice member access-end downgrade is unsupported offline")
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE practice_member IN ACCESS EXCLUSIVE MODE"))
    populated = connection.execute(
        sa.text(
            "SELECT count(*) FROM practice_member WHERE access_ends_at IS NOT NULL"
        )
    ).scalar_one()
    if populated:
        raise RuntimeError(
            "refusing to discard scheduled member access ends: "
            f"memberships_with_access_end={populated}"
        )


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        "practice_member",
        sa.Column(
            "access_ends_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "When an owner-revoked member's access stops; the seat is held "
                "until then."
            ),
        ),
    )
    # Every existing row has a null access_ends_at, so the constraint holds.
    op.create_check_constraint(
        "ck_practice_member_access_end_requires_inactive_seat",
        "practice_member",
        "access_ends_at IS NULL OR seat_state = 'inactive'",
    )


def downgrade() -> None:
    _refuse_populated_downgrade()
    op.drop_constraint(
        "ck_practice_member_access_end_requires_inactive_seat",
        "practice_member",
        type_="check",
    )
    op.drop_column("practice_member", "access_ends_at")
