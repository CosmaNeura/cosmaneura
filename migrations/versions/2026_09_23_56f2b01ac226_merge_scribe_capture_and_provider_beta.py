"""Merge Scribe capture and provider beta

Revision ID: 56f2b01ac226
Revises: c8526f5e8d80, de9260834611
Create Date: 2026-09-23 19:05:04.296927

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
import activemodel


# revision identifiers, used by Alembic.
revision: str = '56f2b01ac226'
down_revision: Union[str, None] = ('c8526f5e8d80', 'de9260834611')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
