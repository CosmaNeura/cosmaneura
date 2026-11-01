"""add_held_medication_status

Revision ID: 312b217ebba4
Revises: d4de1ee4bb02
Create Date: 2026-11-01 12:35:52.983353

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '312b217ebba4'
down_revision: Union[str, None] = 'd4de1ee4bb02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL enum additions must commit before any transaction uses them.
    # IF NOT EXISTS makes a retry safe after the additive autocommit step.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE medicationstatus ADD VALUE IF NOT EXISTS 'held'")


def downgrade() -> None:
    # Retain the additive value and every patient row. Once held values exist,
    # recovery requires held-capable readers; never rebuild/drop this enum.
    pass
