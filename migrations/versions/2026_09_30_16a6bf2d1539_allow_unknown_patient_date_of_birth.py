"""allow_unknown_patient_date_of_birth

Revision ID: 16a6bf2d1539
Revises: de0c6b95f69f
Create Date: 2026-09-30 18:12:18.638472

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '16a6bf2d1539'
down_revision: Union[str, None] = 'de0c6b95f69f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("patient", "date_of_birth", existing_type=sa.DateTime(), nullable=True)


def downgrade() -> None:
    raise RuntimeError(
        "Retain nullable patient birth dates: restore compatible readers and disable "
        "new optional-date entry instead of discarding unknown dates."
    )
