"""add_visit_prep_booking_lifecycle_evidence

Revision ID: ac4d76856637
Revises: c3aa7a31f3ae
Create Date: 2026-10-02 19:34:17.266281

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
import activemodel

# revision identifiers, used by Alembic.
revision: str = 'ac4d76856637'
down_revision: Union[str, None] = 'c3aa7a31f3ae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Generated in owned synthetic PostgreSQL; unrelated comment drift removed.
    op.create_table('visit_prep_booking_event',
    sa.Column('id', activemodel.types.typeid.TypeIDType(prefix='visit_prep_booking_event'), nullable=False, comment='TypeID with prefix: visit_prep_booking_event'),
    sa.Column('booking_receipt_id', activemodel.types.typeid.TypeIDType(prefix='visit_prep_booking'), nullable=False),
    sa.Column('visit_prep_id', activemodel.types.typeid.TypeIDType(prefix='visit_prep'), nullable=False),
    sa.Column('owner_practice_member_id', activemodel.types.typeid.TypeIDType(prefix='practice_member'), nullable=False),
    sa.Column('question_set_version_id', activemodel.types.typeid.TypeIDType(prefix='visit_prep_question_set_version'), nullable=False),
    sa.Column('schedule_generation_id', activemodel.types.typeid.TypeIDType(prefix='visit_prep_schedule'), nullable=False),
    sa.Column('credential_generation_id', activemodel.types.typeid.TypeIDType(prefix='visit_prep_credential'), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('action', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
    sa.Column('prior_snapshot_hmac', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('source_snapshot_hmac', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("action IN ('rescheduled', 'cancelled', 'recipient_changed')", name=op.f('visit_prep_booking_event_action_shape_check')),
    sa.CheckConstraint("prior_snapshot_hmac ~ '^[0-9a-f]{64}$' AND source_snapshot_hmac ~ '^[0-9a-f]{64}$'", name=op.f('visit_prep_booking_event_snapshot_shape_check')),
    sa.CheckConstraint('observed_at IS NOT NULL AND recorded_at IS NOT NULL AND observed_at <= recorded_at', name=op.f('visit_prep_booking_event_time_shape_check')),
    sa.CheckConstraint('sequence > 0', name=op.f('visit_prep_booking_event_sequence_shape_check')),
    sa.ForeignKeyConstraint(['booking_receipt_id'], ['visit_prep_booking_receipt.id'], name='fk_visit_prep_booking_event_receipt', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['credential_generation_id'], ['visit_prep_credential_generation.id'], name='fk_visit_prep_booking_event_credential', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['schedule_generation_id'], ['visit_prep_schedule_generation.id'], name='fk_visit_prep_booking_event_schedule', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['visit_prep_id', 'owner_practice_member_id', 'question_set_version_id'], ['visit_prep.id', 'visit_prep.owner_practice_member_id', 'visit_prep.question_set_version_id'], name='fk_visit_prep_booking_event_root', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('visit_prep_booking_event_pkey')),
    sa.UniqueConstraint('booking_receipt_id', 'sequence', name='uq_visit_prep_booking_event_sequence')
    )
    op.execute("""
        CREATE FUNCTION public.cosmas_guard_visit_prep_booking_event()
        RETURNS trigger LANGUAGE plpgsql AS $guard$
        DECLARE
          receipt public.visit_prep_booking_receipt%ROWTYPE;
          prep public.visit_prep%ROWTYPE;
          prior public.visit_prep_booking_event%ROWTYPE;
          schedule public.visit_prep_schedule_generation%ROWTYPE;
          credential public.visit_prep_credential_generation%ROWTYPE;
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'Visit Prep booking lifecycle evidence is immutable'
              USING ERRCODE = '55000';
          END IF;
          SELECT * INTO receipt FROM public.visit_prep_booking_receipt
           WHERE id = NEW.booking_receipt_id;
          IF NOT FOUND OR receipt.visit_prep_id <> NEW.visit_prep_id
             OR receipt.owner_practice_member_id <> NEW.owner_practice_member_id
             OR receipt.question_set_version_id <> NEW.question_set_version_id THEN
            RAISE EXCEPTION 'Visit Prep booking event requires its original receipt'
              USING ERRCODE = '55000';
          END IF;
          SELECT * INTO prep FROM public.visit_prep
           WHERE id = receipt.visit_prep_id FOR UPDATE;
          SELECT * INTO prior FROM public.visit_prep_booking_event
           WHERE booking_receipt_id = receipt.id ORDER BY sequence DESC LIMIT 1;
          IF NEW.sequence <> COALESCE(prior.sequence, 0) + 1
             OR NEW.prior_snapshot_hmac IS DISTINCT FROM
                COALESCE(prior.source_snapshot_hmac, receipt.snapshot_hmac)
             OR NEW.source_snapshot_hmac = NEW.prior_snapshot_hmac
             OR NEW.observed_at < COALESCE(prior.recorded_at, receipt.recorded_at)
             OR NEW.recorded_at > clock_timestamp() THEN
            RAISE EXCEPTION 'Visit Prep booking event source lineage is stale'
              USING ERRCODE = '55000';
          END IF;
          SELECT * INTO schedule FROM public.visit_prep_schedule_generation
           WHERE id = NEW.schedule_generation_id;
          SELECT * INTO credential FROM public.visit_prep_credential_generation
           WHERE id = NEW.credential_generation_id;
          IF schedule.id IS NULL OR credential.id IS NULL
             OR schedule.visit_prep_id <> prep.id
             OR schedule.owner_practice_member_id <> NEW.owner_practice_member_id
             OR credential.visit_prep_id <> prep.id
             OR credential.owner_practice_member_id <> NEW.owner_practice_member_id
             OR credential.schedule_generation_id <> schedule.id
             OR schedule.created_at > NEW.recorded_at
             OR credential.issued_at > NEW.recorded_at
             OR EXISTS (SELECT 1 FROM public.visit_prep_schedule_generation s
                        WHERE s.visit_prep_id = prep.id
                          AND s.generation > schedule.generation)
             OR EXISTS (SELECT 1 FROM public.visit_prep_credential_generation c
                        WHERE c.visit_prep_id = prep.id
                          AND c.generation > credential.generation) THEN
            RAISE EXCEPTION 'Visit Prep booking event requires current lifecycle'
              USING ERRCODE = '55000';
          END IF;
          IF NEW.action = 'rescheduled' THEN
            IF prep.revoked_at IS NOT NULL OR credential.revoked_at IS NOT NULL
               OR schedule.generation <= COALESCE(
                  (SELECT generation FROM public.visit_prep_schedule_generation
                   WHERE id = prior.schedule_generation_id), 1) THEN
              RAISE EXCEPTION 'Visit Prep booking event requires reschedule result'
                USING ERRCODE = '55000';
            END IF;
          ELSE
            IF prep.revoked_at IS NULL OR credential.revoked_at IS NULL
               OR prep.revoked_at > NEW.recorded_at
               OR credential.revoked_at > NEW.recorded_at
               OR prep.revocation_reason IS DISTINCT FROM
                  (CASE WHEN NEW.action = 'cancelled' THEN 'appointment_cancelled'
                        ELSE 'physician_revoked' END)
               OR credential.revocation_reason IS DISTINCT FROM prep.revocation_reason THEN
              RAISE EXCEPTION 'Visit Prep booking event requires revocation result'
                USING ERRCODE = '55000';
            END IF;
          END IF;
          RETURN NEW;
        END; $guard$;
        CREATE TRIGGER visit_prep_booking_event_guard
        BEFORE INSERT OR UPDATE OR DELETE ON public.visit_prep_booking_event
        FOR EACH ROW EXECUTE FUNCTION public.cosmas_guard_visit_prep_booking_event();
    """)


def downgrade() -> None:
    raise RuntimeError('Retain Visit Prep lifecycle evidence; use a forward fix')
