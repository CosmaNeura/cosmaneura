"""Serialize capture source guards with binding revocation and gap publication.

Revision ID: 71d6e1ac9b04
Revises: 56f2b01ac226
"""

from alembic import op

revision = "71d6e1ac9b04"
down_revision = "56f2b01ac226"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
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
            FROM public.scribe_capture_admission WHERE binding_id=NEW.binding_id FOR UPDATE;
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
            WHERE binding_id=NEW.binding_id;
            IF FOUND AND (NEW.session_id IS DISTINCT FROM manifest.session_id
                OR NEW.raw_source_id IS DISTINCT FROM manifest.raw_source_id
                OR NEW.raw_source_sha256 IS DISTINCT FROM manifest.raw_source_sha256
                OR NEW.raw_core_transcript_id IS DISTINCT FROM manifest.raw_core_transcript_id) THEN
                RAISE EXCEPTION 'working source gap origin mismatch';
            END IF;
            RETURN NEW;
        END $$""")


def downgrade() -> None:
    raise RuntimeError("Retain capture source serialization and revocation guards")
