"""logical_scribe_delivery_producer

Revision ID: 7c1d5e9a2b40
Revises: 4f7a9c2d1e80
Create Date: 2026-08-16 01:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op


revision: str = "7c1d5e9a2b40"
down_revision: Union[str, None] = "4f7a9c2d1e80"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LOGICAL_SCRIBE_PRODUCER = "cosmaneura-scribe-v1"


def _assert_no_collapsed_identity_collisions() -> None:
    """Abort before changing constraints if legacy caller namespaces collide."""

    # Offline mode renders SQL for Squawk and has no result-bearing connection.
    # Every real upgrade takes the online path and runs this fail-closed check.
    if context.is_offline_mode():
        return

    connection = op.get_bind()
    for column in ("idempotency_key", "scribe_session_id"):
        collision = connection.execute(
            sa.text(
                f"""
                SELECT 1
                FROM scribe_transcript_delivery
                GROUP BY {column}
                HAVING count(*) > 1
                LIMIT 1
                """
            )
        ).first()
        if collision is not None:
            raise RuntimeError(
                "Cannot collapse Scribe delivery caller namespaces: "
                f"duplicate {column} values require operator reconciliation."
            )


def upgrade() -> None:
    _assert_no_collapsed_identity_collisions()

    # The server default keeps the migration compatible with an older app revision
    # during a rolling deployment. The producer is never caller-selectable.
    op.add_column(
        "scribe_transcript_delivery",
        sa.Column(
            "logical_producer",
            sa.String(length=64),
            server_default=sa.text(f"'{LOGICAL_SCRIBE_PRODUCER}'"),
            nullable=True,
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE scribe_transcript_delivery
            SET logical_producer = :logical_producer
            WHERE logical_producer IS NULL
            """
        ).bindparams(logical_producer=LOGICAL_SCRIBE_PRODUCER)
    )
    op.alter_column(
        "scribe_transcript_delivery",
        "logical_producer",
        existing_type=sa.String(length=64),
        nullable=False,
    )

    op.drop_constraint(
        "uq_scribe_delivery_caller_idempotency_key",
        "scribe_transcript_delivery",
        type_="unique",
    )
    op.drop_constraint(
        "uq_scribe_delivery_caller_session",
        "scribe_transcript_delivery",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_scribe_delivery_producer_idempotency_key",
        "scribe_transcript_delivery",
        ["logical_producer", "idempotency_key"],
    )
    op.create_unique_constraint(
        "uq_scribe_delivery_producer_session",
        "scribe_transcript_delivery",
        ["logical_producer", "scribe_session_id"],
    )


def downgrade() -> None:
    # New logical-producer uniqueness is stricter than the legacy per-caller
    # constraints, so every upgraded data set can safely restore the old shape.
    op.drop_constraint(
        "uq_scribe_delivery_producer_session",
        "scribe_transcript_delivery",
        type_="unique",
    )
    op.drop_constraint(
        "uq_scribe_delivery_producer_idempotency_key",
        "scribe_transcript_delivery",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_scribe_delivery_caller_idempotency_key",
        "scribe_transcript_delivery",
        ["caller_client_id", "idempotency_key"],
    )
    op.create_unique_constraint(
        "uq_scribe_delivery_caller_session",
        "scribe_transcript_delivery",
        ["caller_client_id", "scribe_session_id"],
    )
    op.drop_column("scribe_transcript_delivery", "logical_producer")
