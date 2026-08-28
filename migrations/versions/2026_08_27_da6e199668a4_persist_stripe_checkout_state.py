"""Persist Stripe Checkout request and trial-policy state.

Revision ID: da6e199668a4
Revises: d0a4c8e2f611
Create Date: 2026-08-27 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "da6e199668a4"
down_revision: str | None = "d0a4c8e2f611"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "practice",
        sa.Column(
            "stripe_checkout_request_json",
            sa.Text(),
            nullable=True,
            comment=(
                "Exact Stripe Checkout request body retained until its session is durable."
            ),
        ),
    )
    op.add_column(
        "practice",
        sa.Column(
            "stripe_checkout_request_created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "Start of the bounded Stripe Checkout idempotency replay window."
            ),
        ),
    )
    op.add_column(
        "practice",
        sa.Column(
            "stripe_trial_clock_policy",
            sa.String(length=64),
            nullable=True,
            comment=(
                "Durable policy provenance for an authoritative Stripe trial clock."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("practice", "stripe_trial_clock_policy")
    op.drop_column("practice", "stripe_checkout_request_created_at")
    op.drop_column("practice", "stripe_checkout_request_json")
