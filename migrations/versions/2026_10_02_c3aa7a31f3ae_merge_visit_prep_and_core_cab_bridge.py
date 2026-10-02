"""merge_visit_prep_and_core_cab_bridge

Revision ID: c3aa7a31f3ae
Revises: e47551a9d77e, 211aefdf709b
Create Date: 2026-10-02 14:56:46.183699

"""
# revision identifiers, used by Alembic.
revision: str = 'c3aa7a31f3ae'
down_revision = ('e47551a9d77e', '211aefdf709b')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise RuntimeError('Retain both additive branches; schema recovery is forward-only.')
