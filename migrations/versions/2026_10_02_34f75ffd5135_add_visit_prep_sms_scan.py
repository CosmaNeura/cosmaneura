"""add_visit_prep_sms_scan

Revision ID: 34f75ffd5135
Revises: 898301cffd5c
Create Date: 2026-10-02 23:47:19.243705

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = '34f75ffd5135'
down_revision: Union[str, None] = '898301cffd5c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Generated on owned PostgreSQL; unrelated existing comment drift removed.
    op.create_table('visit_prep_sms_scan',
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
    sa.Column('after_id', sa.Uuid(), nullable=True),
    sa.Column('version', sa.BigInteger(), nullable=False),
    sa.Column('checked_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("name='visit_prep_sms_v1'", name=op.f('visit_prep_sms_scan_name_shape_check')),
    sa.CheckConstraint('version BETWEEN 0 AND 9223372036854775807', name=op.f('visit_prep_sms_scan_version_shape_check')),
    sa.PrimaryKeyConstraint('name', name=op.f('visit_prep_sms_scan_pkey'))
    )


def downgrade() -> None:
    raise RuntimeError('Retain dormant scheduler checkpoint; use a forward fix')
