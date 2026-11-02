"""Add the Scribe cancellation processing-fence ledger.

Revision ID: 49126bf99c72
Revises: 6bd4416fe39c
Source-only expand; no request activation, backfill, account or PHI deletion.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "49126bf99c72"
down_revision = "6bd4416fe39c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "scribe_cancellation_operation",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("caller_sha256", sa.String(64), nullable=False),
        sa.Column("doctor_ref", sa.String(100), nullable=False),
        sa.Column("request_key_sha256", sa.String(64), nullable=False),
        sa.Column("preview_sha256", sa.String(64), nullable=False),
        sa.Column("inventory_sha256", sa.String(64), nullable=False),
        sa.Column("inventory", postgresql.JSONB, nullable=False),
        sa.Column("policy", postgresql.JSONB, nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("export_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.UniqueConstraint("caller_sha256", name="uq_scribe_cancellation_caller"),
        sa.UniqueConstraint("doctor_ref", name="uq_scribe_cancellation_doctor"),
        sa.CheckConstraint("phase = 'read-only'", name="ck_scribe_cancellation_read_only"),
        sa.CheckConstraint(
            "caller_sha256 ~ '^[a-f0-9]{64}$' AND request_key_sha256 ~ '^[a-f0-9]{64}$' AND "
            "preview_sha256 ~ '^[a-f0-9]{64}$' AND inventory_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_scribe_cancellation_digests",
        ),
        sa.CheckConstraint(
            "export_until >= accepted_at + interval '30 days' AND "
            "export_until <= accepted_at + interval '90 days'",
            name="ck_scribe_cancellation_window",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(inventory) = 'array' AND jsonb_typeof(policy) = 'object' AND "
            "policy ?& ARRAY['grace_days', 'phi_scope', 'approval_sha256'] AND "
            "policy - ARRAY['grace_days', 'phi_scope', 'approval_sha256'] = '{}'::jsonb AND "
            "jsonb_typeof(policy->'grace_days') = 'number' AND "
            "(policy->>'grace_days')::int BETWEEN 30 AND 90 AND "
            "policy->>'phi_scope' IN ('scribe-processing-copies', 'core-and-scribe-clinical-records') AND "
            "policy->>'approval_sha256' ~ '^[a-f0-9]{64}$' AND "
            "export_until = accepted_at + (policy->>'grace_days')::int * interval '1 day'",
            name="ck_scribe_cancellation_policy",
        ),
        comment="Immutable self-only Scribe cancellation receipt; not an erasure manifest.",
    )
    op.execute("""
        CREATE FUNCTION scribe_cancellation_reject_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'scribe cancellation receipt is immutable';
        END $$;
        CREATE TRIGGER scribe_cancellation_immutable
        BEFORE UPDATE OR DELETE ON scribe_cancellation_operation
        FOR EACH ROW EXECUTE FUNCTION scribe_cancellation_reject_mutation();
    """)


def downgrade():
    raise RuntimeError("Expand recovery leaves cancellation ledger dormant; contract cleanup requires approval")
