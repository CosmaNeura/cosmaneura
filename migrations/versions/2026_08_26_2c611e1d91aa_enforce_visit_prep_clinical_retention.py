"""Enforce the exact Visit Prep clinical.v1 retention snapshot.

Revision ID: 2c611e1d91aa
Revises: bc4f96a3a5cc
Create Date: 2026-08-26 03:43:03.182447

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "2c611e1d91aa"
down_revision: Union[str, None] = "bc4f96a3a5cc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _refuse_downgrade() -> None:
    """Keep this additive retention control in place for forward recovery."""

    raise RuntimeError(
        "Visit Prep clinical-retention downgrade is unsupported; forward-fix only"
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION public.cosmas_enforce_visit_prep_clinical_retention()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        DECLARE
          current_scheduled_at timestamptz;
        BEGIN
          -- clinical.v1 is an immutable snapshot of the schedule current at
          -- first confirmation. A later accepted reschedule neither shortens
          -- nor silently rewrites that already-applied retention decision.
          IF OLD.confirmed_at IS NOT NULL THEN
            IF ROW(NEW.retention_policy_version, NEW.retain_until)
                 IS DISTINCT FROM ROW(
                   OLD.retention_policy_version,
                   OLD.retain_until
                 ) THEN
              RAISE EXCEPTION 'Visit Prep retention snapshot is immutable'
                USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
          END IF;

          IF NEW.confirmed_at IS NULL THEN
            RETURN NEW;
          END IF;

          IF NEW.retention_policy_version IS DISTINCT FROM 'clinical.v1'
             OR NEW.retain_until IS NULL THEN
            RAISE EXCEPTION 'confirmed Visit Prep requires clinical.v1 retention'
              USING ERRCODE = '23514';
          END IF;

          SELECT scheduled_at
            INTO current_scheduled_at
            FROM public.visit_prep_schedule_generation
           WHERE visit_prep_id = NEW.id
             AND owner_practice_member_id = NEW.owner_practice_member_id
           ORDER BY generation DESC
           LIMIT 1
           FOR KEY SHARE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'confirmed Visit Prep requires a current schedule'
              USING ERRCODE = '23514';
          END IF;

          IF NEW.retain_until IS DISTINCT FROM (
               ((current_scheduled_at AT TIME ZONE 'UTC') + interval '7 years')
               AT TIME ZONE 'UTC'
             ) THEN
            RAISE EXCEPTION 'clinical.v1 retention must equal seven calendar years'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER visit_prep_enforce_clinical_retention
        BEFORE UPDATE ON public.visit_prep
        FOR EACH ROW
        EXECUTE FUNCTION public.cosmas_enforce_visit_prep_clinical_retention()
        """
    )
    # The feature is still dark. Refuse any unexpected historical confirmation
    # rather than guessing which immutable schedule generation was current at
    # confirmation from transaction timestamps.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
              FROM public.visit_prep AS prep
             WHERE prep.confirmed_at IS NOT NULL
          ) THEN
            RAISE EXCEPTION
              'unexpected confirmed Visit Prep requires explicit migration review'
              USING ERRCODE = '23514';
          END IF;
        END;
        $$
        """
    )


def downgrade() -> None:
    _refuse_downgrade()
