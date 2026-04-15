"""add_comments_to_doctor_video_integration

Revision ID: a1b2c3d4e5f6
Revises: 28d416bf0315
Create Date: 2026-04-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '28d416bf0315'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('doctor_video_integration', 'access_token',
               existing_type=sa.Text(),
               comment='OAuth access token — stored encrypted at rest (AES-256/Fernet)',
               existing_nullable=False)
    op.alter_column('doctor_video_integration', 'refresh_token',
               existing_type=sa.Text(),
               comment='OAuth refresh token — stored encrypted at rest (AES-256/Fernet)',
               existing_nullable=False)
    op.alter_column('doctor_video_integration', 'token_expires_at',
               existing_type=sa.DateTime(timezone=True),
               comment='When the current access_token expires',
               existing_nullable=False)


def downgrade() -> None:
    op.alter_column('doctor_video_integration', 'token_expires_at',
               existing_type=sa.DateTime(timezone=True),
               comment=None,
               existing_comment='When the current access_token expires',
               existing_nullable=False)
    op.alter_column('doctor_video_integration', 'refresh_token',
               existing_type=sa.Text(),
               comment=None,
               existing_comment='OAuth refresh token — stored encrypted at rest (AES-256/Fernet)',
               existing_nullable=False)
    op.alter_column('doctor_video_integration', 'access_token',
               existing_type=sa.Text(),
               comment=None,
               existing_comment='OAuth access token — stored encrypted at rest (AES-256/Fernet)',
               existing_nullable=False)
