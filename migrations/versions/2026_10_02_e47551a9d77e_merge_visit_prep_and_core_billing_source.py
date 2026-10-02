"""merge_visit_prep_and_core_billing_source

Revision ID: e47551a9d77e
Revises: b3f91f1acb40, f5efcbb27acd
Create Date: 2026-10-02 14:48:41.689318

"""
# revision identifiers, used by Alembic.
revision: str = 'e47551a9d77e'
down_revision = ('b3f91f1acb40', 'f5efcbb27acd')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    raise RuntimeError('Retain both additive branches; schema recovery is forward-only.')
