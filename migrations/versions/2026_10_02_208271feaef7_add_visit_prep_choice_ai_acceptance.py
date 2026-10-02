"""add_visit_prep_choice_ai_acceptance

Revision ID: 208271feaef7
Revises: dd93e7a689f1
Create Date: 2026-10-02 17:02:03.992399

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "208271feaef7"
down_revision = "dd93e7a689f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Alembic generated this revision against the owned synthetic database.
    # No columns change. Discard unrelated Stripe/Scribe comment drift; expand
    # only the existing append-only decision guard, retaining every old check.
    op.execute("""
        DO $migration$
        DECLARE definition text; old_check text; new_check text;
        BEGIN
          definition := pg_get_functiondef('public.cosmas_guard_vp_clarification()'::regprocedure);
          old_check := $old$IF NOT FOUND OR correction.event_kind <> 'correction'$old$;
          new_check := $new$IF NOT FOUND OR correction.event_kind NOT IN ('correction', 'explanation')$new$;
          IF strpos(definition, old_check) = 0 THEN
            RAISE EXCEPTION 'Visit Prep decision guard differs from expected predecessor';
          END IF;
          definition := replace(definition, old_check, new_check);
          old_check := $old$-- Choice supplemental words need a separate source contract.
              PERFORM 1 FROM public.visit_prep_question
               WHERE id = proposal.question_id AND kind IN ('story', 'list');
              IF NOT FOUND THEN
                RAISE EXCEPTION 'Visit Prep clarification choice supplementation is unavailable'
                  USING ERRCODE = '55000';
              END IF;$old$;
          new_check := $new$-- Choice acceptance appends an explanation, never a code correction.
              PERFORM 1 FROM public.visit_prep_question
               WHERE id = proposal.question_id AND (
                 (kind IN ('story', 'list') AND correction.event_kind = 'correction')
                 OR (kind = 'choice' AND correction.event_kind = 'explanation'
                   AND correction.question_id = proposal.question_id
                   AND correction.target_revision_id IS NOT DISTINCT FROM proposal.source_contribution_id
                   AND (proposal.source_response_id IS NULL
                     OR correction.response_id = proposal.source_response_id))
               );
              IF NOT FOUND THEN
                RAISE EXCEPTION 'Visit Prep clarification accepted source kind or revision differs'
                  USING ERRCODE = '55000';
              END IF;$new$;
          IF strpos(definition, old_check) = 0 THEN
            RAISE EXCEPTION 'Visit Prep decision guard differs from expected predecessor';
          END IF;
          definition := replace(definition, old_check, new_check);
          EXECUTE definition;
        END;
        $migration$;
    """)


def downgrade() -> None:
    raise RuntimeError("Visit Prep accepted explanations require a forward fix; retain evidence")
