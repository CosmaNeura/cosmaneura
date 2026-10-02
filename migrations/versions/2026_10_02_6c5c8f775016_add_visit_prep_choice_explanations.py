"""Expand append-only contribution evidence with exact-revision explanations.

Revision ID: 6c5c8f775016
Revises: 65dee3dec2d0
Create Date: 2026-10-02 12:39:26.084591
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import activemodel

revision: str = "6c5c8f775016"
down_revision: Union[str, None] = "65dee3dec2d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Generated with owned Alembic autogeneration; unrelated comment drift
    # excluded. Existing rows satisfy the expanded shapes.
    op.add_column(
        "visit_prep_contribution_event",
        sa.Column(
            "target_revision_id",
            activemodel.types.typeid.TypeIDType(prefix="visit_prep_contribution"),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_visit_prep_explanation_revision",
        "visit_prep_contribution_event",
        "visit_prep_contribution_event",
        ["target_revision_id"],
        ["id"],
        ondelete="NO ACTION",
        initially="DEFERRED",
        deferrable=True,
    )
    op.drop_constraint(
        op.f("visit_prep_contribution_event_event_kind_allowed_check"),
        "visit_prep_contribution_event",
        type_="check",
    )
    op.create_check_constraint(
        op.f("visit_prep_contribution_event_event_kind_allowed_check"),
        "visit_prep_contribution_event",
        "event_kind IN ('correction', 'review', 'topic_answer', 'explanation')",
    )
    op.drop_constraint(
        op.f("visit_prep_contribution_event_event_shape_check"),
        "visit_prep_contribution_event",
        type_="check",
    )
    op.create_check_constraint(
        op.f("visit_prep_contribution_event_event_shape_check"),
        "visit_prep_contribution_event",
        "(event_kind = 'review' AND response_id IS NULL AND question_id IS NULL "
        "AND response_kind IS NULL AND answer_ciphertext IS NULL "
        "AND answer_format_version IS NULL AND target_revision_id IS NULL) OR "
        "(event_kind IN ('correction', 'topic_answer') AND target_revision_id IS NULL AND "
        "((event_kind = 'correction' AND response_id IS NOT NULL "
        "AND question_id IS NULL) OR (event_kind = 'topic_answer' "
        "AND response_id IS NULL AND question_id IS NOT NULL)) "
        "AND response_kind IS NOT NULL AND "
        "((response_kind = 'answered' AND answer_ciphertext IS NOT NULL "
        "AND answer_format_version IS NOT DISTINCT FROM 1 "
        "AND octet_length(answer_ciphertext) BETWEEN 1 AND 32768) OR "
        "(response_kind = 'skipped' AND answer_ciphertext IS NULL "
        "AND answer_format_version IS NULL))) OR "
        "(event_kind = 'explanation' AND question_id IS NOT NULL AND "
        "response_kind IS NOT NULL AND "
        "((response_kind = 'answered' AND answer_ciphertext IS NOT NULL "
        "AND answer_format_version IS NOT DISTINCT FROM 1 "
        "AND octet_length(answer_ciphertext) BETWEEN 1 AND 32768) OR "
        "(response_kind = 'skipped' AND answer_ciphertext IS NULL "
        "AND answer_format_version IS NULL)))",
    )
    # Existing mutation trigger runs first: root/credential/time/role/basis and
    # retention guards are unchanged. This trigger proves exact source lineage.
    op.execute("""
        CREATE FUNCTION public.cosmas_guard_visit_prep_explanation()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        DECLARE original_id uuid; latest_id uuid; latest_kind text;
        BEGIN
          IF NEW.event_kind <> 'explanation' THEN RETURN NEW; END IF;
          PERFORM 1 FROM public.visit_prep_question
           WHERE id = NEW.question_id AND kind = 'choice'
             AND question_set_version_id = NEW.question_set_version_id;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'Visit Prep explanation requires a coded topic'
              USING ERRCODE = '23503';
          END IF;
          SELECT id INTO original_id
            FROM public.visit_prep_question_response
           WHERE visit_prep_id = NEW.visit_prep_id AND question_id = NEW.question_id;
          IF NEW.response_id IS DISTINCT FROM original_id THEN
            RAISE EXCEPTION 'Visit Prep explanation original source changed'
              USING ERRCODE = '23503';
          END IF;
          SELECT e.id, e.response_kind INTO latest_id, latest_kind
            FROM public.visit_prep_contribution_event e
           WHERE e.visit_prep_id = NEW.visit_prep_id
             AND ((e.event_kind = 'correction' AND e.response_id = original_id)
               OR (e.event_kind = 'topic_answer' AND e.question_id = NEW.question_id))
           ORDER BY e.event_sequence DESC LIMIT 1;
          IF latest_id IS NULL THEN
            SELECT response_kind INTO latest_kind
              FROM public.visit_prep_question_response WHERE id = original_id;
          END IF;
          IF NEW.target_revision_id IS DISTINCT FROM latest_id
             OR latest_kind IS DISTINCT FROM 'answered' THEN
            RAISE EXCEPTION 'Visit Prep explanation target revision changed'
              USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER visit_prep_contribution_guard_supplement
        BEFORE INSERT ON public.visit_prep_contribution_event
        FOR EACH ROW EXECUTE FUNCTION public.cosmas_guard_visit_prep_explanation()
    """)


def downgrade() -> None:
    raise RuntimeError(
        "Visit Prep explanations downgrade is unsupported; use a forward fix"
    )
