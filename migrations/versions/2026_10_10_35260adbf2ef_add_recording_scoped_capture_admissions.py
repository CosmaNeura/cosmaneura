"""add_recording_scoped_capture_admissions

Revision ID: 35260adbf2ef
Revises: 56789df3c14c
Create Date: 2026-10-10 12:28:58.684375

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
import activemodel


# revision identifiers, used by Alembic.
revision: str = '35260adbf2ef'
down_revision: Union[str, None] = '56789df3c14c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.add_column('scribe_capture_admission', sa.Column('recording_key_sha256', sa.String(64), nullable=False, server_default='legacy'))
    op.create_check_constraint('ck_scribe_admission_recording_key', 'scribe_capture_admission', "recording_key_sha256 = 'legacy' OR recording_key_sha256 ~ '^[a-f0-9]{64}$'")
    op.create_unique_constraint('uq_scribe_admission_recording', 'scribe_capture_admission', ['binding_id', 'recording_key_sha256'])
    op.drop_constraint('uq_scribe_admission_binding', 'scribe_capture_admission', type_='unique')
    for table, name in (
        ('scribe_capture_source_version', 'uq_scribe_working_version'),
        ('scribe_capture_reviewed_source', 'uq_scribe_review_working_version'),
    ):
        op.drop_constraint(name, table, type_='unique')
        op.create_unique_constraint(name, table, ['binding_id', 'session_id', 'working_version'])
    op.drop_constraint('uq_scribe_gap_manifest_binding', 'scribe_capture_gap_manifest', type_='unique')
    op.create_unique_constraint('uq_scribe_gap_manifest_binding', 'scribe_capture_gap_manifest', ['binding_id', 'session_id'])
    op.execute("""CREATE OR REPLACE FUNCTION enforce_scribe_working_source_insert() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        DECLARE binding record; admitted record; prior record; raw record; effective record;
        BEGIN
            SELECT doctor_id, patient_id, appointment_id, revoked_at INTO binding
            FROM public.scribe_capture_binding WHERE id=NEW.binding_id FOR UPDATE;
            IF NOT FOUND OR binding.revoked_at IS NOT NULL THEN
                RAISE EXCEPTION 'working source binding unavailable';
            END IF;
            SELECT session_id, status INTO admitted FROM public.scribe_capture_admission
            WHERE binding_id=NEW.binding_id AND session_id=NEW.session_id FOR UPDATE;
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
            INTO prior FROM public.scribe_capture_source_version WHERE binding_id=NEW.binding_id AND session_id=NEW.session_id
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
    op.execute("""CREATE OR REPLACE FUNCTION enforce_scribe_gap_manifest_insert() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        DECLARE binding record; admitted record; raw record; effective record; item jsonb;
        BEGIN
            SELECT doctor_id,patient_id,appointment_id,revoked_at INTO binding
            FROM public.scribe_capture_binding WHERE id=NEW.binding_id FOR UPDATE;
            IF NOT FOUND OR binding.revoked_at IS NOT NULL THEN
                RAISE EXCEPTION 'gap manifest binding unavailable';
            END IF;
            SELECT session_id,status,consent_payload INTO admitted FROM public.scribe_capture_admission
            WHERE binding_id=NEW.binding_id AND session_id=NEW.session_id FOR UPDATE;
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
                WHERE binding_id=NEW.binding_id AND session_id=NEW.session_id AND (raw_source_id IS DISTINCT FROM NEW.raw_source_id
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
    op.execute("""CREATE OR REPLACE FUNCTION enforce_scribe_gap_working_origin()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        DECLARE manifest record;
        BEGIN
            -- This trigger sorts before the working-source guard. Serialize
            -- before reading the manifest, not after taking that snapshot.
            PERFORM id FROM public.scribe_capture_binding
            WHERE id=NEW.binding_id FOR UPDATE;
            SELECT session_id,raw_source_id,raw_source_sha256,raw_core_transcript_id
            INTO manifest FROM public.scribe_capture_gap_manifest
            WHERE binding_id=NEW.binding_id AND session_id=NEW.session_id;
            IF FOUND AND (NEW.session_id IS DISTINCT FROM manifest.session_id
                OR NEW.raw_source_id IS DISTINCT FROM manifest.raw_source_id
                OR NEW.raw_source_sha256 IS DISTINCT FROM manifest.raw_source_sha256
                OR NEW.raw_core_transcript_id IS DISTINCT FROM manifest.raw_core_transcript_id) THEN
                RAISE EXCEPTION 'working source gap origin mismatch';
            END IF;
            RETURN NEW;
        END $$""")
    op.execute("""CREATE OR REPLACE FUNCTION guard_scribe_reviewed_source()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        DECLARE binding record; admitted record;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'reviewed source mapping is immutable';
            END IF;
            SELECT doctor_id, patient_id, appointment_id, revoked_at INTO binding
            FROM public.scribe_capture_binding WHERE id=NEW.binding_id FOR UPDATE;
            IF NOT FOUND OR binding.revoked_at IS NOT NULL THEN
                RAISE EXCEPTION 'reviewed source binding unavailable';
            END IF;
            SELECT session_id, status, configured_capture INTO admitted
            FROM public.scribe_capture_admission WHERE binding_id=NEW.binding_id AND session_id=NEW.session_id FOR UPDATE;
            IF NOT FOUND OR admitted.session_id IS DISTINCT FROM NEW.session_id
                OR admitted.status IS DISTINCT FROM 'ready'
                OR admitted.configured_capture IS NULL THEN
                RAISE EXCEPTION 'reviewed source scope mismatch';
            END IF;
            PERFORM id FROM public.appointment WHERE id=binding.appointment_id FOR UPDATE;
            IF NOT EXISTS (
                SELECT 1 FROM public.ai_visit_transcript t
                WHERE t.id=NEW.transcript_id AND t.appointment_id=binding.appointment_id
                  AND t.patient_id=binding.patient_id AND t.doctor_id=binding.doctor_id
                  AND t.deleted_at IS NULL
            ) THEN
                RAISE EXCEPTION 'reviewed source scope mismatch';
            END IF;
            RETURN NEW;
        END $$""")
    op.execute("""CREATE FUNCTION guard_scribe_recording_identity() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            IF NEW.recording_key_sha256 IS DISTINCT FROM OLD.recording_key_sha256 THEN
                RAISE EXCEPTION 'recording admission identity is immutable';
            END IF;
            RETURN NEW;
        END $$""")
    op.execute("CREATE TRIGGER scribe_recording_identity_immutable BEFORE UPDATE ON scribe_capture_admission FOR EACH ROW EXECUTE FUNCTION guard_scribe_recording_identity()")


def downgrade() -> None:
    raise RuntimeError('Disable recording admissions and retain sessions, consent and source history; this expand rung is not destructively reversible.')
