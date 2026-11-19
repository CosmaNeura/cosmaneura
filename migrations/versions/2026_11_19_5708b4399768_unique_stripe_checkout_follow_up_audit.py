"""unique_stripe_checkout_follow_up_audit

Partial unique indexes so concurrent late-Checkout deliveries cannot insert
duplicate stripe_checkout_follow_up_required or stripe_refund_needed audit
rows for the same (practice_id, document_reference). Also adds
practice.late_checkout_refused_at as the durable refuse marker that preserves
local trial for remaining clinicians.

Revision ID: 5708b4399768
Revises: c764298c7590
Create Date: 2026-11-19 18:35:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "5708b4399768"
down_revision: Union[str, None] = "c764298c7590"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "practice",
        sa.Column(
            "late_checkout_refused_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "Set when late Checkout was refused after owner self-delete; "
                "preserves unbound local trial without relying on "
                "owner_reassignment_needed_at"
            ),
        ),
    )
    op.create_index(
        "uq_practice_access_audit_stripe_checkout_follow_up",
        "practice_access_audit",
        ["practice_id", "document_reference"],
        unique=True,
        postgresql_where=sa.text(
            "action = 'stripe_checkout_follow_up_required' "
            "AND document_reference IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_practice_access_audit_stripe_refund_needed",
        "practice_access_audit",
        ["practice_id", "document_reference"],
        unique=True,
        postgresql_where=sa.text(
            "action = 'stripe_refund_needed' AND document_reference IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_practice_access_audit_stripe_refund_needed",
        table_name="practice_access_audit",
        postgresql_where=sa.text(
            "action = 'stripe_refund_needed' AND document_reference IS NOT NULL"
        ),
    )
    op.drop_index(
        "uq_practice_access_audit_stripe_checkout_follow_up",
        table_name="practice_access_audit",
        postgresql_where=sa.text(
            "action = 'stripe_checkout_follow_up_required' "
            "AND document_reference IS NOT NULL"
        ),
    )
    op.drop_column("practice", "late_checkout_refused_at")
