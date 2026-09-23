"""add_scribe_capture_note_edit

Revision ID: f2388a506337
Revises: cbd4ef4c37d5
Create Date: 2026-09-17 10:49:21.074220

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
import activemodel


# revision identifiers, used by Alembic.
revision: str = 'f2388a506337'
down_revision: Union[str, None] = 'cbd4ef4c37d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Generated from the new model against the disposable W8 Core database.
    op.create_table('scribe_capture_note_edit',
    sa.Column('id', activemodel.types.typeid.TypeIDType(prefix='scribe_note_edit'), nullable=False, comment='TypeID with prefix: scribe_note_edit'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('binding_id', activemodel.types.typeid.TypeIDType(prefix='scribe_capture_binding'), nullable=False),
    sa.Column('client_key', sa.Uuid(), nullable=False),
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('base_note_id', activemodel.types.typeid.TypeIDType(prefix='visit_note'), nullable=False),
    sa.Column('result_note_id', activemodel.types.typeid.TypeIDType(prefix='visit_note'), nullable=False),
    sa.Column('request_sha256', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('source_sha256', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('result_sha256', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.CheckConstraint("request_sha256 ~ '^[0-9a-f]{64}$' AND source_sha256 ~ '^[0-9a-f]{64}$' AND result_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('scribe_capture_note_edit_ck_scribe_note_edit_digests_check')),
    sa.ForeignKeyConstraint(['base_note_id'], ['ai_visit_note.id'], name=op.f('scribe_capture_note_edit_base_note_id_fkey')),
    sa.ForeignKeyConstraint(['binding_id'], ['scribe_capture_binding.id'], name=op.f('scribe_capture_note_edit_binding_id_fkey')),
    sa.ForeignKeyConstraint(['result_note_id'], ['ai_visit_note.id'], name=op.f('scribe_capture_note_edit_result_note_id_fkey')),
    sa.PrimaryKeyConstraint('id', name=op.f('scribe_capture_note_edit_pkey')),
    sa.UniqueConstraint('binding_id', 'client_key', name='uq_scribe_note_edit_key'),
    sa.UniqueConstraint('result_note_id', name='uq_scribe_note_edit_result')
    )
    op.execute("""CREATE FUNCTION reject_scribe_note_edit_change() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'scribe note edit history is immutable';
        END $$""")
    op.execute("""CREATE TRIGGER scribe_note_edit_immutable
        BEFORE UPDATE OR DELETE ON scribe_capture_note_edit
        FOR EACH ROW EXECUTE FUNCTION reject_scribe_note_edit_change()""")


def downgrade() -> None:
    raise RuntimeError("Disable capture notes; retained edit history is not destructively downgraded")
