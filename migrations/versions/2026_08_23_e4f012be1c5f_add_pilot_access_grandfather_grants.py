"""Add retained per-Clerk pilot access grandfather grants.

Revision ID: e4f012be1c5f
Revises: 4af72b94781e
Create Date: 2026-08-23 00:00:01.000000
"""

from typing import Sequence, Union

import activemodel.types.typeid
import sqlalchemy as sa
import sqlmodel
from alembic import op


revision: str = "e4f012be1c5f"
down_revision: Union[str, None] = "4af72b94781e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pilot_access_grandfather_grant",
        sa.Column(
            "id",
            activemodel.types.typeid.TypeIDType(prefix="pilot_access_grant"),
            nullable=False,
            comment="TypeID with prefix: pilot_access_grant",
        ),
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
            "clerk_id",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column(
            "doctor_id_snapshot",
            activemodel.types.typeid.TypeIDType(prefix="doctor"),
            nullable=True,
        ),
        sa.Column(
            "practice_id_snapshot",
            activemodel.types.typeid.TypeIDType(prefix="practice"),
            nullable=True,
        ),
        sa.Column(
            "email_snapshot",
            sqlmodel.sql.sqltypes.AutoString(length=320),
            nullable=False,
        ),
        sa.Column(
            "email_sha256",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=False,
        ),
        sa.Column(
            "source_ref",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column(
            "source_sha256",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=False,
        ),
        sa.Column(
            "grant_plan_sha256",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=False,
        ),
        sa.Column(
            "granted_by_ref",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column("grant_reason", sa.String(length=512), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "revoked_by_ref",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=True,
        ),
        sa.Column("revocation_reason", sa.String(length=512), nullable=True),
        sa.Column(
            "revocation_plan_sha256",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "left(clerk_id, 5) = 'user_'",
            name=op.f(
                "pilot_access_grandfather_grant_ck_pilot_access_grant_clerk_subject_check"
            ),
        ),
        sa.CheckConstraint(
            "email_snapshot = lower(btrim(email_snapshot)) "
            "AND position('@' in email_snapshot) > 1",
            name=op.f(
                "pilot_access_grandfather_grant_ck_pilot_access_grant_email_normalized_check"
            ),
        ),
        sa.CheckConstraint(
            "email_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f(
                "pilot_access_grandfather_grant_ck_pilot_access_grant_email_sha256_check"
            ),
        ),
        sa.CheckConstraint(
            "grant_plan_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f(
                "pilot_access_grandfather_grant_ck_pilot_access_grant_plan_sha256_check"
            ),
        ),
        sa.CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f(
                "pilot_access_grandfather_grant_ck_pilot_access_grant_source_sha256_check"
            ),
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL "
            "AND revoked_by_ref IS NULL "
            "AND revocation_reason IS NULL "
            "AND revocation_plan_sha256 IS NULL "
            "AND retain_until IS NULL) OR "
            "(revoked_at IS NOT NULL "
            "AND revoked_at >= granted_at "
            "AND revoked_by_ref IS NOT NULL "
            "AND revocation_reason IS NOT NULL "
            "AND revocation_plan_sha256 ~ '^[0-9a-f]{64}$' "
            "AND retain_until IS NOT NULL "
            "AND retain_until >= revoked_at + interval '7 years')",
            name=op.f(
                "pilot_access_grandfather_grant_ck_pilot_access_grant_revocation_complete_check"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["doctor_id_snapshot"],
            ["doctor.id"],
            name=op.f(
                "pilot_access_grandfather_grant_doctor_id_snapshot_fkey"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["practice_id_snapshot"],
            ["practice.id"],
            name=op.f(
                "pilot_access_grandfather_grant_practice_id_snapshot_fkey"
            ),
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pilot_access_grandfather_grant_pkey")
        ),
        comment=(
            "Durable per-Clerk-account evidence for legacy pilot access; "
            "Doctor and practice identifiers are grant-time audit snapshots only."
        ),
    )
    op.create_index(
        "uq_pilot_access_grant_active_clerk",
        "pilot_access_grandfather_grant",
        ["clerk_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "uq_pilot_access_grant_active_doctor",
        "pilot_access_grandfather_grant",
        ["doctor_id_snapshot"],
        unique=True,
        postgresql_where=sa.text(
            "revoked_at IS NULL AND doctor_id_snapshot IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_pilot_access_grant_active_email_sha256",
        "pilot_access_grandfather_grant",
        ["email_sha256"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "pilot_access_grant_retain_until_idx",
        "pilot_access_grandfather_grant",
        ["retain_until"],
        unique=False,
    )
    op.execute(
        """
        CREATE FUNCTION cosmas_guard_pilot_access_grant_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF OLD.revoked_at IS NULL OR OLD.retain_until IS NULL
               OR CURRENT_TIMESTAMP < OLD.retain_until THEN
              RAISE EXCEPTION
                'pilot access authorization evidence is still retained'
                USING ERRCODE = '55000';
            END IF;
            RETURN OLD;
          END IF;

          IF OLD.revoked_at IS NOT NULL THEN
            RAISE EXCEPTION
              'revoked pilot access authorization evidence is immutable'
              USING ERRCODE = '55000';
          END IF;

          IF NEW.id IS DISTINCT FROM OLD.id
             OR NEW.created_at IS DISTINCT FROM OLD.created_at
             OR NEW.clerk_id IS DISTINCT FROM OLD.clerk_id
             OR NEW.doctor_id_snapshot IS DISTINCT FROM OLD.doctor_id_snapshot
             OR NEW.practice_id_snapshot IS DISTINCT FROM OLD.practice_id_snapshot
             OR NEW.email_snapshot IS DISTINCT FROM OLD.email_snapshot
             OR NEW.email_sha256 IS DISTINCT FROM OLD.email_sha256
             OR NEW.source_ref IS DISTINCT FROM OLD.source_ref
             OR NEW.source_sha256 IS DISTINCT FROM OLD.source_sha256
             OR NEW.grant_plan_sha256 IS DISTINCT FROM OLD.grant_plan_sha256
             OR NEW.granted_by_ref IS DISTINCT FROM OLD.granted_by_ref
             OR NEW.grant_reason IS DISTINCT FROM OLD.grant_reason
             OR NEW.granted_at IS DISTINCT FROM OLD.granted_at
             OR NEW.revoked_at IS NULL THEN
            RAISE EXCEPTION
              'pilot access grant evidence may only transition to revoked'
              USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER pilot_access_grandfather_grant_guard_mutation
        BEFORE UPDATE OR DELETE ON pilot_access_grandfather_grant
        FOR EACH ROW EXECUTE FUNCTION cosmas_guard_pilot_access_grant_mutation()
        """
    )


def downgrade() -> None:
    populated = op.get_bind().execute(
        sa.text("SELECT count(*) FROM pilot_access_grandfather_grant")
    ).scalar_one()
    if populated:
        raise RuntimeError(
            "refusing to drop retained pilot access authorization evidence: "
            f"row_count={populated}"
        )
    op.execute(
        "DROP TRIGGER IF EXISTS pilot_access_grandfather_grant_guard_mutation "
        "ON pilot_access_grandfather_grant"
    )
    op.execute("DROP FUNCTION IF EXISTS cosmas_guard_pilot_access_grant_mutation()")
    op.drop_index(
        "pilot_access_grant_retain_until_idx",
        table_name="pilot_access_grandfather_grant",
    )
    op.drop_index(
        "uq_pilot_access_grant_active_doctor",
        table_name="pilot_access_grandfather_grant",
        postgresql_where=sa.text(
            "revoked_at IS NULL AND doctor_id_snapshot IS NOT NULL"
        ),
    )
    op.drop_index(
        "uq_pilot_access_grant_active_email_sha256",
        table_name="pilot_access_grandfather_grant",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_index(
        "uq_pilot_access_grant_active_clerk",
        table_name="pilot_access_grandfather_grant",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_table("pilot_access_grandfather_grant")
