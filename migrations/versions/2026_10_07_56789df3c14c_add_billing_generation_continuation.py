"""Expand explicit, bounded provider generation continuation; dormant by default."""
from alembic import op
import sqlalchemy as sa
from activemodel.types import TypeIDType
from sqlalchemy.dialects.postgresql import JSONB

revision = "56789df3c14c"
down_revision = "4c5b17148cff"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("billing_header_action", sa.Column("continuation_consent", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("billing_header_action", sa.Column("initial_authorized_until", sa.DateTime(timezone=True)))
    op.create_check_constraint("ck_billing_action_continuation", "billing_header_action",
        "(NOT continuation_consent AND initial_authorized_until IS NULL) OR (continuation_consent AND generation_id IS NOT NULL AND purpose = 'header.generate' AND initial_authorized_until IS NOT NULL AND initial_authorized_until > created_at AND initial_authorized_until <= expires_at)")
    op.create_table("billing_header_generation_continuation",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("action_id", TypeIDType("billing_action"), sa.ForeignKey("billing_header_action.id"), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("actor", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("authorized_until", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("purpose = 'header.generation-continue'", name="ck_billing_continue_purpose"),
        sa.CheckConstraint("request_sha256 ~ '^[a-f0-9]{64}$'", name="ck_billing_continue_digest"),
        sa.CheckConstraint("authorized_until > created_at", name="ck_billing_continue_expiry"),
        sa.UniqueConstraint("action_id", "authorized_until", name="uq_billing_continue_expiry"))
    op.execute("""
        CREATE FUNCTION billing_generation_continue_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE binding billing_header_action%ROWTYPE; previous_expiry timestamptz; evidence_count integer;
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'continuation evidence is append only' USING ERRCODE='23514';
          END IF;
          SELECT * INTO binding FROM billing_header_action WHERE id=NEW.action_id FOR UPDATE;
          SELECT count(*), max(authorized_until) INTO evidence_count, previous_expiry
            FROM billing_header_generation_continuation WHERE action_id=NEW.action_id;
          previous_expiry := COALESCE(previous_expiry, binding.initial_authorized_until);
          IF binding.id IS NULL OR NOT binding.continuation_consent OR binding.finished_at IS NOT NULL
             OR clock_timestamp() >= LEAST(binding.expires_at, previous_expiry)
             OR NEW.created_at < binding.created_at OR NEW.created_at > clock_timestamp()
             OR NEW.authorized_until <= previous_expiry OR NEW.authorized_until > binding.expires_at
             OR NEW.request_sha256 <> binding.request_sha256 OR NEW.actor <> binding.actor
             OR evidence_count >= 48 THEN
            RAISE EXCEPTION 'continuation binding invalid or lapsed' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END; $$;
        CREATE TRIGGER billing_generation_continue_guard BEFORE INSERT OR UPDATE OR DELETE
          ON billing_header_generation_continuation FOR EACH ROW EXECUTE FUNCTION billing_generation_continue_guard();
        CREATE TRIGGER billing_generation_continue_truncate_guard BEFORE TRUNCATE
          ON billing_header_generation_continuation FOR EACH STATEMENT EXECUTE FUNCTION billing_generation_continue_guard();
    """)


def downgrade():
    raise RuntimeError("Disable continuation and retain append-only evidence; no destructive rollback.")
