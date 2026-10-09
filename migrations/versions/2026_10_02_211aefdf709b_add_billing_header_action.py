"""Expand purpose-bound provider action grants; no external authority activation."""

from alembic import op
import sqlalchemy as sa
from activemodel.types import TypeIDType
from sqlalchemy.dialects.postgresql import JSONB

revision = "211aefdf709b"
down_revision = "f5efcbb27acd"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "billing_header_action",
        sa.Column("id", TypeIDType("billing_action"), primary_key=True),
        sa.Column(
            "operation_id",
            sa.Uuid(),
            sa.ForeignKey("billing_header_prepared_operation.operation_id"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("operation_sha256", sa.String(64), nullable=False),
        sa.Column("actor", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "request_sha256 ~ '^[a-f0-9]{64}$' AND operation_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_billing_action_hashes",
        ),
        sa.CheckConstraint(
            "purpose IN ('completed-note.accept','header.read','header.generate','header.save')",
            name="ck_billing_action_purpose",
        ),
        sa.CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + interval '120 seconds'",
            name="ck_billing_action_lifetime",
        ),
    )
    op.create_index(
        "ix_billing_action_request",
        "billing_header_action",
        ["operation_id", "request_sha256", "purpose"],
    )
    op.execute("""
        CREATE FUNCTION billing_header_action_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'UPDATE' AND OLD.finished_at IS NULL AND NEW.finished_at IS NOT NULL
             AND NEW.finished_at >= OLD.created_at AND NEW.finished_at <= clock_timestamp()
             AND (to_jsonb(NEW)-'finished_at') = (to_jsonb(OLD)-'finished_at') THEN RETURN NEW; END IF;
          RAISE EXCEPTION 'billing action binding is immutable' USING ERRCODE='23514';
        END; $$;
        CREATE TRIGGER billing_header_action_guard BEFORE UPDATE OR DELETE ON billing_header_action
          FOR EACH ROW EXECUTE FUNCTION billing_header_action_guard();
        CREATE TRIGGER billing_header_action_truncate_guard BEFORE TRUNCATE ON billing_header_action
          FOR EACH STATEMENT EXECUTE FUNCTION billing_header_action_guard();
    """)


def downgrade():
    raise RuntimeError(
        "Disable CAB bridge and retain provider action evidence; no destructive rollback."
    )
