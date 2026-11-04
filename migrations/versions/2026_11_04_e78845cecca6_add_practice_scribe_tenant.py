"""Expand stable Core-owned Scribe practice tenancy.

Revision ID generated with secrets.token_hex(6). Recovery leaves the structure
dormant; no existing authority or provider link is rewritten.
"""

import sqlalchemy as sa
from activemodel.types import TypeIDType
from alembic import op

revision = "e78845cecca6"
down_revision = "ddd6261fa36f"
branch_labels = None
depends_on = None


def upgrade():
    # The same aggregate-only guard runs online and in generated rollout SQL.
    op.execute("""
        DO $$
        DECLARE practice_conflicts bigint; reused_uuids bigint; zero_uuids bigint;
        BEGIN
            WITH active AS (
                SELECT practice_id, scribe_tenant_id
                FROM scribe_capture_provider_link WHERE revoked_at IS NULL
            )
            SELECT
                (SELECT count(*) FROM (
                    SELECT practice_id FROM active GROUP BY practice_id
                    HAVING count(DISTINCT scribe_tenant_id) <> 1
                ) p),
                (SELECT count(*) FROM (
                    SELECT scribe_tenant_id FROM active GROUP BY scribe_tenant_id
                    HAVING count(DISTINCT practice_id) <> 1
                ) t),
                (SELECT count(*) FROM active
                 WHERE scribe_tenant_id = '00000000-0000-0000-0000-000000000000')
            INTO practice_conflicts, reused_uuids, zero_uuids;
            IF practice_conflicts + reused_uuids + zero_uuids <> 0 THEN
                RAISE EXCEPTION 'scribe_tenant_backfill_conflict: practice_conflicts=%, reused_uuids=%, zero_uuids=%',
                    practice_conflicts, reused_uuids, zero_uuids USING ERRCODE = '23514';
            END IF;
        END $$;
    """)
    op.create_table(
        "practice_scribe_tenant",
        sa.Column("id", TypeIDType("practice_scribe_tenant"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("practice_id", TypeIDType("practice"), nullable=False),
        sa.Column("scribe_tenant_id", sa.Uuid, nullable=False),
        sa.ForeignKeyConstraint(["practice_id"], ["practice.id"]),
        sa.UniqueConstraint("practice_id"),
        sa.UniqueConstraint("scribe_tenant_id"),
        sa.CheckConstraint(
            "scribe_tenant_id <> '00000000-0000-0000-0000-000000000000'",
            name="ck_practice_scribe_tenant_nonzero",
        ),
    )
    op.execute("""
        INSERT INTO practice_scribe_tenant
            (id, created_at, updated_at, practice_id, scribe_tenant_id)
        SELECT gen_random_uuid(), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
               practice_id, min(scribe_tenant_id::text)::uuid
        FROM scribe_capture_provider_link WHERE revoked_at IS NULL
        GROUP BY practice_id;
        CREATE FUNCTION practice_scribe_tenant_reject_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'practice_scribe_tenant_immutable' USING ERRCODE = '23514';
        END $$;
        CREATE TRIGGER practice_scribe_tenant_immutable
        BEFORE UPDATE OR DELETE ON practice_scribe_tenant
        FOR EACH ROW EXECUTE FUNCTION practice_scribe_tenant_reject_mutation();
    """)


def downgrade():
    raise RuntimeError(
        "Expand recovery leaves practice Scribe tenancy dormant; contract cleanup requires approval"
    )
