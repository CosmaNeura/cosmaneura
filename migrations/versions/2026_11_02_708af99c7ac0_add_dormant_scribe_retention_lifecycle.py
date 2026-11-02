"""add_dormant_scribe_retention_lifecycle

Revision ID: 708af99c7ac0
Revises: 49126bf99c72
Create Date: 2026-11-02 16:54:36.558662

"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op


# revision identifiers, used by Alembic.
revision = "708af99c7ac0"
down_revision = "49126bf99c72"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scribe_retention_event",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("operation_id", sa.Uuid, sa.ForeignKey("scribe_cancellation_operation.id"), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("event_key_sha256", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.UniqueConstraint("operation_id", "sequence", name="uq_scribe_retention_sequence"),
        sa.UniqueConstraint("operation_id", "event_key_sha256", name="uq_scribe_retention_retry"),
        sa.CheckConstraint("sequence > 0", name="ck_scribe_retention_sequence"),
        sa.CheckConstraint("kind IN ('expiry-hold', 'erasure-intent', 'verified-receipt', 'certificate-ready', 'certificate-issued')", name="ck_scribe_retention_kind"),
        sa.CheckConstraint("event_key_sha256 ~ '^[a-f0-9]{64}$' AND jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 65536", name="ck_scribe_retention_payload"),
        comment="Append-only non-PHI erasure lifecycle evidence; no PHI, blob paths, tokens or login mutation.",
    )
    op.execute("""
        CREATE FUNCTION scribe_retention_reject_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'scribe retention evidence is immutable';
        END $$;
        CREATE TRIGGER scribe_retention_immutable
        BEFORE UPDATE OR DELETE ON scribe_retention_event
        FOR EACH ROW EXECUTE FUNCTION scribe_retention_reject_mutation();
    """)


def downgrade() -> None:
    raise RuntimeError("Expand recovery leaves retention lifecycle dormant; evidence deletion requires a separately authorized retention operation")
