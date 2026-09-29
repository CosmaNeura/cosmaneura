"""Record the highest seat count paid in the current Stripe billing period.

Revision ID: ed76434b6308
Revises: 71d6e1ac9b04
Create Date: 2026-09-28 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "ed76434b6308"
down_revision: str | None = "71d6e1ac9b04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _refuse_populated_downgrade() -> None:
    """Never discard what a practice has already paid for this period."""

    if op.get_context().as_sql:
        raise RuntimeError(
            "Stripe paid seat high-water downgrade is unsupported in offline mode"
        )
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE practice IN ACCESS EXCLUSIVE MODE"))
    populated = connection.execute(
        sa.text(
            "SELECT count(*) FROM practice WHERE "
            "stripe_paid_seat_high_water IS NOT NULL OR "
            "stripe_paid_seat_period_start IS NOT NULL"
        )
    ).scalar_one()
    if populated:
        raise RuntimeError(
            "refusing to discard Stripe paid seat high-water: "
            f"practices_with_paid_seat_high_water={populated}"
        )


def upgrade() -> None:
    op.add_column(
        "practice",
        sa.Column(
            "stripe_paid_seat_high_water",
            sa.Integer(),
            nullable=True,
            comment="Highest seat count Stripe held in the recorded billing period.",
        ),
    )
    op.add_column(
        "practice",
        sa.Column(
            "stripe_paid_seat_period_start",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Stripe item current_period_start the high-water belongs to.",
        ),
    )
    op.create_check_constraint(
        "ck_practice_stripe_paid_seat_high_water_bounds",
        "practice",
        "stripe_paid_seat_high_water IS NULL "
        "OR stripe_paid_seat_high_water BETWEEN 1 AND 20",
    )
    op.create_check_constraint(
        "ck_practice_stripe_paid_seat_period_pairing",
        "practice",
        "(stripe_paid_seat_high_water IS NULL) "
        "= (stripe_paid_seat_period_start IS NULL)",
    )


def downgrade() -> None:
    _refuse_populated_downgrade()
    op.drop_constraint(
        "ck_practice_stripe_paid_seat_period_pairing", "practice", type_="check"
    )
    op.drop_constraint(
        "ck_practice_stripe_paid_seat_high_water_bounds", "practice", type_="check"
    )
    op.drop_column("practice", "stripe_paid_seat_period_start")
    op.drop_column("practice", "stripe_paid_seat_high_water")
