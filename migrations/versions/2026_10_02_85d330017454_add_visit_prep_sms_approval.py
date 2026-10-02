"""add_visit_prep_sms_approval

Revision ID: 85d330017454
Revises: 950a65bbfa96
Create Date: 2026-10-02 22:31:29.537518

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
import activemodel

# revision identifiers, used by Alembic.
revision: str = '85d330017454'
down_revision: Union[str, None] = '950a65bbfa96'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Generated on owned synthetic PostgreSQL; unrelated comment drift removed.
    op.create_table('visit_prep_sms_approval_event',
    sa.Column('id', activemodel.types.typeid.TypeIDType(prefix='visit_prep_sms_approval'), nullable=False, comment='TypeID with prefix: visit_prep_sms_approval'),
    sa.Column('owner_practice_member_id', activemodel.types.typeid.TypeIDType(prefix='practice_member'), nullable=False),
    sa.Column('practice_id', activemodel.types.typeid.TypeIDType(prefix='practice'), nullable=False),
    sa.Column('owner_doctor_id', activemodel.types.typeid.TypeIDType(prefix='doctor'), nullable=False),
    sa.Column('event_sequence', sa.Integer(), nullable=False),
    sa.Column('event_kind', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
    sa.Column('template_version', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
    sa.Column('sender_digest', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('subject_digest', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('policy_fingerprint', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('operation_id', sa.Uuid(), nullable=False),
    sa.Column('payload_hmac', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('evidence_ciphertext', sa.Text(), nullable=False),
    sa.Column('envelope_format_version', sa.Integer(), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("((event_kind='approved' AND expires_at IS NOT NULL AND expires_at > recorded_at) OR (event_kind='withdrawn' AND expires_at IS NULL)) IS TRUE", name=op.f('visit_prep_sms_approval_event_time_shape_check')),
    sa.CheckConstraint("event_kind IN ('approved','withdrawn') AND template_version='visit_prep_sms_v1'", name=op.f('visit_prep_sms_approval_event_policy_shape_check')),
    sa.CheckConstraint("sender_digest ~ '^[0-9a-f]{64}$' AND subject_digest ~ '^[0-9a-f]{64}$' AND policy_fingerprint ~ '^[0-9a-f]{64}$' AND payload_hmac ~ '^[0-9a-f]{64}$'", name=op.f('visit_prep_sms_approval_event_digest_shape_check')),
    sa.CheckConstraint("substring(operation_id::text FROM 15 FOR 1) = '4' AND substring(operation_id::text FROM 20 FOR 1) IN ('8','9','a','b')", name=op.f('visit_prep_sms_approval_event_operation_shape_check')),
    sa.CheckConstraint('envelope_format_version=1 AND octet_length(evidence_ciphertext) BETWEEN 1 AND 4096', name=op.f('visit_prep_sms_approval_event_envelope_shape_check')),
    sa.CheckConstraint('event_sequence BETWEEN 1 AND 2147483647', name=op.f('visit_prep_sms_approval_event_sequence_shape_check')),
    sa.ForeignKeyConstraint(['owner_practice_member_id', 'practice_id', 'owner_doctor_id'], ['practice_member.id', 'practice_member.practice_id', 'practice_member.doctor_id'], name='fk_visit_prep_sms_approval_owner', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('visit_prep_sms_approval_event_pkey')),
    sa.UniqueConstraint('owner_practice_member_id', 'event_sequence', name='uq_visit_prep_sms_approval_sequence'),
    sa.UniqueConstraint('owner_practice_member_id', 'operation_id', name='uq_visit_prep_sms_approval_operation')
    )
    op.execute("""
        CREATE FUNCTION public.cosmas_guard_visit_prep_sms_approval()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        DECLARE checked_at timestamptz; next_sequence integer;
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'SMS approval evidence is append-only' USING ERRCODE = '55000';
          END IF;
          PERFORM 1 FROM public.practice WHERE id=NEW.practice_id FOR UPDATE;
          PERFORM 1 FROM public.practice_member WHERE id=NEW.owner_practice_member_id
            AND practice_id=NEW.practice_id AND doctor_id=NEW.owner_doctor_id FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'SMS approval owner is unavailable' USING ERRCODE = '55000';
          END IF;
          SELECT COALESCE(MAX(event_sequence),0)+1 INTO next_sequence
            FROM public.visit_prep_sms_approval_event
            WHERE owner_practice_member_id=NEW.owner_practice_member_id;
          checked_at := clock_timestamp();
          IF NEW.event_sequence <> next_sequence OR NEW.recorded_at > checked_at OR
             (NEW.event_kind='approved' AND NEW.expires_at <= checked_at) THEN
            RAISE EXCEPTION 'SMS approval sequence or time is invalid' USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER visit_prep_sms_approval_guard
        BEFORE INSERT OR UPDATE OR DELETE ON public.visit_prep_sms_approval_event
        FOR EACH ROW EXECUTE FUNCTION public.cosmas_guard_visit_prep_sms_approval();
        CREATE TRIGGER visit_prep_sms_approval_no_truncate
        BEFORE TRUNCATE ON public.visit_prep_sms_approval_event
        FOR EACH STATEMENT EXECUTE FUNCTION public.cosmas_guard_visit_prep_sms_approval();
    """)


def downgrade() -> None:
    raise RuntimeError('Retain SMS approval evidence; use a forward fix')
