"""retain reviewed excerpt citation identities

Revision ID: d4de1ee4bb02
Revises: 3d40e4e90e05
Create Date: 2026-10-30 16:20:20.402692

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d4de1ee4bb02"
down_revision: Union[str, None] = "3d40e4e90e05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


BINDING_CHECK = """reviewed_excerpt_bindings IS NULL OR (
    outcome = 'completed' AND presentation_mode = 'standard' AND NOT suppress_citations
    AND jsonb_typeof(reviewed_excerpt_bindings) = 'object'
    AND reviewed_excerpt_bindings ->> 'schema_version' = '1.0.0'
    AND jsonb_typeof(reviewed_excerpt_bindings -> 'excerpts') = 'array'
    AND jsonb_array_length(reviewed_excerpt_bindings -> 'excerpts') BETWEEN 1 AND 64
    AND octet_length(reviewed_excerpt_bindings::text) <= 65536
) IS TRUE"""


def upgrade() -> None:
    op.add_column(
        "canonical_conversation_final_phase",
        sa.Column("reviewed_excerpt_bindings", postgresql.JSONB(), nullable=True),
    )
    op.create_check_constraint(
        "ck_conversation_reviewed_excerpt_bindings",
        "canonical_conversation_final_phase",
        BINDING_CHECK,
    )


def downgrade() -> None:
    raise RuntimeError(
        "Retain additive citation bindings; roll back the emitter "
        "and leave this expand rung dormant."
    )
