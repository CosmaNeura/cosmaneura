"""converge visit note versioning and add restore audit

Revision ID: f70935048976
Revises: e4f012be1c5f
Create Date: 2026-08-23 21:19:13.649896
"""

from typing import Sequence, Union

import activemodel.types.typeid
import sqlalchemy as sa
from alembic import op


revision: str = "f70935048976"
down_revision: Union[str, None] = "e4f012be1c5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Converge both legitimate pre-release database shapes.

    Production reaches this revision after running the revised a81c9d7e4f20
    classifier. Staging had already run the original a81c9d7e4f20 revision and
    therefore arrives with the old full unique constraint and neither version
    column. Conditional DDL makes both paths end with the same schema.
    """

    op.execute(
        """
        ALTER TABLE ai_visit_note
          ADD COLUMN IF NOT EXISTS superseded_at timestamptz,
          ADD COLUMN IF NOT EXISTS supersession_reason varchar(64)
        """
    )
    # Match the installed transcript primary-key storage type. Production uses
    # UUID-backed TypeIDs; deriving the catalog type also keeps this convergence
    # migration correct for independently reconstructed/staging-like schemas.
    op.execute(
        """
        DO $$
        DECLARE
            transcript_id_type text;
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                  FROM information_schema.columns
                 WHERE table_schema = current_schema()
                   AND table_name = 'ai_visit_note'
                   AND column_name = 'restore_frontier_transcript_id'
            ) THEN
                SELECT format_type(attribute.atttypid, attribute.atttypmod)
                  INTO transcript_id_type
                  FROM pg_attribute attribute
                  JOIN pg_class relation
                    ON relation.oid = attribute.attrelid
                  JOIN pg_namespace namespace
                    ON namespace.oid = relation.relnamespace
                 WHERE namespace.nspname = current_schema()
                   AND relation.relname = 'ai_visit_transcript'
                   AND attribute.attname = 'id'
                   AND NOT attribute.attisdropped;
                IF transcript_id_type IS NULL THEN
                    RAISE EXCEPTION
                        'ai_visit_note convergence failed: transcript key type unavailable';
                END IF;
                EXECUTE format(
                    'ALTER TABLE ai_visit_note ADD COLUMN '
                    'restore_frontier_transcript_id %s',
                    transcript_id_type
                );
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                  FROM pg_constraint
                 WHERE conrelid = 'ai_visit_note'::regclass
                   AND conname =
                       'ai_visit_note_restore_frontier_transcript_id_fkey'
            ) THEN
                ALTER TABLE ai_visit_note
                  ADD CONSTRAINT
                    ai_visit_note_restore_frontier_transcript_id_fkey
                  FOREIGN KEY (restore_frontier_transcript_id)
                  REFERENCES ai_visit_transcript(id);
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        COMMENT ON COLUMN ai_visit_note.superseded_at IS
          'When set, this generated version is retained as history, not current output.';
        COMMENT ON COLUMN ai_visit_note.supersession_reason IS
          'Bounded, non-PHI code explaining why this version stopped being current.';
        COMMENT ON COLUMN ai_visit_note.restore_frontier_transcript_id IS
          'Latest active transcript protected by an administrative version restore.';
        COMMENT ON TABLE ai_visit_note IS
          'This represents the note summary for a single specific note schema. A given appointment will have multiple\nAI visit notes.\n\nGenerated versions are retained. Exactly one version per appointment and\nschema is current; prior outputs are marked with supersession metadata and\nremain available for audited administrative recovery.';
        """
    )
    op.execute(
        """
        ALTER TABLE ai_visit_note
          DROP CONSTRAINT IF EXISTS ck_ai_visit_note_supersession_complete
        """
    )
    # ActiveModel's metadata naming convention expands the logical check name
    # used by Alembic on the revised production path. The shorter name covers
    # an independently-created equivalent without assuming either history.
    op.execute(
        """
        ALTER TABLE ai_visit_note
          DROP CONSTRAINT IF EXISTS
            ai_visit_note_ck_ai_visit_note_supersession_complete_check
        """
    )
    op.create_check_constraint(
        "ck_ai_visit_note_supersession_complete",
        "ai_visit_note",
        "(superseded_at IS NULL AND supersession_reason IS NULL) OR "
        "(superseded_at IS NOT NULL AND supersession_reason IS NOT NULL "
        "AND char_length(supersession_reason) BETWEEN 1 AND 64)",
    )

    # Staging's old full constraint is replaced in the same migration
    # transaction. Production does not have it because revised a81c9d7e4f20
    # creates the partial current-row index directly.
    op.execute(
        """
        ALTER TABLE ai_visit_note
          DROP CONSTRAINT IF EXISTS uq_ai_visit_note_appointment_schema
        """
    )
    # Check before asking PostgreSQL to build the unique index. Its native
    # duplicate-key error includes the conflicting key values; this aggregate
    # failure stays operationally useful without placing clinical identifiers
    # in migration logs.
    op.execute(
        """
        DO $$
        DECLARE
            duplicate_current_group_count bigint;
        BEGIN
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
                    'ai_visit_note convergence failed: % current duplicate groups',
                    duplicate_current_group_count;
            END IF;
        END
        $$;
        """
    )
    op.execute(
        "DROP INDEX IF EXISTS uq_ai_visit_note_current_appointment_schema"
    )
    op.create_index(
        "uq_ai_visit_note_current_appointment_schema",
        "ai_visit_note",
        ["appointment_id", "doctor_note_schema_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    op.execute(
        "DROP TRIGGER IF EXISTS ai_visit_note_reject_delete ON ai_visit_note"
    )
    op.execute(
        """
        CREATE TRIGGER ai_visit_note_reject_delete
        BEFORE DELETE ON ai_visit_note
        FOR EACH ROW EXECUTE FUNCTION cosmas_reject_append_only_mutation()
        """
    )
    op.create_table(
        "ai_visit_note_version_audit",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "id",
            activemodel.types.typeid.TypeIDType(
                prefix="visit_note_version_audit"
            ),
            nullable=False,
            comment="TypeID with prefix: visit_note_version_audit",
        ),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("operator_ref", sa.String(length=255), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("transition_sequence", sa.Integer(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "appointment_ref_sha256", sa.String(length=64), nullable=False
        ),
        sa.Column("schema_ref_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "prior_current_note_ref_sha256", sa.String(length=64), nullable=False
        ),
        sa.Column(
            "restored_note_ref_sha256", sa.String(length=64), nullable=False
        ),
        sa.Column(
            "restore_frontier_transcript_ref_sha256",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("plan_sha256", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "action = 'current_note_restored'",
            name="ck_ai_visit_note_version_audit_action",
        ),
        sa.CheckConstraint(
            "reason_code IN ("
            "'operator_selected_prior_version', "
            "'clinical_correction', "
            "'incident_recovery'"
            ")",
            name="ck_ai_visit_note_version_audit_reason_code",
        ),
        sa.CheckConstraint(
            "transition_sequence > 0",
            name="ck_ai_visit_note_version_audit_transition_sequence",
        ),
        sa.CheckConstraint(
            "appointment_ref_sha256 ~ '^[0-9a-f]{64}$' "
            "AND schema_ref_sha256 ~ '^[0-9a-f]{64}$' "
            "AND prior_current_note_ref_sha256 ~ '^[0-9a-f]{64}$' "
            "AND restored_note_ref_sha256 ~ '^[0-9a-f]{64}$' "
            "AND (restore_frontier_transcript_ref_sha256 IS NULL "
            "OR restore_frontier_transcript_ref_sha256 ~ '^[0-9a-f]{64}$') "
            "AND plan_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_ai_visit_note_version_audit_digests",
        ),
        sa.PrimaryKeyConstraint("id", name="ai_visit_note_version_audit_pkey"),
        comment="Pseudonymous evidence for one atomic current-version swap.",
    )
    op.create_index(
        "uq_ai_visit_note_version_audit_plan_sha256",
        "ai_visit_note_version_audit",
        ["plan_sha256"],
        unique=True,
    )
    op.create_index(
        "uq_ai_visit_note_version_audit_family_sequence",
        "ai_visit_note_version_audit",
        [
            "appointment_ref_sha256",
            "schema_ref_sha256",
            "transition_sequence",
        ],
        unique=True,
    )
    op.execute(
        """
        CREATE TRIGGER ai_visit_note_version_audit_reject_mutation
        BEFORE UPDATE OR DELETE ON ai_visit_note_version_audit
        FOR EACH ROW EXECUTE FUNCTION cosmas_reject_append_only_mutation()
        """
    )


def downgrade() -> None:
    # This revision reconciles a migration body that staging had already run.
    # Reversing it would produce a database shape that corresponds to neither
    # source history and could expose retained versions as current. Roll back
    # application code through a reviewed forward migration instead.
    raise RuntimeError(
        "visit note versioning convergence is forward-only; downgrade refused"
    )
