"""add_visit_prep_unmatched_cancellation

Revision ID: 0b78b19b8d6e
Revises: 3a7854eb39e0
Create Date: 2026-10-02 20:37:35.227267

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
import activemodel

# revision identifiers, used by Alembic.
revision: str = '0b78b19b8d6e'
down_revision: Union[str, None] = '3a7854eb39e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Generated in owned synthetic PostgreSQL; unrelated comment drift removed.
    op.create_table('visit_prep_booking_cancellation',
    sa.Column('id', activemodel.types.typeid.TypeIDType(prefix='visit_prep_booking_cancellation'), nullable=False, comment='TypeID with prefix: visit_prep_booking_cancellation'),
    sa.Column('connection_id', sa.Uuid(), nullable=False),
    sa.Column('source', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
    sa.Column('booking_digest', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('source_snapshot_hmac', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('owner_practice_member_id', activemodel.types.typeid.TypeIDType(prefix='practice_member'), nullable=False),
    sa.Column('practice_id', activemodel.types.typeid.TypeIDType(prefix='practice'), nullable=False),
    sa.Column('owner_doctor_id', activemodel.types.typeid.TypeIDType(prefix='doctor'), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("source = 'acuity' AND booking_digest ~ '^[0-9a-f]{64}$' AND source_snapshot_hmac ~ '^[0-9a-f]{64}$' AND connection_id <> '00000000-0000-0000-0000-000000000000'::uuid", name=op.f('visit_prep_booking_cancellation_source_shape_check')),
    sa.CheckConstraint('observed_at IS NOT NULL AND recorded_at IS NOT NULL AND observed_at <= recorded_at', name=op.f('visit_prep_booking_cancellation_time_shape_check')),
    sa.CheckConstraint('sequence > 0', name=op.f('visit_prep_booking_cancellation_sequence_shape_check')),
    sa.ForeignKeyConstraint(['owner_practice_member_id', 'practice_id', 'owner_doctor_id'], ['practice_member.id', 'practice_member.practice_id', 'practice_member.doctor_id'], name='fk_visit_prep_booking_cancellation_owner', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('visit_prep_booking_cancellation_pkey')),
    sa.UniqueConstraint('connection_id', 'booking_digest', 'sequence', name='uq_visit_prep_booking_cancellation_sequence')
    )
    op.execute("""
        CREATE FUNCTION public.cosmas_guard_visit_prep_booking_cancellation()
        RETURNS trigger LANGUAGE plpgsql AS $guard$
        DECLARE
          prior public.visit_prep_booking_cancellation%ROWTYPE;
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'Visit Prep unmatched cancellation evidence is immutable'
              USING ERRCODE = '55000';
          END IF;
          -- Match the caller's canonical practice-before-member serialization.
          PERFORM 1 FROM public.practice WHERE id = NEW.practice_id FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'Visit Prep cancellation requires its owner practice'
              USING ERRCODE = '55000';
          END IF;
          PERFORM 1 FROM public.practice_member
           WHERE id = NEW.owner_practice_member_id AND practice_id = NEW.practice_id
             AND doctor_id = NEW.owner_doctor_id FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'Visit Prep cancellation requires its owner practice'
              USING ERRCODE = '55000';
          END IF;
          SELECT * INTO prior FROM public.visit_prep_booking_cancellation
           WHERE connection_id = NEW.connection_id AND booking_digest = NEW.booking_digest
           ORDER BY sequence DESC LIMIT 1;
          IF NEW.sequence <> COALESCE(prior.sequence, 0) + 1
             OR NEW.observed_at < COALESCE(prior.recorded_at, NEW.observed_at)
             OR NEW.recorded_at > clock_timestamp()
             OR (prior.id IS NOT NULL AND (
                 prior.owner_practice_member_id <> NEW.owner_practice_member_id
                 OR prior.practice_id <> NEW.practice_id
                 OR prior.owner_doctor_id <> NEW.owner_doctor_id))
             OR EXISTS (SELECT 1 FROM public.visit_prep_booking_receipt
                        WHERE connection_id = NEW.connection_id
                          AND booking_digest = NEW.booking_digest) THEN
            RAISE EXCEPTION 'Visit Prep cancellation requires current unmatched source'
              USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END; $guard$;
        CREATE TRIGGER visit_prep_booking_cancellation_guard
        BEFORE INSERT OR UPDATE OR DELETE ON public.visit_prep_booking_cancellation
        FOR EACH ROW EXECUTE FUNCTION public.cosmas_guard_visit_prep_booking_cancellation();
    """)


def downgrade() -> None:
    raise RuntimeError('Retain unmatched cancellation evidence; use a forward fix')
