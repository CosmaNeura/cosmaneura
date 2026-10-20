"""Expand durable async provider generation intent; no external activation."""
from alembic import op
import sqlalchemy as sa

revision = "4c5b17148cff"
down_revision = "211aefdf709b"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("billing_header_action", sa.Column("generation_id", sa.Uuid()))
    op.add_column("billing_header_action", sa.Column("completion_status", sa.String(16)))
    op.drop_constraint("ck_billing_action_purpose", "billing_header_action", type_="check")
    op.create_check_constraint("ck_billing_action_purpose", "billing_header_action",
        "purpose IN ('completed-note.accept','header.read','header.generate','header.save','header.generation-status')")
    op.drop_constraint("ck_billing_action_lifetime", "billing_header_action", type_="check")
    op.create_check_constraint("ck_billing_action_lifetime", "billing_header_action",
        "expires_at > created_at AND expires_at <= created_at + CASE WHEN generation_id IS NULL THEN interval '120 seconds' ELSE interval '360 seconds' END")
    op.create_check_constraint("ck_billing_action_generation", "billing_header_action",
        "generation_id IS NULL OR purpose = 'header.generate'")
    op.create_check_constraint("ck_billing_action_completion", "billing_header_action",
        "completion_status IS NULL OR (generation_id IS NOT NULL AND finished_at IS NOT NULL AND completion_status IN ('succeeded','failed','unknown'))")
    op.create_index("uq_billing_action_generation", "billing_header_action",
        ["operation_id", "generation_id"], unique=True,
        postgresql_where=sa.text("generation_id IS NOT NULL"))
    op.execute("""
        CREATE OR REPLACE FUNCTION billing_header_action_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'UPDATE' AND OLD.finished_at IS NULL AND NEW.finished_at IS NOT NULL
             AND NEW.finished_at >= OLD.created_at AND NEW.finished_at <= clock_timestamp()
             AND (to_jsonb(NEW)-'finished_at'-'completion_status') = (to_jsonb(OLD)-'finished_at'-'completion_status')
             AND OLD.completion_status IS NULL
             AND (NEW.completion_status IS NULL OR (OLD.generation_id IS NOT NULL
                  AND NEW.completion_status IN ('succeeded','failed','unknown'))) THEN RETURN NEW; END IF;
          RAISE EXCEPTION 'billing action binding is immutable' USING ERRCODE='23514';
        END; $$;
    """)


def downgrade():
    raise RuntimeError("Disable async CAB generation and retain action evidence; no destructive rollback.")
