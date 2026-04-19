"""remove_zoom_oauth_token_columns

Revision ID: b2b524604b33
Revises: 3766645b25a6
Create Date: 2026-04-13 16:47:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b2b524604b33'
down_revision: Union[str, None] = '3766645b25a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Delete all existing Zoom integration rows so doctors reconnect via the new flow.
    op.execute("DELETE FROM doctor_video_integration WHERE provider = 'zoom'")

    # Drop the per-user OAuth token columns — no longer needed with S2S OAuth.
    op.drop_column('doctor_video_integration', 'access_token')
    op.drop_column('doctor_video_integration', 'refresh_token')
    op.drop_column('doctor_video_integration', 'token_expires_at')


def downgrade() -> None:
    # NOTE: columns are restored as nullable because upgrade() deleted all token data.
    # A full data restore would be required before tightening these back to NOT NULL.
    op.add_column('doctor_video_integration',
        sa.Column('token_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('doctor_video_integration',
        sa.Column('refresh_token', sa.String(), nullable=True))
    op.add_column('doctor_video_integration',
        sa.Column('access_token', sa.String(), nullable=True))
