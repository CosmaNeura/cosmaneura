"""retain note versions and enforce one current note per appointment schema

Revision ID: a81c9d7e4f20
Revises: dc296f605553
Create Date: 2026-08-21 16:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a81c9d7e4f20"
down_revision: Union[str, None] = "dc296f605553"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Classify historical versions without deleting or rewriting a note.

    Production has not applied this revision. Staging applied the earlier,
    fail-closed form; a later generated convergence revision brings that
    already-applied shape to the same final schema.
    """

    op.add_column(
        "ai_visit_note",
        sa.Column(
            "superseded_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "When set, this generated version is retained as history, "
                "not current output."
            ),
        ),
    )
    op.add_column(
        "ai_visit_note",
        sa.Column(
            "supersession_reason",
            sa.String(length=64),
            nullable=True,
            comment=(
                "Bounded, non-PHI code explaining why this version stopped "
                "being current."
            ),
        ),
    )

    # Preserve an exact, transaction-local copy of every pre-existing column.
    # JSON is compared as text because PostgreSQL's json type has no equality
    # operator and its key order is clinically significant to this application.
    op.execute(
        """
        CREATE TEMPORARY TABLE ai_visit_note_before_a81c9d7e4f20
        ON COMMIT DROP AS
        SELECT id,
               created_at,
               updated_at,
               doctor_id,
               appointment_id,
               ai_transcript_id,
               doctor_note_schema_id,
               note::text AS note_text
          FROM ai_visit_note
        """
    )

    # A duplicate group may be classified automatically only when every
    # clinical ownership edge agrees. The exception exposes a count, never an
    # identifier or note body.
    op.execute(
        """
        DO $$
        DECLARE
            invalid_scope_count bigint;
            stale_winner_count bigint;
        BEGIN
            WITH duplicate_keys AS (
                SELECT appointment_id, doctor_note_schema_id
                  FROM ai_visit_note
                 GROUP BY appointment_id, doctor_note_schema_id
                HAVING COUNT(*) > 1
            )
            SELECT COUNT(*)
              INTO invalid_scope_count
              FROM ai_visit_note note
              JOIN duplicate_keys duplicate_key
                USING (appointment_id, doctor_note_schema_id)
              LEFT JOIN appointment appointment
                ON appointment.id = note.appointment_id
              LEFT JOIN patient patient
                ON patient.id = appointment.patient_id
              LEFT JOIN doctor_note_schema schema
                ON schema.id = note.doctor_note_schema_id
              LEFT JOIN ai_visit_transcript transcript
                ON transcript.id = note.ai_transcript_id
             WHERE appointment.id IS NULL
                OR patient.id IS NULL
                OR schema.id IS NULL
                OR transcript.id IS NULL
                OR patient.doctor_id IS DISTINCT FROM appointment.doctor_id
                OR note.doctor_id IS DISTINCT FROM appointment.doctor_id
                OR note.doctor_id IS DISTINCT FROM schema.doctor_id
                OR transcript.doctor_id IS DISTINCT FROM note.doctor_id
                OR transcript.appointment_id IS DISTINCT FROM note.appointment_id
                OR transcript.patient_id IS DISTINCT FROM appointment.patient_id;

            IF invalid_scope_count > 0 THEN
                RAISE EXCEPTION
                    'ai_visit_note version preflight failed: % ownership mismatches',
                    invalid_scope_count;
            END IF;

            WITH duplicate_keys AS (
                SELECT appointment_id, doctor_note_schema_id
                  FROM ai_visit_note
                 GROUP BY appointment_id, doctor_note_schema_id
                HAVING COUNT(*) > 1
            ),
            newest_notes AS (
                SELECT DISTINCT ON (note.appointment_id, note.doctor_note_schema_id)
                       note.appointment_id,
                       note.doctor_note_schema_id,
                       note.ai_transcript_id
                  FROM ai_visit_note note
                  JOIN duplicate_keys duplicate_key
                    USING (appointment_id, doctor_note_schema_id)
                 ORDER BY note.appointment_id,
                          note.doctor_note_schema_id,
                          note.created_at DESC,
                          note.id DESC
            ),
            latest_transcripts AS (
                SELECT DISTINCT ON (transcript.appointment_id)
                       transcript.appointment_id,
                       transcript.id
                  FROM ai_visit_transcript transcript
                  JOIN duplicate_keys duplicate_key
                    ON duplicate_key.appointment_id = transcript.appointment_id
                 ORDER BY transcript.appointment_id,
                          transcript.created_at DESC,
                          transcript.id DESC
            )
            SELECT COUNT(*)
              INTO stale_winner_count
              FROM newest_notes newest
              JOIN latest_transcripts latest
                ON latest.appointment_id = newest.appointment_id
             WHERE newest.ai_transcript_id IS DISTINCT FROM latest.id;

            IF stale_winner_count > 0 THEN
                RAISE EXCEPTION
                    'ai_visit_note version preflight failed: % newest notes do not reference the latest transcript',
                    stale_winner_count;
            END IF;
        END
        $$;
        """
    )

    # The generated-at timestamp and UUID make the winner deterministic. No
    # content column or pre-existing timestamp is touched.
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY appointment_id, doctor_note_schema_id
                       ORDER BY created_at DESC, id DESC
                   ) AS version_rank
              FROM ai_visit_note
        )
        UPDATE ai_visit_note note
           SET superseded_at = CURRENT_TIMESTAMP,
               supersession_reason = 'migration:a81c9d7e4f20'
          FROM ranked
         WHERE ranked.id = note.id
           AND ranked.version_rank > 1
        """
    )

    # Fail closed if classification changed an old column, changed the row
    # count, or left more than one current row in a logical note slot.
    op.execute(
        """
        DO $$
        DECLARE
            changed_row_count bigint;
            duplicate_current_group_count bigint;
        BEGIN
            SELECT COUNT(*)
              INTO changed_row_count
              FROM ai_visit_note_before_a81c9d7e4f20 before
              FULL OUTER JOIN ai_visit_note after USING (id)
             WHERE before.id IS NULL
                OR after.id IS NULL
                OR before.created_at IS DISTINCT FROM after.created_at
                OR before.updated_at IS DISTINCT FROM after.updated_at
                OR before.doctor_id IS DISTINCT FROM after.doctor_id
                OR before.appointment_id IS DISTINCT FROM after.appointment_id
                OR before.ai_transcript_id IS DISTINCT FROM after.ai_transcript_id
                OR before.doctor_note_schema_id
                   IS DISTINCT FROM after.doctor_note_schema_id
                OR before.note_text IS DISTINCT FROM after.note::text;

            IF changed_row_count > 0 THEN
                RAISE EXCEPTION
                    'ai_visit_note preservation check failed: % rows changed',
                    changed_row_count;
            END IF;

            SELECT COUNT(*)
              INTO duplicate_current_group_count
              FROM (
                  SELECT 1
                    FROM ai_visit_note
                   WHERE superseded_at IS NULL
                   GROUP BY appointment_id, doctor_note_schema_id
                  HAVING COUNT(*) > 1
              ) duplicate_groups;

            IF duplicate_current_group_count > 0 THEN
                RAISE EXCEPTION
                    'ai_visit_note current-version check failed: % duplicate groups',
                    duplicate_current_group_count;
            END IF;
        END
        $$;
        """
    )

    op.create_check_constraint(
        "ck_ai_visit_note_supersession_complete",
        "ai_visit_note",
        "(superseded_at IS NULL AND supersession_reason IS NULL) OR "
        "(superseded_at IS NOT NULL AND supersession_reason IS NOT NULL "
        "AND char_length(supersession_reason) BETWEEN 1 AND 64)",
    )
    op.create_index(
        "uq_ai_visit_note_current_appointment_schema",
        "ai_visit_note",
        ["appointment_id", "doctor_note_schema_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    # The prior application image hard-deleted a generated note immediately
    # before inserting its replacement. It can remain live briefly while this
    # migration commits, so reject deletes at the database boundary. The new
    # writer supersedes in place and never needs DELETE.
    op.execute(
        """
        CREATE TRIGGER ai_visit_note_reject_delete
        BEFORE DELETE ON ai_visit_note
        FOR EACH ROW EXECUTE FUNCTION cosmas_reject_append_only_mutation()
        """
    )


def downgrade() -> None:
    # Removing classification would make retained historical outputs current
    # again. A clean database can downgrade; one with history must use the
    # reviewed restore/rollback procedure instead.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM ai_visit_note WHERE superseded_at IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'visit note versioning contains retained history; downgrade refused';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS ai_visit_note_reject_delete ON ai_visit_note"
    )
    op.drop_index(
        "uq_ai_visit_note_current_appointment_schema",
        table_name="ai_visit_note",
    )
    op.drop_constraint(
        "ck_ai_visit_note_supersession_complete",
        "ai_visit_note",
        type_="check",
    )
    op.drop_column("ai_visit_note", "supersession_reason")
    op.drop_column("ai_visit_note", "superseded_at")
