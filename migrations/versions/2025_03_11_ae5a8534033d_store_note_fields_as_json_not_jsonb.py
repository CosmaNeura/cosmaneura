"""store note fields as JSON not JSONB

Revision ID: ae5a8534033d
Revises: e5a43ea65095
Create Date: 2025-03-11 10:16:02.729846

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
import activemodel
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'ae5a8534033d'
down_revision: Union[str, None] = 'e5a43ea65095'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column(
      'doctor_note_schema',
      sa.Column('fields_json', type_=postgresql.JSON(astext_type=sa.Text()), nullable=True)
    )
    op.execute("UPDATE doctor_note_schema SET fields_json = fields::json")
    op.drop_column('doctor_note_schema', 'fields')
    op.alter_column('doctor_note_schema', 'fields_json', new_column_name='fields')
    op.alter_column('doctor_note_schema', 'fields', nullable=False)

def downgrade() -> None:
    # Reverse the migration (JSON back to JSONB)
    op.add_column(
      'doctor_note_schema',
      sa.Column('fields', postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.execute("UPDATE doctor_note_schema SET fields_jsonb = fields::jsonb")
    op.drop_column('doctor_note_schema', 'fields')
    op.alter_column('doctor_note_schema', 'fields_jsonb', new_column_name='fields')
    op.alter_column('doctor_note_schema', 'fields', nullable=False)
