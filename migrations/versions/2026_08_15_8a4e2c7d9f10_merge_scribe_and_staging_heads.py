"""merge Scribe and staging migration heads

Revision ID: 8a4e2c7d9f10
Revises: 4f7a9c2d1e80, b9b6490ba8d3
Create Date: 2026-08-15 12:00:00.000000
"""

from typing import Sequence, Union


revision: str = "8a4e2c7d9f10"
down_revision: Union[str, Sequence[str], None] = (
    "4f7a9c2d1e80",
    "b9b6490ba8d3",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
