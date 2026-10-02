"""Allow retained completed Visit Prep to follow its appointment.

Revision ID: 3a7854eb39e0
Revises: ac4d76856637
Create Date: 2026-10-02 19:56:43.406772
"""

from alembic import op

revision = "3a7854eb39e0"
down_revision = "ac4d76856637"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Generated in owned PostgreSQL. Remove unrelated autogenerate comment drift;
    # amend only the exact completed-state clauses, retaining all other guards.
    for function, replacement in (
        (
            "cosmas_guard_visit_prep_schedule",
            """OR root_state = 'misdirected'
             OR (root_state = 'completed' AND (
                 NEW.generation <= 1 OR NEW.scheduled_at <= clock_timestamp()
                 OR NOT EXISTS (SELECT 1 FROM public.visit_prep p
                     WHERE p.id = NEW.visit_prep_id
                       AND p.retain_until > clock_timestamp()
                       AND NEW.scheduled_at < p.retain_until)))""",
        ),
        (
            "cosmas_guard_visit_prep_credential",
            """OR root_state = 'misdirected'
             OR (root_state = 'completed' AND (
                 NEW.generation <= 1
                 OR NOT EXISTS (SELECT 1 FROM public.visit_prep p
                     JOIN public.visit_prep_schedule_generation s
                       ON s.id = NEW.schedule_generation_id
                     WHERE p.id = NEW.visit_prep_id
                       AND p.retain_until > clock_timestamp()
                       AND s.scheduled_at > clock_timestamp()
                       AND s.scheduled_at < p.retain_until)
                 OR NOT EXISTS (SELECT 1 FROM public.visit_prep_credential_generation c
                     WHERE c.visit_prep_id = NEW.visit_prep_id
                       AND c.generation = NEW.generation - 1
                       AND c.schedule_generation_id <> NEW.schedule_generation_id
                       AND c.revoked_at IS NOT NULL
                       AND c.revocation_reason = 'rescheduled')))""",
        ),
    ):
        op.execute(
            f"""
            DO $amend$
            DECLARE definition text;
              old_clause text := 'OR root_state IN (''completed'', ''misdirected'')';
            BEGIN
              SELECT pg_get_functiondef('public.{function}()'::regprocedure)
                INTO definition;
              IF (length(definition) - length(replace(definition, old_clause, '')))
                   / length(old_clause) <> 1 THEN
                RAISE EXCEPTION 'Unexpected Visit Prep lifecycle guard; use a reviewed forward fix';
              END IF;
              EXECUTE replace(definition, old_clause, '{replacement.replace(chr(39), chr(39) * 2)}');
            END; $amend$;
            """
        )


def downgrade() -> None:
    raise RuntimeError("Retain completed Visit Prep lineage; use a forward fix")
