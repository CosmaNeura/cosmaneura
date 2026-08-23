"""harden Stripe billing state and privileged-access evidence

Revision ID: f777b62b8152
Revises: c380a7cd8feb
Create Date: 2026-08-20 22:37:51.000000
"""

from typing import Sequence, Union

import activemodel
import sqlalchemy as sa
from alembic import op


revision: str = "f777b62b8152"
down_revision: Union[str, None] = "c380a7cd8feb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "practice_stripe_customer_id_key", "practice", ["stripe_customer_id"]
    )
    op.create_unique_constraint(
        "practice_stripe_subscription_id_key",
        "practice",
        ["stripe_subscription_id"],
    )
    op.create_unique_constraint(
        "baa_acceptance_practice_version_key",
        "baa_acceptance",
        ["practice_id", "baa_version_id"],
    )

    op.add_column(
        "practice",
        sa.Column(
            "stripe_last_event_created_at",
            sa.DateTime(timezone=True),
            comment="Creation time of the newest Stripe event applied to this practice.",
        ),
    )
    op.add_column(
        "practice", sa.Column("stripe_last_event_id", sa.String(length=255))
    )
    op.add_column(
        "practice",
        sa.Column(
            "stripe_state_version",
            sa.Integer(),
            server_default="0",
            nullable=False,
            comment=(
                "Successful authoritative status writes increment this optimistic "
                "CAS fence."
            ),
        ),
    )
    op.add_column(
        "practice",
        sa.Column(
            "stripe_checkout_attempt",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "practice",
        sa.Column(
            "stripe_checkout_state_version",
            sa.Integer(),
            server_default="0",
            nullable=False,
            comment="CAS fence for hosted-session cache writes/clears.",
        ),
    )
    op.add_column(
        "practice", sa.Column("stripe_checkout_session_id", sa.String(length=255))
    )
    op.add_column(
        "practice",
        sa.Column("stripe_checkout_session_url", sa.String(length=2048)),
    )
    op.add_column(
        "practice",
        sa.Column(
            "stripe_checkout_session_expires_at", sa.DateTime(timezone=True)
        ),
    )
    op.add_column(
        "practice",
        sa.Column(
            "stripe_quantity_sync_required",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.add_column(
        "practice", sa.Column("stripe_quantity_sync_target", sa.Integer())
    )
    op.add_column(
        "practice",
        sa.Column(
            "stripe_quantity_sync_version",
            sa.Integer(),
            server_default="0",
            nullable=False,
            comment=(
                "Monotonic fence preventing an older in-flight sync from clearing "
                "newer work."
            ),
        ),
    )
    op.add_column(
        "practice",
        sa.Column(
            "stripe_quantity_sync_attempts",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "practice",
        sa.Column("stripe_quantity_sync_error", sa.String(length=512)),
    )
    op.add_column(
        "practice", sa.Column("stripe_quantity_synced_at", sa.DateTime(timezone=True))
    )

    op.create_table(
        "practice_access_audit",
        sa.Column(
            "id",
            activemodel.types.typeid.TypeIDType(prefix="practice_access_audit"),
            nullable=False,
            comment="TypeID with prefix: practice_access_audit",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "practice_id",
            activemodel.types.typeid.TypeIDType(prefix="practice"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("operator_ref", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("document_reference", sa.String(length=255)),
        sa.ForeignKeyConstraint(
            ["practice_id"],
            ["practice.id"],
            name=op.f("practice_access_audit_practice_id_fkey"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("practice_access_audit_pkey")),
        comment="Append-only operator evidence for an access-affecting practice change.",
    )
    op.create_index(
        op.f("practice_access_audit_action_idx"),
        "practice_access_audit",
        ["action"],
        unique=False,
    )
    op.create_table(
        "stripe_event_receipt",
        sa.Column(
            "id",
            activemodel.types.typeid.TypeIDType(prefix="stripe_event_receipt"),
            nullable=False,
            comment="TypeID with prefix: stripe_event_receipt",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("event_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "practice_id",
            activemodel.types.typeid.TypeIDType(prefix="practice"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["practice_id"],
            ["practice.id"],
            name=op.f("stripe_event_receipt_practice_id_fkey"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("stripe_event_receipt_pkey")),
        sa.UniqueConstraint(
            "event_id", name=op.f("stripe_event_receipt_event_id_key")
        ),
        comment="One non-PHI row per event, committed with its mirrored state.",
    )
    op.create_table(
        "stripe_event_inbox",
        sa.Column(
            "id",
            activemodel.types.typeid.TypeIDType(prefix="stripe_event_inbox"),
            nullable=False,
            comment="TypeID with prefix: stripe_event_inbox",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column(
            "state", sa.String(length=32), server_default="pending", nullable=False
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.String(length=512)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name=op.f("stripe_event_inbox_pkey")),
        sa.UniqueConstraint("event_id", name=op.f("stripe_event_inbox_event_id_key")),
        comment="Signed billing event persisted before the webhook returns success.",
    )
    op.create_index(
        op.f("stripe_event_inbox_state_idx"),
        "stripe_event_inbox",
        ["state"],
        unique=False,
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION cosmas_reject_append_only_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = '55000';
        END;
        $$
        """
    )
    for table_name in ("baa_acceptance", "practice_access_audit"):
        op.execute(
            f"""
            CREATE TRIGGER {table_name}_reject_mutation
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION cosmas_reject_append_only_mutation()
            """
        )


def downgrade() -> None:
    for table_name in ("practice_access_audit", "baa_acceptance"):
        op.execute(
            f"DROP TRIGGER IF EXISTS {table_name}_reject_mutation ON {table_name}"
        )
    op.execute("DROP FUNCTION IF EXISTS cosmas_reject_append_only_mutation()")
    op.drop_index(
        op.f("stripe_event_inbox_state_idx"), table_name="stripe_event_inbox"
    )
    op.drop_table("stripe_event_inbox")
    op.drop_table("stripe_event_receipt")
    op.drop_index(
        op.f("practice_access_audit_action_idx"),
        table_name="practice_access_audit",
    )
    op.drop_table("practice_access_audit")

    op.drop_column("practice", "stripe_quantity_synced_at")
    op.drop_column("practice", "stripe_quantity_sync_error")
    op.drop_column("practice", "stripe_quantity_sync_attempts")
    op.drop_column("practice", "stripe_quantity_sync_version")
    op.drop_column("practice", "stripe_quantity_sync_target")
    op.drop_column("practice", "stripe_quantity_sync_required")
    op.drop_column("practice", "stripe_checkout_session_expires_at")
    op.drop_column("practice", "stripe_checkout_session_url")
    op.drop_column("practice", "stripe_checkout_session_id")
    op.drop_column("practice", "stripe_checkout_state_version")
    op.drop_column("practice", "stripe_checkout_attempt")
    op.drop_column("practice", "stripe_last_event_id")
    op.drop_column("practice", "stripe_state_version")
    op.drop_column("practice", "stripe_last_event_created_at")
    op.drop_constraint(
        "baa_acceptance_practice_version_key", "baa_acceptance", type_="unique"
    )
    op.drop_constraint(
        "practice_stripe_subscription_id_key", "practice", type_="unique"
    )
    op.drop_constraint("practice_stripe_customer_id_key", "practice", type_="unique")
