"""Add environment-scoped superadmin capability grants and audit.

Revision ID: 6a91d2f4c8e0
Revises: f70935048976
Create Date: 2026-08-24 12:00:00.000000
"""

from collections.abc import Sequence

import activemodel.types.typeid
import sqlalchemy as sa
from alembic import op


revision: str = "6a91d2f4c8e0"
down_revision: str | None = "f70935048976"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id(prefix: str) -> sa.Column:
    return sa.Column(
        "id",
        activemodel.types.typeid.TypeIDType(prefix=prefix),
        nullable=False,
        comment=f"TypeID with prefix: {prefix}",
    )


def upgrade() -> None:
    op.create_table(
        "superadmin_capability_grant",
        _id("superadmin_capability"),
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
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("capability_kind", sa.String(length=32), nullable=False),
        sa.Column("clerk_issuer", sa.String(length=255), nullable=False),
        sa.Column("clerk_subject", sa.String(length=255), nullable=False),
        sa.Column(
            "doctor_id",
            activemodel.types.typeid.TypeIDType(prefix="doctor"),
            nullable=False,
        ),
        sa.Column("issuer_sha256", sa.String(length=64), nullable=False),
        sa.Column("subject_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "verified_primary_email_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("source_ref", sa.String(length=255), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("grant_plan_sha256", sa.String(length=64), nullable=False),
        sa.Column("granted_by_ref", sa.String(length=255), nullable=False),
        sa.Column("grant_reason_code", sa.String(length=64), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_ref", sa.String(length=255), nullable=True),
        sa.Column("revocation_reason_code", sa.String(length=64), nullable=True),
        sa.Column("revocation_plan_sha256", sa.String(length=64), nullable=True),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "environment IN ('staging', 'production')",
            name="superadmin_capability_grant_ck_superadmin_capability_grant_environment_check",
        ),
        sa.CheckConstraint(
            "capability_kind IN ('platform_admin', 'view_as')",
            name="superadmin_capability_grant_ck_superadmin_capability_grant_kind_check",
        ),
        sa.CheckConstraint(
            "left(clerk_issuer, 8) = 'https://' "
            "AND right(clerk_issuer, 1) <> '/'",
            name="superadmin_capability_grant_ck_superadmin_capability_grant_issuer_check",
        ),
        sa.CheckConstraint(
            "left(clerk_subject, 5) = 'user_'",
            name="superadmin_capability_grant_ck_superadmin_capability_grant_subject_check",
        ),
        sa.CheckConstraint(
            "issuer_sha256 ~ '^[0-9a-f]{64}$' "
            "AND subject_sha256 ~ '^[0-9a-f]{64}$' "
            "AND verified_primary_email_sha256 ~ '^[0-9a-f]{64}$' "
            "AND source_sha256 ~ '^[0-9a-f]{64}$' "
            "AND grant_plan_sha256 ~ '^[0-9a-f]{64}$'",
            name="superadmin_capability_grant_ck_superadmin_capability_grant_digests_check",
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL "
            "AND revoked_by_ref IS NULL "
            "AND revocation_reason_code IS NULL "
            "AND revocation_plan_sha256 IS NULL "
            "AND retain_until IS NULL) OR "
            "(revoked_at IS NOT NULL "
            "AND revoked_at >= granted_at "
            "AND revoked_by_ref IS NOT NULL "
            "AND revocation_reason_code IS NOT NULL "
            "AND revocation_plan_sha256 ~ '^[0-9a-f]{64}$' "
            "AND retain_until IS NOT NULL "
            "AND retain_until >= revoked_at + interval '7 years')",
            name="superadmin_capability_grant_ck_superadmin_capability_grant_revocation_complete_check",
        ),
        sa.ForeignKeyConstraint(
            ["doctor_id"],
            ["doctor.id"],
            name="superadmin_capability_grant_doctor_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="superadmin_capability_grant_pkey"),
        comment="One independently revocable capability for one immutable identity.",
    )
    op.create_index(
        "uq_superadmin_capability_active_identity_kind",
        "superadmin_capability_grant",
        ["environment", "capability_kind", "clerk_issuer", "clerk_subject"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "uq_superadmin_capability_active_doctor_kind",
        "superadmin_capability_grant",
        ["environment", "capability_kind", "doctor_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "superadmin_capability_grant_retain_until_idx",
        "superadmin_capability_grant",
        ["retain_until"],
        unique=False,
    )

    op.create_table(
        "superadmin_capability_audit",
        _id("superadmin_capability_audit"),
        sa.Column(
            "grant_id",
            activemodel.types.typeid.TypeIDType(prefix="superadmin_capability"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("capability_kind", sa.String(length=32), nullable=False),
        sa.Column(
            "identity_binding_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("operator_ref", sa.String(length=255), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('granted', 'revoked')",
            name="superadmin_capability_audit_ck_superadmin_capability_audit_action_check",
        ),
        sa.CheckConstraint(
            "environment IN ('staging', 'production')",
            name="superadmin_capability_audit_ck_superadmin_capability_audit_environment_check",
        ),
        sa.CheckConstraint(
            "capability_kind IN ('platform_admin', 'view_as')",
            name="superadmin_capability_audit_ck_superadmin_capability_audit_kind_check",
        ),
        sa.CheckConstraint(
            "identity_binding_sha256 ~ '^[0-9a-f]{64}$' "
            "AND evidence_sha256 ~ '^[0-9a-f]{64}$'",
            name="superadmin_capability_audit_ck_superadmin_capability_audit_digests_check",
        ),
        sa.ForeignKeyConstraint(
            ["grant_id"],
            ["superadmin_capability_grant.id"],
            name="superadmin_capability_audit_grant_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="superadmin_capability_audit_pkey"),
        sa.UniqueConstraint(
            "grant_id",
            "action",
            name="uq_superadmin_capability_audit_grant_action",
        ),
        comment="Append-only, value-minimized evidence for a capability transition.",
    )
    op.create_index(
        "superadmin_capability_audit_occurred_at_idx",
        "superadmin_capability_audit",
        ["occurred_at"],
        unique=False,
    )

    op.execute(
        """
        CREATE FUNCTION superadmin_capability_guard_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'superadmin capability evidence is retained';
            END IF;
            IF OLD.revoked_at IS NOT NULL
               OR NEW.revoked_at IS NULL
               OR OLD.id IS DISTINCT FROM NEW.id
               OR OLD.created_at IS DISTINCT FROM NEW.created_at
               OR OLD.environment IS DISTINCT FROM NEW.environment
               OR OLD.capability_kind IS DISTINCT FROM NEW.capability_kind
               OR OLD.clerk_issuer IS DISTINCT FROM NEW.clerk_issuer
               OR OLD.clerk_subject IS DISTINCT FROM NEW.clerk_subject
               OR OLD.doctor_id IS DISTINCT FROM NEW.doctor_id
               OR OLD.issuer_sha256 IS DISTINCT FROM NEW.issuer_sha256
               OR OLD.subject_sha256 IS DISTINCT FROM NEW.subject_sha256
               OR OLD.verified_primary_email_sha256 IS DISTINCT FROM
                    NEW.verified_primary_email_sha256
               OR OLD.source_ref IS DISTINCT FROM NEW.source_ref
               OR OLD.source_sha256 IS DISTINCT FROM NEW.source_sha256
               OR OLD.grant_plan_sha256 IS DISTINCT FROM NEW.grant_plan_sha256
               OR OLD.granted_by_ref IS DISTINCT FROM NEW.granted_by_ref
               OR OLD.grant_reason_code IS DISTINCT FROM NEW.grant_reason_code
               OR OLD.granted_at IS DISTINCT FROM NEW.granted_at
            THEN
                RAISE EXCEPTION
                    'superadmin capability may only transition once to revoked';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER superadmin_capability_grant_guard_mutation
        BEFORE UPDATE OR DELETE ON superadmin_capability_grant
        FOR EACH ROW EXECUTE FUNCTION superadmin_capability_guard_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER superadmin_capability_audit_reject_mutation
        BEFORE UPDATE OR DELETE ON superadmin_capability_audit
        FOR EACH ROW EXECUTE FUNCTION cosmas_reject_append_only_mutation()
        """
    )


def downgrade() -> None:
    if op.get_context().as_sql:
        raise RuntimeError(
            "superadmin capability downgrade is unsupported in offline mode"
        )
    connection = op.get_bind()
    for table_name in (
        "superadmin_capability_audit",
        "superadmin_capability_grant",
    ):
        connection.execute(sa.text(f'LOCK TABLE "{table_name}" IN ACCESS EXCLUSIVE MODE'))
        populated = connection.execute(
            sa.text(f'SELECT EXISTS (SELECT 1 FROM "{table_name}" LIMIT 1)')
        ).scalar_one()
        if populated:
            raise RuntimeError(
                "superadmin capability downgrade refused because durable records exist"
            )
    op.execute(
        "DROP TRIGGER IF EXISTS superadmin_capability_audit_reject_mutation "
        "ON superadmin_capability_audit"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS superadmin_capability_grant_guard_mutation "
        "ON superadmin_capability_grant"
    )
    op.execute("DROP FUNCTION IF EXISTS superadmin_capability_guard_mutation()")
    op.drop_index(
        "superadmin_capability_audit_occurred_at_idx",
        table_name="superadmin_capability_audit",
    )
    op.drop_table("superadmin_capability_audit")
    op.drop_index(
        "superadmin_capability_grant_retain_until_idx",
        table_name="superadmin_capability_grant",
    )
    op.drop_index(
        "uq_superadmin_capability_active_doctor_kind",
        table_name="superadmin_capability_grant",
    )
    op.drop_index(
        "uq_superadmin_capability_active_identity_kind",
        table_name="superadmin_capability_grant",
    )
    op.drop_table("superadmin_capability_grant")
