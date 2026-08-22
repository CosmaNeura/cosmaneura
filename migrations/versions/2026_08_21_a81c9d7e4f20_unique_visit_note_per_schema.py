"""enforce one visit note per appointment schema

Revision ID: a81c9d7e4f20
Revises: dc296f605553
Create Date: 2026-08-21 16:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "a81c9d7e4f20"
down_revision: Union[str, None] = "dc296f605553"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Do not silently discard clinical notes.  If historical duplicates exist,
    # stop the rollout so an operator can reconcile them before the invariant is
    # installed.  The error intentionally contains counts only, never row ids or
    # note content.
    op.execute(
        """
        DO $$
        DECLARE
            duplicate_group_count bigint;
        BEGIN
            SELECT COUNT(*)
              INTO duplicate_group_count
              FROM (
                  SELECT 1
                    FROM ai_visit_note
                   GROUP BY appointment_id, doctor_note_schema_id
                  HAVING COUNT(*) > 1
              ) duplicate_groups;

            IF duplicate_group_count > 0 THEN
                RAISE EXCEPTION
                    'ai_visit_note uniqueness preflight failed: % duplicate groups',
                    duplicate_group_count;
            END IF;
        END
        $$;
        """
    )
    op.create_unique_constraint(
        "uq_ai_visit_note_appointment_schema",
        "ai_visit_note",
        ["appointment_id", "doctor_note_schema_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_ai_visit_note_appointment_schema",
        "ai_visit_note",
        type_="unique",
    )
