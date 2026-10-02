"""add_visit_prep_booking_receipt

Revision ID: b3f91f1acb40
Revises: 208271feaef7
Create Date: 2026-10-02 18:25:44.455705

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
import activemodel

# revision identifiers, used by Alembic.
revision: str = 'b3f91f1acb40'
down_revision: Union[str, None] = '208271feaef7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Generated in owned synthetic PostgreSQL; unrelated comment drift removed.
    op.create_table('visit_prep_booking_receipt',
    sa.Column('id', activemodel.types.typeid.TypeIDType(prefix='visit_prep_booking'), nullable=False, comment='TypeID with prefix: visit_prep_booking'),
    sa.Column('connection_id', sa.Uuid(), nullable=False),
    sa.Column('source', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
    sa.Column('booking_digest', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('snapshot_hmac', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('visit_prep_id', activemodel.types.typeid.TypeIDType(prefix='visit_prep'), nullable=False),
    sa.Column('owner_practice_member_id', activemodel.types.typeid.TypeIDType(prefix='practice_member'), nullable=False),
    sa.Column('question_set_version_id', activemodel.types.typeid.TypeIDType(prefix='visit_prep_question_set_version'), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("source = 'acuity' AND booking_digest ~ '^[0-9a-f]{64}$' AND snapshot_hmac ~ '^[0-9a-f]{64}$'", name=op.f('visit_prep_booking_receipt_source_shape_check')),
    sa.CheckConstraint('recorded_at IS NOT NULL', name=op.f('visit_prep_booking_receipt_recorded_shape_check')),
    sa.ForeignKeyConstraint(['visit_prep_id', 'owner_practice_member_id', 'question_set_version_id'], ['visit_prep.id', 'visit_prep.owner_practice_member_id', 'visit_prep.question_set_version_id'], name='fk_visit_prep_booking_root', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('visit_prep_booking_receipt_pkey')),
    sa.UniqueConstraint('connection_id', 'booking_digest', name='uq_visit_prep_booking_source'),
    sa.UniqueConstraint('visit_prep_id', name='uq_visit_prep_booking_preparation')
    )
    op.execute("""
        CREATE FUNCTION public.cosmas_guard_visit_prep_booking() RETURNS trigger
        LANGUAGE plpgsql AS $guard$
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'Visit Prep booking receipts are immutable; retain evidence'
              USING ERRCODE = '55000';
          END IF;
          PERFORM 1 FROM public.visit_prep p
           WHERE p.id = NEW.visit_prep_id
             AND p.owner_practice_member_id = NEW.owner_practice_member_id
             AND p.question_set_version_id = NEW.question_set_version_id
             AND p.creation_source = 'appointment_booking'
             AND p.created_at <= NEW.recorded_at
             AND NEW.recorded_at <= clock_timestamp()
             AND NEW.connection_id <> '00000000-0000-0000-0000-000000000000'::uuid;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'Visit Prep booking receipt requires its current creation source'
              USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END; $guard$;
        CREATE TRIGGER visit_prep_booking_guard
        BEFORE INSERT OR UPDATE OR DELETE ON public.visit_prep_booking_receipt
        FOR EACH ROW EXECUTE FUNCTION public.cosmas_guard_visit_prep_booking();
    """)


def downgrade() -> None:
    raise RuntimeError('Visit Prep booking evidence requires a forward fix; retain the dormant expand')
