"""add_capture_note_save_fences

Revision ID: 9172abd84a3d
Revises: 05972a4ee86c
Create Date: 2026-09-21 15:23:53.398407

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
import activemodel


# revision identifiers, used by Alembic.
revision: str = '9172abd84a3d'
down_revision: Union[str, None] = '05972a4ee86c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('scribe_capture_note_save_fence',
        sa.Column('id', activemodel.types.typeid.TypeIDType(prefix='scribe_note_close'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('binding_id', activemodel.types.typeid.TypeIDType(prefix='scribe_capture_binding'), nullable=False),
        sa.Column('client_key', sa.Uuid(), nullable=False),
        sa.Column('session_id', sa.Uuid(), nullable=False),
        sa.Column('base_note_id', activemodel.types.typeid.TypeIDType(prefix='visit_note'), nullable=False),
        sa.Column('request_sha256', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.CheckConstraint("request_sha256 ~ '^[0-9a-f]{64}$'", name='ck_scribe_note_close_digest'),
        sa.ForeignKeyConstraint(['base_note_id'], ['ai_visit_note.id']),
        sa.ForeignKeyConstraint(['binding_id'], ['scribe_capture_binding.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('binding_id', 'client_key', name='uq_scribe_note_close_key'),
    )
    op.execute("""CREATE FUNCTION guard_scribe_note_save_outcome() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            PERFORM pg_advisory_xact_lock(hashtextextended(NEW.binding_id::text || ':' || NEW.client_key::text, 0));
            IF TG_TABLE_NAME = 'scribe_capture_note_edit' THEN
                IF EXISTS (SELECT 1 FROM scribe_capture_note_save_fence
                    WHERE binding_id = NEW.binding_id AND client_key = NEW.client_key) THEN
                    RAISE EXCEPTION 'scribe note save request is closed';
                END IF;
            ELSE
                IF EXISTS (SELECT 1 FROM scribe_capture_note_edit
                    WHERE binding_id = NEW.binding_id AND client_key = NEW.client_key) THEN
                    RAISE EXCEPTION 'scribe note save already recorded';
                END IF;
            END IF;
            RETURN NEW;
        END $$""")
    for table in ('scribe_capture_note_edit', 'scribe_capture_note_save_fence'):
        op.execute(f"""CREATE TRIGGER scribe_note_save_exclusive
            BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION guard_scribe_note_save_outcome()""")
    op.execute("""CREATE TRIGGER scribe_note_close_immutable
        BEFORE UPDATE OR DELETE ON scribe_capture_note_save_fence
        FOR EACH ROW EXECUTE FUNCTION reject_scribe_note_edit_change()""")


def downgrade() -> None:
    raise RuntimeError('Disable capture notes; retained save fences must continue preventing late publication')
