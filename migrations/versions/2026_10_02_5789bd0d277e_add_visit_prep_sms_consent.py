"""add_visit_prep_sms_consent

Revision ID: 5789bd0d277e
Revises: 0b78b19b8d6e
Create Date: 2026-10-02 20:55:29.907396

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
import activemodel

# revision identifiers, used by Alembic.
revision: str = '5789bd0d277e'
down_revision: Union[str, None] = '0b78b19b8d6e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Generated against owned synthetic PostgreSQL; unrelated comment drift removed.
    op.create_table('visit_prep_sms_consent_event',
    sa.Column('id', activemodel.types.typeid.TypeIDType(prefix='visit_prep_sms_consent'), nullable=False, comment='TypeID with prefix: visit_prep_sms_consent'),
    sa.Column('visit_prep_id', activemodel.types.typeid.TypeIDType(prefix='visit_prep'), nullable=False),
    sa.Column('owner_practice_member_id', activemodel.types.typeid.TypeIDType(prefix='practice_member'), nullable=False),
    sa.Column('question_set_version_id', activemodel.types.typeid.TypeIDType(prefix='visit_prep_question_set_version'), nullable=False),
    sa.Column('credential_id', activemodel.types.typeid.TypeIDType(prefix='visit_prep_credential'), nullable=False),
    sa.Column('event_sequence', sa.Integer(), nullable=False),
    sa.Column('event_kind', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
    sa.Column('actor_role', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
    sa.Column('operation_id', sa.Uuid(), nullable=False),
    sa.Column('recipient_digest', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('disclosure_fingerprint', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('payload_hmac_sha256', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('evidence_ciphertext', sa.Text(), nullable=False),
    sa.Column('envelope_format_version', sa.Integer(), nullable=False),
    sa.Column('effective_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("effective_at <= recorded_at AND ((event_kind = 'granted' AND expires_at > recorded_at AND expires_at IS NOT NULL) OR (event_kind = 'withdrawn' AND expires_at IS NULL))", name=op.f('visit_prep_sms_consent_event_time_shape_check')),
    sa.CheckConstraint("event_sequence > 0 AND event_kind IN ('granted','withdrawn') AND actor_role IN ('patient','caregiver')", name=op.f('visit_prep_sms_consent_event_choice_shape_check')),
    sa.CheckConstraint("recipient_digest ~ '^[0-9a-f]{64}$' AND disclosure_fingerprint ~ '^[0-9a-f]{64}$' AND payload_hmac_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('visit_prep_sms_consent_event_digest_shape_check')),
    sa.CheckConstraint("substring(operation_id::text FROM 15 FOR 1) = '4' AND substring(operation_id::text FROM 20 FOR 1) IN ('8','9','a','b')", name=op.f('visit_prep_sms_consent_event_operation_shape_check')),
    sa.CheckConstraint('envelope_format_version = 1 AND octet_length(evidence_ciphertext) BETWEEN 1 AND 8192', name=op.f('visit_prep_sms_consent_event_evidence_shape_check')),
    sa.ForeignKeyConstraint(['credential_id'], ['visit_prep_credential_generation.id'], name='fk_visit_prep_sms_consent_credential', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['visit_prep_id', 'owner_practice_member_id', 'question_set_version_id'], ['visit_prep.id', 'visit_prep.owner_practice_member_id', 'visit_prep.question_set_version_id'], name='fk_visit_prep_sms_consent_root', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('visit_prep_sms_consent_event_pkey')),
    sa.UniqueConstraint('visit_prep_id', 'event_sequence', name='uq_visit_prep_sms_consent_sequence'),
    sa.UniqueConstraint('visit_prep_id', 'operation_id', name='uq_visit_prep_sms_consent_operation')
    )
    op.execute("""
        CREATE FUNCTION public.cosmas_guard_visit_prep_sms_consent()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        DECLARE root_row record; credential record; schedule record; prior record;
                checked_at timestamptz;
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'Visit Prep SMS consent evidence is append-only' USING ERRCODE = '55000';
          END IF;
          SELECT * INTO root_row FROM public.visit_prep WHERE id = NEW.visit_prep_id FOR UPDATE;
          IF NOT FOUND OR root_row.owner_practice_member_id <> NEW.owner_practice_member_id
             OR root_row.question_set_version_id <> NEW.question_set_version_id THEN
            RAISE EXCEPTION 'Visit Prep SMS consent requires current lineage' USING ERRCODE = '55000';
          END IF;
          SELECT * INTO credential FROM public.visit_prep_credential_generation
            WHERE id = NEW.credential_id FOR UPDATE;
          IF NOT FOUND OR credential.visit_prep_id <> root_row.id
             OR credential.owner_practice_member_id <> root_row.owner_practice_member_id THEN
            RAISE EXCEPTION 'Visit Prep SMS consent requires current lineage' USING ERRCODE = '55000';
          END IF;
          SELECT * INTO schedule FROM public.visit_prep_schedule_generation
            WHERE id = credential.schedule_generation_id FOR UPDATE;
          checked_at := clock_timestamp();
          IF NOT FOUND OR schedule.visit_prep_id <> root_row.id
             OR schedule.owner_practice_member_id <> root_row.owner_practice_member_id
             OR credential.generation <> (SELECT MAX(generation) FROM public.visit_prep_credential_generation WHERE visit_prep_id = root_row.id)
             OR schedule.generation <> (SELECT MAX(generation) FROM public.visit_prep_schedule_generation WHERE visit_prep_id = root_row.id)
             OR credential.revoked_at IS NOT NULL OR credential.expires_at <= checked_at
             OR root_row.revoked_at IS NOT NULL OR root_row.misdirected_at IS NOT NULL
             OR root_row.retain_until IS NULL OR root_row.retain_until <= checked_at OR root_row.confirmed_at IS NULL
             OR root_row.confirmed_by_role IS NULL OR root_row.confirmed_by_role <> NEW.actor_role OR NOT root_row.has_sms_contact
             OR credential.token_format_version <> 1
             OR credential.issued_at > NEW.effective_at OR NEW.effective_at < root_row.confirmed_at
             OR NEW.effective_at > checked_at THEN
            RAISE EXCEPTION 'Visit Prep SMS consent context is inactive or stale' USING ERRCODE = '55000';
          END IF;
          SELECT * INTO prior FROM public.visit_prep_sms_consent_event
            WHERE visit_prep_id = root_row.id ORDER BY event_sequence DESC LIMIT 1;
          IF NEW.event_sequence <> COALESCE(prior.event_sequence, 0) + 1
             OR (prior.id IS NOT NULL AND NEW.effective_at < prior.recorded_at)
             OR (NEW.event_kind = 'granted' AND (root_row.completed_at IS NOT NULL
                 OR NEW.expires_at <= checked_at
                 OR NEW.expires_at > LEAST(credential.expires_at, schedule.scheduled_at))) THEN
            RAISE EXCEPTION 'Visit Prep SMS consent sequence or deadline is not current' USING ERRCODE = '55000';
          END IF;
          NEW.recorded_at := checked_at;
          RETURN NEW;
        END $$;
        CREATE TRIGGER visit_prep_sms_consent_guard
        BEFORE INSERT OR UPDATE OR DELETE ON public.visit_prep_sms_consent_event
        FOR EACH ROW EXECUTE FUNCTION public.cosmas_guard_visit_prep_sms_consent();

        CREATE FUNCTION public.cosmas_deny_visit_prep_sms_consent_truncate()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'Visit Prep SMS consent evidence cannot be truncated' USING ERRCODE = '55000';
        END $$;
        CREATE TRIGGER visit_prep_sms_consent_no_truncate BEFORE TRUNCATE ON public.visit_prep_sms_consent_event
        FOR EACH STATEMENT EXECUTE FUNCTION public.cosmas_deny_visit_prep_sms_consent_truncate();
    """)


def downgrade() -> None:
    raise RuntimeError('Retain SMS consent evidence; use a forward fix')
