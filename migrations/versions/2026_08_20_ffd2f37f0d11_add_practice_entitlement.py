"""add practice entitlement

Trial, exemption, and mirrored-Stripe fields on practice. The platform is
the system of record for entitlement (ARCHITECTURE.md §8, ADR 0016); these
columns are the different ways it gets set.

Revision ID: ffd2f37f0d11
Revises: c7e91a4b2d08
Create Date: 2026-08-20 11:20:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "ffd2f37f0d11"
down_revision: Union[str, None] = "c7e91a4b2d08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "practice",
        sa.Column(
            "trial_ends_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Stamped as now + TRIAL_LENGTH the first time the BAA is accepted.",
        ),
    )
    op.add_column(
        "practice",
        sa.Column(
            "billing_exempt",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
            comment="Comped access (early-adopter pilots, future centrally invoiced groups).",
        ),
    )
    op.add_column(
        "practice",
        sa.Column(
            "billing_exempt_until",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="End of the comp. Null while billing_exempt means exempt indefinitely.",
        ),
    )
    op.add_column(
        "practice",
        sa.Column("stripe_customer_id", sa.String(), nullable=True),
    )
    op.add_column(
        "practice",
        sa.Column("stripe_subscription_id", sa.String(), nullable=True),
    )
    op.add_column(
        "practice",
        sa.Column(
            "subscription_status",
            sa.String(),
            nullable=True,
            comment="Mirrored from Stripe webhooks (trialing/active/past_due/canceled/paused).",
        ),
    )


def downgrade() -> None:
    op.drop_column("practice", "subscription_status")
    op.drop_column("practice", "stripe_subscription_id")
    op.drop_column("practice", "stripe_customer_id")
    op.drop_column("practice", "billing_exempt_until")
    op.drop_column("practice", "billing_exempt")
    op.drop_column("practice", "trial_ends_at")
