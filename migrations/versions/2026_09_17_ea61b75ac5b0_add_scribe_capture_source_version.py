"""add_scribe_capture_source_version

Revision ID: ea61b75ac5b0
Revises: f2388a506337
Create Date: 2026-09-17 12:16:48.583339

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
import activemodel


# revision identifiers, used by Alembic.
revision: str = 'ea61b75ac5b0'
down_revision: Union[str, None] = 'f2388a506337'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    # Generated from metadata against only the disposable W8 Core database.
    op.create_table('scribe_capture_source_version',
    sa.Column('id', activemodel.types.typeid.TypeIDType(prefix='scribe_source_version'), nullable=False, comment='TypeID with prefix: scribe_source_version'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('binding_id', activemodel.types.typeid.TypeIDType(prefix='scribe_capture_binding'), nullable=False),
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('raw_source_id', sa.Uuid(), nullable=False),
    sa.Column('raw_source_sha256', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('working_transcript_id', sa.Uuid(), nullable=False),
    sa.Column('working_version', sa.Integer(), nullable=False),
    sa.Column('working_sha256', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('source_sha256', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('raw_core_transcript_id', activemodel.types.typeid.TypeIDType(prefix='ai_visit_transcript'), nullable=False),
    sa.Column('transcript_id', activemodel.types.typeid.TypeIDType(prefix='ai_visit_transcript'), nullable=False),
    sa.CheckConstraint("raw_source_sha256 ~ '^[0-9a-f]{64}$' AND working_sha256 ~ '^[0-9a-f]{64}$' AND source_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('scribe_capture_source_version_ck_scribe_working_hashes_check')),
    sa.CheckConstraint('working_version >= 2', name=op.f('scribe_capture_source_version_ck_scribe_working_version_check')),
    sa.ForeignKeyConstraint(['binding_id'], ['scribe_capture_binding.id'], name=op.f('scribe_capture_source_version_binding_id_fkey')),
    sa.ForeignKeyConstraint(['raw_core_transcript_id'], ['ai_visit_transcript.id'], name=op.f('scribe_capture_source_version_raw_core_transcript_id_fkey')),
    sa.ForeignKeyConstraint(['transcript_id'], ['ai_visit_transcript.id'], name=op.f('scribe_capture_source_version_transcript_id_fkey')),
    sa.PrimaryKeyConstraint('id', name=op.f('scribe_capture_source_version_pkey')),
    sa.UniqueConstraint('binding_id', 'working_version', name='uq_scribe_working_version'),
    sa.UniqueConstraint('transcript_id', name='uq_scribe_working_core_transcript')
    )
    # ### end Alembic commands ###
    op.execute("""CREATE FUNCTION enforce_scribe_working_source_insert() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        DECLARE binding record; admitted record; prior record; raw record; effective record;
        BEGIN
            SELECT doctor_id, patient_id, appointment_id, revoked_at INTO binding
            FROM public.scribe_capture_binding WHERE id=NEW.binding_id FOR UPDATE;
            IF NOT FOUND OR binding.revoked_at IS NOT NULL THEN
                RAISE EXCEPTION 'working source binding unavailable';
            END IF;
            SELECT session_id, status INTO admitted FROM public.scribe_capture_admission
            WHERE binding_id=NEW.binding_id FOR UPDATE;
            IF NOT FOUND OR admitted.session_id IS DISTINCT FROM NEW.session_id
                OR admitted.status IS DISTINCT FROM 'ready' THEN
                RAISE EXCEPTION 'working source admission unavailable';
            END IF;
            PERFORM id FROM public.appointment WHERE id=binding.appointment_id FOR UPDATE;
            SELECT doctor_id,patient_id,appointment_id,deleted_at INTO raw
            FROM public.ai_visit_transcript WHERE id=NEW.raw_core_transcript_id;
            IF NOT FOUND OR raw.deleted_at IS NOT NULL OR raw.doctor_id <> binding.doctor_id
                OR raw.patient_id <> binding.patient_id OR raw.appointment_id <> binding.appointment_id THEN
                RAISE EXCEPTION 'working source raw scope mismatch';
            END IF;
            PERFORM id FROM public.scribe_transcript_delivery
            WHERE logical_producer='cosmaneura-scribe-v1' AND scribe_session_id=NEW.session_id
                AND transcript_id=NEW.raw_core_transcript_id AND appointment_id=binding.appointment_id;
            IF NOT FOUND THEN RAISE EXCEPTION 'working source raw delivery unavailable'; END IF;
            SELECT doctor_id,patient_id,appointment_id,deleted_at INTO effective
            FROM public.ai_visit_transcript WHERE id=NEW.transcript_id;
            IF NOT FOUND OR effective.deleted_at IS NOT NULL OR NEW.transcript_id=NEW.raw_core_transcript_id
                OR effective.doctor_id <> binding.doctor_id OR effective.patient_id <> binding.patient_id
                OR effective.appointment_id <> binding.appointment_id THEN
                RAISE EXCEPTION 'working source transcript scope mismatch';
            END IF;
            SELECT working_version,raw_source_id,raw_source_sha256,working_transcript_id,raw_core_transcript_id
            INTO prior FROM public.scribe_capture_source_version WHERE binding_id=NEW.binding_id
            ORDER BY working_version DESC LIMIT 1;
            IF FOUND AND (NEW.working_version <= prior.working_version
                OR NEW.raw_source_id IS DISTINCT FROM prior.raw_source_id
                OR NEW.raw_source_sha256 IS DISTINCT FROM prior.raw_source_sha256
                OR NEW.working_transcript_id IS DISTINCT FROM prior.working_transcript_id
                OR NEW.raw_core_transcript_id IS DISTINCT FROM prior.raw_core_transcript_id) THEN
                RAISE EXCEPTION 'working source origin or version changed';
            END IF;
            RETURN NEW;
        END $$""")
    op.execute("""CREATE TRIGGER scribe_working_source_insert
        BEFORE INSERT ON scribe_capture_source_version
        FOR EACH ROW EXECUTE FUNCTION enforce_scribe_working_source_insert()""")
    op.execute("""CREATE FUNCTION reject_scribe_working_source_change() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'working source history is immutable';
        END $$""")
    op.execute("""CREATE TRIGGER scribe_working_source_immutable
        BEFORE UPDATE OR DELETE ON scribe_capture_source_version
        FOR EACH ROW EXECUTE FUNCTION reject_scribe_working_source_change()""")


def downgrade() -> None:
    raise RuntimeError("Disable working capture; retained source mappings cannot be destructively downgraded")
