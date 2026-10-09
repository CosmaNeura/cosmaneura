"""add_scribe_capture_gap_manifest

Revision ID: b8d3e1ba9d08
Revises: ea61b75ac5b0
Create Date: 2026-09-17 15:21:47.779269

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
import activemodel


# revision identifiers, used by Alembic.
revision: str = 'b8d3e1ba9d08'
down_revision: Union[str, None] = 'ea61b75ac5b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    # Generated from metadata against only the disposable W8 Core database.
    op.create_table('scribe_capture_gap_manifest',
    sa.Column('id', activemodel.types.typeid.TypeIDType(prefix='scribe_gap_manifest'), nullable=False, comment='TypeID with prefix: scribe_gap_manifest'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('binding_id', activemodel.types.typeid.TypeIDType(prefix='scribe_capture_binding'), nullable=False),
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('raw_source_id', sa.Uuid(), nullable=False),
    sa.Column('raw_source_sha256', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('recovery_sha256', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('recovery_canonical', sa.Text(), nullable=False),
    sa.Column('raw_core_transcript_id', activemodel.types.typeid.TypeIDType(prefix='ai_visit_transcript'), nullable=False),
    sa.Column('initial_transcript_id', activemodel.types.typeid.TypeIDType(prefix='ai_visit_transcript'), nullable=True),
    sa.Column('initial_source_sha256', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
    sa.CheckConstraint("COALESCE((octet_length(recovery_canonical) BETWEEN 2 AND 2000000 AND encode(sha256(convert_to(recovery_canonical, 'UTF8')), 'hex') = recovery_sha256 AND jsonb_typeof(recovery_canonical::jsonb) = 'object' AND recovery_canonical::jsonb->>'session_id' = session_id::text AND jsonb_typeof(recovery_canonical::jsonb->'recoveries') = 'array' AND jsonb_array_length(recovery_canonical::jsonb->'recoveries') BETWEEN 1 AND 1000), false)", name=op.f('scribe_capture_gap_manifest_ck_scribe_gap_canonical_manifest_check')),
    sa.CheckConstraint("raw_source_sha256 ~ '^[0-9a-f]{64}$' AND recovery_sha256 ~ '^[0-9a-f]{64}$' AND (initial_source_sha256 IS NULL OR initial_source_sha256 ~ '^[0-9a-f]{64}$')", name=op.f('scribe_capture_gap_manifest_ck_scribe_gap_hashes_check')),
    sa.CheckConstraint('(initial_transcript_id IS NULL) = (initial_source_sha256 IS NULL)', name=op.f('scribe_capture_gap_manifest_ck_scribe_gap_initial_mapping_check')),
    sa.ForeignKeyConstraint(['binding_id'], ['scribe_capture_binding.id'], name=op.f('scribe_capture_gap_manifest_binding_id_fkey')),
    sa.ForeignKeyConstraint(['initial_transcript_id'], ['ai_visit_transcript.id'], name=op.f('scribe_capture_gap_manifest_initial_transcript_id_fkey')),
    sa.ForeignKeyConstraint(['raw_core_transcript_id'], ['ai_visit_transcript.id'], name=op.f('scribe_capture_gap_manifest_raw_core_transcript_id_fkey')),
    sa.PrimaryKeyConstraint('id', name=op.f('scribe_capture_gap_manifest_pkey')),
    sa.UniqueConstraint('binding_id', name='uq_scribe_gap_manifest_binding'),
    sa.UniqueConstraint('initial_transcript_id', name='uq_scribe_gap_initial_transcript'),
    sa.UniqueConstraint('session_id', name='uq_scribe_gap_manifest_session')
    )
    # ### end Alembic commands ###
    op.execute("""CREATE FUNCTION enforce_scribe_gap_manifest_insert() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        DECLARE binding record; admitted record; raw record; effective record; item jsonb;
        BEGIN
            SELECT doctor_id,patient_id,appointment_id,revoked_at INTO binding
            FROM public.scribe_capture_binding WHERE id=NEW.binding_id FOR UPDATE;
            IF NOT FOUND OR binding.revoked_at IS NOT NULL THEN
                RAISE EXCEPTION 'gap manifest binding unavailable';
            END IF;
            SELECT session_id,status,consent_payload INTO admitted FROM public.scribe_capture_admission
            WHERE binding_id=NEW.binding_id FOR UPDATE;
            IF NOT FOUND OR admitted.session_id IS DISTINCT FROM NEW.session_id
                OR admitted.status IS DISTINCT FROM 'ready' THEN
                RAISE EXCEPTION 'gap manifest admission unavailable';
            END IF;
            PERFORM id FROM public.appointment WHERE id=binding.appointment_id FOR UPDATE;
            SELECT doctor_id,patient_id,appointment_id,deleted_at INTO raw
            FROM public.ai_visit_transcript WHERE id=NEW.raw_core_transcript_id;
            IF NOT FOUND OR raw.deleted_at IS NOT NULL OR raw.doctor_id <> binding.doctor_id
                OR raw.patient_id <> binding.patient_id OR raw.appointment_id <> binding.appointment_id THEN
                RAISE EXCEPTION 'gap manifest raw scope mismatch';
            END IF;
            PERFORM id FROM public.scribe_transcript_delivery
            WHERE logical_producer='cosmaneura-scribe-v1' AND scribe_session_id=NEW.session_id
                AND transcript_id=NEW.raw_core_transcript_id AND appointment_id=binding.appointment_id;
            IF NOT FOUND THEN RAISE EXCEPTION 'gap manifest raw delivery unavailable'; END IF;
            IF NEW.initial_transcript_id IS NOT NULL THEN
                SELECT doctor_id,patient_id,appointment_id,deleted_at INTO effective
                FROM public.ai_visit_transcript WHERE id=NEW.initial_transcript_id;
                IF NOT FOUND OR effective.deleted_at IS NOT NULL
                    OR NEW.initial_transcript_id=NEW.raw_core_transcript_id
                    OR effective.doctor_id <> binding.doctor_id OR effective.patient_id <> binding.patient_id
                    OR effective.appointment_id <> binding.appointment_id THEN
                    RAISE EXCEPTION 'gap manifest initial transcript scope mismatch';
                END IF;
            END IF;
            IF EXISTS (SELECT 1 FROM public.scribe_capture_source_version
                WHERE binding_id=NEW.binding_id AND (raw_source_id IS DISTINCT FROM NEW.raw_source_id
                    OR raw_source_sha256 IS DISTINCT FROM NEW.raw_source_sha256
                    OR raw_core_transcript_id IS DISTINCT FROM NEW.raw_core_transcript_id)) THEN
                RAISE EXCEPTION 'gap manifest working origin mismatch';
            END IF;
            FOR item IN SELECT value FROM jsonb_array_elements(NEW.recovery_canonical::jsonb->'recoveries') LOOP
                IF item->>'session_id' IS DISTINCT FROM NEW.session_id::text
                    OR item->>'acknowledged_by_practitioner_id' IS DISTINCT FROM admitted.consent_payload->>'practitioner_id'
                    OR item->>'review_state' IS DISTINCT FROM 'required'
                    OR item->'lease'->>'session_id' IS DISTINCT FROM NEW.session_id::text THEN
                    RAISE EXCEPTION 'gap manifest acknowledgment scope mismatch';
                END IF;
            END LOOP;
            RETURN NEW;
        END $$""")
    op.execute("""CREATE TRIGGER scribe_gap_manifest_insert
        BEFORE INSERT ON scribe_capture_gap_manifest
        FOR EACH ROW EXECUTE FUNCTION enforce_scribe_gap_manifest_insert()""")
    op.execute("""CREATE FUNCTION reject_scribe_gap_manifest_change() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'gap manifest history is immutable';
        END $$""")
    op.execute("""CREATE TRIGGER scribe_gap_manifest_immutable
        BEFORE UPDATE OR DELETE ON scribe_capture_gap_manifest
        FOR EACH ROW EXECUTE FUNCTION reject_scribe_gap_manifest_change()""")
    op.execute("""CREATE FUNCTION enforce_scribe_gap_working_origin() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        DECLARE manifest record;
        BEGIN
            SELECT session_id,raw_source_id,raw_source_sha256,raw_core_transcript_id INTO manifest
            FROM public.scribe_capture_gap_manifest WHERE binding_id=NEW.binding_id;
            IF FOUND AND (NEW.session_id IS DISTINCT FROM manifest.session_id
                OR NEW.raw_source_id IS DISTINCT FROM manifest.raw_source_id
                OR NEW.raw_source_sha256 IS DISTINCT FROM manifest.raw_source_sha256
                OR NEW.raw_core_transcript_id IS DISTINCT FROM manifest.raw_core_transcript_id) THEN
                RAISE EXCEPTION 'working source gap origin mismatch';
            END IF;
            RETURN NEW;
        END $$""")
    op.execute("""CREATE TRIGGER scribe_gap_working_source_insert
        BEFORE INSERT ON scribe_capture_source_version
        FOR EACH ROW EXECUTE FUNCTION enforce_scribe_gap_working_origin()""")


def downgrade() -> None:
    raise RuntimeError("Disable qualified capture sources; retained gap manifests cannot be destructively downgraded")
