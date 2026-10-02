"""add_visit_prep_sms_stop

Revision ID: 79601b0047dc
Revises: 5789bd0d277e
Create Date: 2026-10-02 21:20:16.774169

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
import activemodel

# revision identifiers, used by Alembic.
revision: str = '79601b0047dc'
down_revision: Union[str, None] = '5789bd0d277e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Generated against owned synthetic PostgreSQL; unrelated comment drift removed.
    op.create_table('visit_prep_sms_stop_event',
    sa.Column('id', activemodel.types.typeid.TypeIDType(prefix='visit_prep_sms_stop'), nullable=False, comment='TypeID with prefix: visit_prep_sms_stop'),
    sa.Column('sender_digest', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('recipient_digest', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('source_event_digest', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('source_evidence_hmac', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('payload_hmac', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('evidence_ciphertext', sa.Text(), nullable=False),
    sa.Column('envelope_format_version', sa.Integer(), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("sender_digest ~ '^[0-9a-f]{64}$' AND recipient_digest ~ '^[0-9a-f]{64}$' AND source_event_digest ~ '^[0-9a-f]{64}$' AND source_evidence_hmac ~ '^[0-9a-f]{64}$' AND payload_hmac ~ '^[0-9a-f]{64}$'", name=op.f('visit_prep_sms_stop_event_digest_shape_check')),
    sa.CheckConstraint('envelope_format_version = 1 AND octet_length(evidence_ciphertext) BETWEEN 1 AND 4096', name=op.f('visit_prep_sms_stop_event_evidence_shape_check')),
    sa.CheckConstraint('occurred_at <= recorded_at', name=op.f('visit_prep_sms_stop_event_time_shape_check')),
    sa.PrimaryKeyConstraint('id', name=op.f('visit_prep_sms_stop_event_pkey')),
    sa.UniqueConstraint('sender_digest', 'source_event_digest', name='uq_visit_prep_sms_stop_source')
    )
    op.create_index('ix_visit_prep_sms_stop_recipient', 'visit_prep_sms_stop_event', ['sender_digest', 'recipient_digest'], unique=False)
    op.execute("""
        CREATE FUNCTION public.cosmas_guard_visit_prep_sms_stop()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        DECLARE checked_at timestamptz;
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'SMS STOP evidence is append-only' USING ERRCODE = '55000';
          END IF;
          IF current_setting('transaction_isolation') <> 'read committed'
             OR NOT pg_try_advisory_xact_lock(hashtextextended(
               'visit-prep-sms-stop-v1:' || NEW.sender_digest || ':' || NEW.recipient_digest, 0)) THEN
            RAISE EXCEPTION 'SMS STOP fence is unavailable' USING ERRCODE = '55000';
          END IF;
          checked_at := clock_timestamp();
          IF NEW.occurred_at > checked_at THEN
            RAISE EXCEPTION 'SMS STOP occurrence is not current' USING ERRCODE = '55000';
          END IF;
          NEW.recorded_at := checked_at;
          RETURN NEW;
        END $$;
        CREATE TRIGGER visit_prep_sms_stop_guard
        BEFORE INSERT OR UPDATE OR DELETE ON public.visit_prep_sms_stop_event
        FOR EACH ROW EXECUTE FUNCTION public.cosmas_guard_visit_prep_sms_stop();

        CREATE FUNCTION public.cosmas_deny_visit_prep_sms_stop_truncate()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'SMS STOP evidence cannot be truncated' USING ERRCODE = '55000';
        END $$;
        CREATE TRIGGER visit_prep_sms_stop_no_truncate BEFORE TRUNCATE ON public.visit_prep_sms_stop_event
        FOR EACH STATEMENT EXECUTE FUNCTION public.cosmas_deny_visit_prep_sms_stop_truncate();
    """)


def downgrade() -> None:
    raise RuntimeError('Retain SMS STOP evidence; use a forward fix')
