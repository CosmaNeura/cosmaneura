"""Record the per-seat amount of the price a live subscription is bound to.

Revision ID: 5f3c9a1e7b24
Revises: c764298c7590
Create Date: 2026-11-19 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "5f3c9a1e7b24"
down_revision: str | None = "c764298c7590"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Display only. Existing rows stay NULL (amount hidden) until the next
    # verified Stripe sync writes the bound amount; nothing is backfilled.
    op.add_column(
        "practice",
        sa.Column(
            "stripe_bound_unit_amount",
            sa.Integer(),
            nullable=True,
            comment=(
                "Per-seat minor-unit amount Core last verified for the bound "
                "Stripe price."
            ),
        ),
    )


def downgrade() -> None:
    # Re-derived by the next verified sync; dropping it changes no charge.
    op.drop_column("practice", "stripe_bound_unit_amount")
