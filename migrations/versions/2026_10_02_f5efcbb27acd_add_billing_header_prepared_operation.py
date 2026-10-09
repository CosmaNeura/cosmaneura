"""Expand: immutable provider-confirmed billing source; no delivery or admission activation."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from activemodel.types import TypeIDType

revision = "f5efcbb27acd"
down_revision = "16a6bf2d1539"
branch_labels = None
depends_on = None


def upgrade():
    refs = {"doctor_id": ("doctor", "doctor"), "practice_id": ("practice", "practice"),
            "appointment_id": ("appointment", "appointment"), "patient_id": ("patient", "patient"),
            "note_id": ("ai_visit_note", "visit_note")}
    op.create_table(
        "billing_header_prepared_operation",
        sa.Column("id", TypeIDType("billing_operation"), primary_key=True),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        *(sa.Column(name, TypeIDType(prefix), sa.ForeignKey(table + ".id"), nullable=False)
          for name, (table, prefix) in refs.items()),
        sa.Column("schema_ref", sa.String(128), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        *(sa.Column(name, JSONB(), nullable=False)
          for name in ("actor", "source", "ordered_content", "visit", "admission")),
        sa.Column("projected_text", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("operation_id", name="uq_billing_operation_id"),
        sa.UniqueConstraint("appointment_id", "schema_ref", "source_revision", name="uq_billing_source_revision"),
        sa.CheckConstraint("source_revision > 0", name="ck_billing_source_revision"),
        sa.CheckConstraint("request_sha256 ~ '^[a-f0-9]{64}$'", name="ck_billing_request_digest"),
    )
    op.execute("""
        CREATE FUNCTION billing_header_operation_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'billing operation evidence is immutable' USING ERRCODE = '23514'; END;
        $$;
        CREATE TRIGGER billing_header_operation_immutable
        BEFORE UPDATE OR DELETE OR TRUNCATE ON billing_header_prepared_operation
        FOR EACH STATEMENT EXECUTE FUNCTION billing_header_operation_immutable();
    """)


def downgrade():
    raise RuntimeError("Disable billing source policy and retain immutable prepared operations; no destructive rollback.")
