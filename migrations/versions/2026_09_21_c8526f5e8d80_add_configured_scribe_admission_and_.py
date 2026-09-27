"""add_configured_scribe_admission_and_reviewed_source

Revision ID: c8526f5e8d80
Revises: 9172abd84a3d
Create Date: 2026-09-21 21:55:32.798414

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
import activemodel
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c8526f5e8d80'
down_revision: Union[str, None] = '9172abd84a3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('scribe_capture_admission', sa.Column('configured_capture',
        postgresql.JSONB(none_as_null=True), nullable=True))
    op.create_check_constraint('ck_scribe_configured_capture_object', 'scribe_capture_admission',
        "configured_capture IS NULL OR jsonb_typeof(configured_capture) = 'object'")
    op.execute("""CREATE FUNCTION guard_scribe_configured_admission() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            IF OLD.configured_capture IS DISTINCT FROM NEW.configured_capture THEN
                RAISE EXCEPTION 'configured admission is immutable';
            END IF;
            RETURN NEW;
        END $$""")
    op.execute("""CREATE TRIGGER scribe_configured_admission_immutable BEFORE UPDATE
        ON scribe_capture_admission FOR EACH ROW EXECUTE FUNCTION guard_scribe_configured_admission()""")
    op.create_table('scribe_capture_reviewed_source',
        sa.Column('id', activemodel.types.typeid.TypeIDType(prefix='scribe_review_source'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('binding_id', activemodel.types.typeid.TypeIDType(prefix='scribe_capture_binding'), nullable=False),
        sa.Column('session_id', sa.Uuid(), nullable=False),
        sa.Column('source_version', sa.Integer(), nullable=False),
        sa.Column('working_transcript_id', sa.Uuid(), nullable=False),
        sa.Column('working_version', sa.Integer(), nullable=False),
        sa.Column('source_sha256', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('payload_sha256', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column('transcript_id', activemodel.types.typeid.TypeIDType(prefix='ai_visit_transcript'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['binding_id'], ['scribe_capture_binding.id']),
        sa.ForeignKeyConstraint(['transcript_id'], ['ai_visit_transcript.id']),
        sa.UniqueConstraint('binding_id', 'working_version', name='uq_scribe_review_working_version'),
        sa.UniqueConstraint('transcript_id', name='uq_scribe_review_core_transcript'),
        sa.CheckConstraint('source_version >= 1 AND working_version >= 1', name='ck_scribe_review_versions'),
        sa.CheckConstraint("source_sha256 ~ '^[a-f0-9]{64}$' AND payload_sha256 ~ '^[a-f0-9]{64}$'",
            name='ck_scribe_review_source_digests'),
    )
    op.execute("""CREATE FUNCTION guard_scribe_reviewed_source() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'reviewed source mapping is immutable';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM scribe_capture_binding b
                JOIN scribe_capture_admission a ON a.binding_id = b.id
                JOIN ai_visit_transcript t ON t.id = NEW.transcript_id
                WHERE b.id = NEW.binding_id AND a.session_id = NEW.session_id
                  AND a.status = 'ready' AND a.configured_capture IS NOT NULL
                  AND t.appointment_id = b.appointment_id AND t.patient_id = b.patient_id
                  AND t.doctor_id = b.doctor_id AND t.deleted_at IS NULL
            ) THEN
                RAISE EXCEPTION 'reviewed source scope mismatch';
            END IF;
            RETURN NEW;
        END $$""")
    op.execute("""CREATE TRIGGER scribe_reviewed_source_scope BEFORE INSERT OR UPDATE OR DELETE
        ON scribe_capture_reviewed_source FOR EACH ROW EXECUTE FUNCTION guard_scribe_reviewed_source()""")


def downgrade() -> None:
    raise RuntimeError('Disable configured capture; retain admission and reviewed-source evidence')
