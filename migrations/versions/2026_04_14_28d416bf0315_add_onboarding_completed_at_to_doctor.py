"""add_onboarding_completed_at_to_doctor

Revision ID: 28d416bf0315
Revises: f1e2d3c4b5a6
Create Date: 2026-04-14 17:19:50.245202

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '28d416bf0315'
down_revision: Union[str, None] = 'f1e2d3c4b5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('doctor', sa.Column(
        'onboarding_completed_at',
        sa.DateTime(timezone=True),
        nullable=True,
        comment='set to now() when the doctor completes the onboarding wizard; null means onboarding not yet done',
    ))


def downgrade() -> None:
    op.drop_column('doctor', 'onboarding_completed_at')
