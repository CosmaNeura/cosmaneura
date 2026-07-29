"""add_terminology_to_doctor

Revision ID: b4f9d1c73a20
Revises: 73ecc7333867
Create Date: 2026-07-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b4f9d1c73a20'
down_revision: Union[str, None] = '73ecc7333867'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    sa.Enum('doctor_patient', 'therapist_client', 'counselor_client', 'nutritionist_client', name='terminology').create(op.get_bind())
    op.add_column('doctor', sa.Column('terminology', postgresql.ENUM('doctor_patient', 'therapist_client', 'counselor_client', 'nutritionist_client', name='terminology', create_type=False), nullable=False, server_default='doctor_patient'))


def downgrade() -> None:
    op.drop_column('doctor', 'terminology')
    sa.Enum('doctor_patient', 'therapist_client', 'counselor_client', 'nutritionist_client', name='terminology').drop(op.get_bind())
