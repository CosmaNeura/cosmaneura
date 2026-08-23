"""merge practice entitlement and v7 authority heads

The practice entitlement/BAA chain (bfd0ced59a81, from master) and the
staging-only v7 authority audit chain (d4e5f6a7b8c9) both descend from
91d2c4e5f6a7; this no-op merge gives staging a single head again.

Revision ID: c380a7cd8feb
Revises: d4e5f6a7b8c9, bfd0ced59a81
Create Date: 2026-08-20 18:21:33.804036
"""

from typing import Sequence, Union

revision: str = "c380a7cd8feb"
down_revision: Union[str, Sequence[str], None] = (
    "d4e5f6a7b8c9",
    "bfd0ced59a81",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
