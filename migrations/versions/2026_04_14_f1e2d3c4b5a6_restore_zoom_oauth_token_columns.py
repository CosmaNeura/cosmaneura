"""restore_zoom_oauth_token_columns

Revision ID: f1e2d3c4b5a6
Revises: b2b524604b33
Create Date: 2026-04-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f1e2d3c4b5a6'
down_revision: Union[str, None] = 'b2b524604b33'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Delete existing zoom rows — doctors will reconnect via Authorization Code Grant.
    op.execute("DELETE FROM doctor_video_integration WHERE provider = 'zoom'")
    # Add per-doctor OAuth token columns (table is now empty so NOT NULL is safe).
    op.add_column('doctor_video_integration',
        sa.Column('access_token', sa.Text(), nullable=False))
    op.add_column('doctor_video_integration',
        sa.Column('refresh_token', sa.Text(), nullable=False))
    op.add_column('doctor_video_integration',
        sa.Column('token_expires_at', sa.DateTime(timezone=True), nullable=False))


def downgrade() -> None:
    op.drop_column('doctor_video_integration', 'token_expires_at')
    op.drop_column('doctor_video_integration', 'refresh_token')
    op.drop_column('doctor_video_integration', 'access_token')
