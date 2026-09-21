"""Persist Stripe Checkout request and trial-policy state.

Revision ID: da6e199668a4
Revises: e7a9c3d5f102
Create Date: 2026-08-27 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "da6e199668a4"
down_revision: str | None = "e7a9c3d5f102"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _refuse_populated_downgrade() -> None:
    """Never discard Checkout idempotency or trial-clock evidence."""

    if op.get_context().as_sql:
        raise RuntimeError(
            "Stripe Checkout state downgrade is unsupported in offline mode"
        )
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE practice IN ACCESS EXCLUSIVE MODE"))
    populated = connection.execute(
        sa.text(
            "SELECT count(*) FROM practice WHERE "
            "stripe_checkout_request_json IS NOT NULL OR "
            "stripe_checkout_request_created_at IS NOT NULL OR "
            "stripe_checkout_entitlement_contract IS NOT NULL OR "
            "stripe_subscription_entitlement_contract IS NOT NULL OR "
            "stripe_trial_clock_policy IS NOT NULL"
        )
    ).scalar_one()
    if populated:
        raise RuntimeError(
            "refusing to discard Stripe Checkout state: "
            f"practices_with_checkout_state={populated}"
        )


def upgrade() -> None:
    op.alter_column(
        "practice",
        "trial_ends_at",
        existing_type=sa.DateTime(timezone=True),
        comment=(
            "Fixed public Checkout trial end, authoritative Stripe end, "
            "or legacy BAA trial."
        ),
    )
    op.add_column(
        "practice",
        sa.Column(
            "stripe_checkout_request_json",
            sa.Text(),
            nullable=True,
            comment="Durable exact Stripe request body for crash-safe idempotent retries.",
        ),
    )
    op.add_column(
        "practice",
        sa.Column(
            "stripe_checkout_request_created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Start of the bounded Stripe idempotency replay window.",
        ),
    )
    op.add_column(
        "practice",
        sa.Column(
            "stripe_checkout_entitlement_contract",
            sa.String(length=64),
            nullable=True,
            comment="Exact entitlement contract stamped on the pending Checkout attempt.",
        ),
    )
    op.add_column(
        "practice",
        sa.Column(
            "stripe_trial_clock_policy",
            sa.String(length=64),
            nullable=True,
            comment="Durable provenance preventing a bound paid-pilot clock downgrade.",
        ),
    )
    op.add_column(
        "practice",
        sa.Column(
            "stripe_subscription_entitlement_contract",
            sa.String(length=64),
            nullable=True,
            comment="Exact entitlement contract bound to the authoritative subscription.",
        ),
    )

    # Any binding or cached Session that predates this migration was created by
    # the legacy, pre-contract Checkout implementation. Mark it explicitly so
    # runtime compatibility never has to infer provenance from mutable Stripe
    # metadata or from a missing value.
    op.execute(
        sa.text(
            "UPDATE practice SET stripe_checkout_entitlement_contract = "
            "'stripe-legacy-unversioned.v0' "
            "WHERE stripe_checkout_session_id IS NOT NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE practice SET stripe_subscription_entitlement_contract = "
            "'stripe-legacy-unversioned.v0' "
            "WHERE stripe_subscription_id IS NOT NULL"
        )
    )


def downgrade() -> None:
    _refuse_populated_downgrade()
    op.drop_column("practice", "stripe_subscription_entitlement_contract")
    op.drop_column("practice", "stripe_trial_clock_policy")
    op.drop_column("practice", "stripe_checkout_entitlement_contract")
    op.drop_column("practice", "stripe_checkout_request_created_at")
    op.drop_column("practice", "stripe_checkout_request_json")
    op.alter_column(
        "practice",
        "trial_ends_at",
        existing_type=sa.DateTime(timezone=True),
        comment="Stamped as now + TRIAL_LENGTH the first time the BAA is accepted.",
    )
