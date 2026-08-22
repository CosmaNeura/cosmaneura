"""merge staging and EHR sandbox heads

Revision ID: 2f6a7b8c9d10
Revises: 8a4e2c7d9f10, 91d2c4e5f6a7
Create Date: 2026-08-18 16:00:00.000000
"""

from typing import Sequence, Union


revision: str = "2f6a7b8c9d10"
down_revision: Union[str, Sequence[str], None] = (
    "8a4e2c7d9f10",
    "91d2c4e5f6a7",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
