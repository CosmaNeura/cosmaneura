"""add provider organization roster access

Revision ID: ab25569de9c6
Revises: f777b62b8152
Create Date: 2026-08-22 16:46:05.450406

"""

from typing import Sequence, Union

import activemodel
import sqlalchemy as sa
import sqlmodel
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "ab25569de9c6"
down_revision: Union[str, None] = "f777b62b8152"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "provider_organization",
        sa.Column(
            "id",
            activemodel.types.typeid.TypeIDType(prefix="provider_organization"),
            nullable=False,
            comment="TypeID with prefix: provider_organization",
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
            "legal_name",
            sqlmodel.sql.sqltypes.AutoString(length=160),
            nullable=False,
        ),
        sa.Column(
            "slug",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=False,
        ),
        sa.Column(
            "email_domain",
            sqlmodel.sql.sqltypes.AutoString(length=253),
            nullable=False,
        ),
        sa.Column(
            "status",
            sqlmodel.sql.sqltypes.AutoString(length=16),
            nullable=False,
        ),
        sa.Column(
            "access_code_salt",
            sqlmodel.sql.sqltypes.AutoString(length=32),
            nullable=False,
        ),
        sa.Column(
            "access_code_digest",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=False,
        ),
        sa.Column("access_code_version", sa.Integer(), nullable=False),
        sa.Column(
            "access_code_created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "email_domain = lower(btrim(email_domain)) "
            "AND position('@' in email_domain) = 0",
            name=op.f(
                "provider_organization_ck_provider_organization_email_domain_normalized_check"
            ),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'suspended')",
            name=op.f(
                "provider_organization_ck_provider_organization_status_check"
            ),
        ),
        sa.CheckConstraint(
            "access_code_version > 0",
            name=op.f(
                "provider_organization_ck_provider_organization_access_code_version_positive_check"
            ),
        ),
        sa.CheckConstraint(
            "char_length(access_code_digest) = 64",
            name=op.f(
                "provider_organization_ck_provider_organization_access_code_digest_length_check"
            ),
        ),
        sa.CheckConstraint(
            "char_length(access_code_salt) = 32",
            name=op.f(
                "provider_organization_ck_provider_organization_access_code_salt_length_check"
            ),
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("provider_organization_pkey")
        ),
        sa.UniqueConstraint(
            "access_code_digest",
            name=op.f("provider_organization_access_code_digest_key"),
        ),
        comment="An organization above one or more practice-level access tenants.",
    )
    op.create_index(
        op.f("provider_organization_email_domain_idx"),
        "provider_organization",
        ["email_domain"],
        unique=True,
    )
    op.create_index(
        op.f("provider_organization_slug_idx"),
        "provider_organization",
        ["slug"],
        unique=True,
    )
    op.create_index(
        op.f("provider_organization_status_idx"),
        "provider_organization",
        ["status"],
        unique=False,
    )

    # The composite practice key must exist before the roster table can
    # reference it. Keeping organization_id nullable makes this additive for
    # every existing standalone practice.
    op.add_column(
        "practice",
        sa.Column(
            "organization_id",
            activemodel.types.typeid.TypeIDType(prefix="provider_organization"),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_practice_id_organization_id",
        "practice",
        ["id", "organization_id"],
    )
    op.create_foreign_key(
        op.f("practice_organization_id_fkey"),
        "practice",
        "provider_organization",
        ["organization_id"],
        ["id"],
    )

    op.create_table(
        "organization_roster_approval",
        sa.Column(
            "id",
            activemodel.types.typeid.TypeIDType(
                prefix="organization_roster_approval"
            ),
            nullable=False,
            comment="TypeID with prefix: organization_roster_approval",
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
            "organization_id",
            activemodel.types.typeid.TypeIDType(prefix="provider_organization"),
            nullable=False,
        ),
        sa.Column(
            "target_practice_id",
            activemodel.types.typeid.TypeIDType(prefix="practice"),
            nullable=False,
        ),
        sa.Column(
            "approved_email",
            sqlmodel.sql.sqltypes.AutoString(length=320),
            nullable=False,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "approved_by_ref",
            sqlmodel.sql.sqltypes.AutoString(length=255),
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
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "claimed_doctor_id",
            activemodel.types.typeid.TypeIDType(prefix="doctor"),
            nullable=True,
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_code_version", sa.Integer(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "revoked_by_ref",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=True,
        ),
        sa.Column("revocation_reason", sa.String(length=512), nullable=True),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(revoked_at IS NULL AND revoked_by_ref IS NULL "
            "AND revocation_reason IS NULL AND retain_until IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_by_ref IS NOT NULL "
            "AND revocation_reason IS NOT NULL AND retain_until IS NOT NULL "
            "AND retain_until >= revoked_at + interval '7 years')",
            name=op.f(
                "organization_roster_approval_ck_org_roster_approval_revocation_complete_check"
            ),
        ),
        sa.CheckConstraint(
            "position('@' in approved_email) > 1",
            name=op.f(
                "organization_roster_approval_ck_org_roster_approval_email_shape_check"
            ),
        ),
        sa.CheckConstraint(
            "(claimed_doctor_id IS NULL AND claimed_at IS NULL "
            "AND claimed_code_version IS NULL) OR "
            "(claimed_doctor_id IS NOT NULL AND claimed_at IS NOT NULL "
            "AND claimed_code_version IS NOT NULL "
            "AND claimed_code_version > 0)",
            name=op.f(
                "organization_roster_approval_ck_org_roster_approval_claim_complete_check"
            ),
        ),
        sa.CheckConstraint(
            "approved_email = lower(btrim(approved_email))",
            name=op.f(
                "organization_roster_approval_ck_org_roster_approval_email_normalized_check"
            ),
        ),
        sa.CheckConstraint(
            "char_length(source_sha256) = 64",
            name=op.f(
                "organization_roster_approval_ck_org_roster_approval_source_sha256_length_check"
            ),
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > approved_at",
            name=op.f(
                "organization_roster_approval_ck_org_roster_approval_expiry_after_approval_check"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["claimed_doctor_id"],
            ["doctor.id"],
            name=op.f("organization_roster_approval_claimed_doctor_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["provider_organization.id"],
            name=op.f("organization_roster_approval_organization_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["target_practice_id", "organization_id"],
            ["practice.id", "practice.organization_id"],
            name="fk_org_roster_approval_practice_organization",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("organization_roster_approval_pkey")
        ),
        comment=(
            "One exact-email organization approval and its access lifecycle.\n\n"
            "This row is authorization provenance, not a workforce attestation.  Claim\n"
            "and revocation mutate its lifecycle fields only through privileged service\n"
            "code; append-only operator evidence belongs in "
            "``PracticeAccessAudit``."
        ),
    )
    op.create_index(
        "organization_roster_approval_organization_id_idx",
        "organization_roster_approval",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "organization_roster_approval_retain_until_idx",
        "organization_roster_approval",
        ["retain_until"],
        unique=False,
    )
    op.create_index(
        "organization_roster_approval_target_practice_id_idx",
        "organization_roster_approval",
        ["target_practice_id"],
        unique=False,
    )
    op.create_index(
        "uq_org_roster_approval_active_claimed_doctor",
        "organization_roster_approval",
        ["claimed_doctor_id"],
        unique=True,
        postgresql_where=sa.text(
            "revoked_at IS NULL AND claimed_doctor_id IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_org_roster_approval_active_email",
        "organization_roster_approval",
        ["approved_email"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.add_column(
        "practice_access_audit",
        sa.Column("evidence_sha256", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        op.f(
            "practice_access_audit_ck_practice_access_audit_evidence_sha256_length_check"
        ),
        "practice_access_audit",
        "evidence_sha256 IS NULL OR char_length(evidence_sha256) = 64",
    )

    op.add_column(
        "practice_member",
        sa.Column(
            "organization_roster_approval_id",
            activemodel.types.typeid.TypeIDType(
                prefix="organization_roster_approval"
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "practice_member",
        sa.Column(
            "organization_authorized_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "practice_member",
        sa.Column("access_revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "practice_member",
        sa.Column(
            "access_revoked_by_ref",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=True,
        ),
    )
    op.add_column(
        "practice_member",
        sa.Column("access_revocation_reason", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "practice_member",
        sa.Column(
            "access_records_retain_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        op.f("practice_member_organization_roster_approval_id_key"),
        "practice_member",
        ["organization_roster_approval_id"],
    )
    op.create_index(
        "practice_member_access_records_retain_until_idx",
        "practice_member",
        ["access_records_retain_until"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("practice_member_organization_roster_approval_id_fkey"),
        "practice_member",
        "organization_roster_approval",
        ["organization_roster_approval_id"],
        ["id"],
    )
    op.create_check_constraint(
        op.f(
            "practice_member_ck_practice_member_organization_authorization_complete_check"
        ),
        "practice_member",
        "(organization_roster_approval_id IS NULL "
        "AND organization_authorized_at IS NULL) OR "
        "(organization_roster_approval_id IS NOT NULL "
        "AND organization_authorized_at IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("practice_member_ck_practice_member_revocation_complete_check"),
        "practice_member",
        "(access_revoked_at IS NULL AND access_revoked_by_ref IS NULL "
        "AND access_revocation_reason IS NULL "
        "AND access_records_retain_until IS NULL) OR "
        "(access_revoked_at IS NOT NULL "
        "AND access_revoked_by_ref IS NOT NULL "
        "AND access_revocation_reason IS NOT NULL "
        "AND access_records_retain_until IS NOT NULL "
        "AND access_records_retain_until >= "
        "access_revoked_at + interval '7 years')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("practice_member_ck_practice_member_revocation_complete_check"),
        "practice_member",
        type_="check",
    )
    op.drop_constraint(
        op.f(
            "practice_member_ck_practice_member_organization_authorization_complete_check"
        ),
        "practice_member",
        type_="check",
    )
    op.drop_constraint(
        op.f("practice_member_organization_roster_approval_id_fkey"),
        "practice_member",
        type_="foreignkey",
    )
    op.drop_index(
        "practice_member_access_records_retain_until_idx",
        table_name="practice_member",
    )
    op.drop_constraint(
        op.f("practice_member_organization_roster_approval_id_key"),
        "practice_member",
        type_="unique",
    )
    op.drop_column("practice_member", "access_records_retain_until")
    op.drop_column("practice_member", "access_revocation_reason")
    op.drop_column("practice_member", "access_revoked_by_ref")
    op.drop_column("practice_member", "access_revoked_at")
    op.drop_column("practice_member", "organization_authorized_at")
    op.drop_column("practice_member", "organization_roster_approval_id")

    op.drop_constraint(
        op.f(
            "practice_access_audit_ck_practice_access_audit_evidence_sha256_length_check"
        ),
        "practice_access_audit",
        type_="check",
    )
    op.drop_column("practice_access_audit", "evidence_sha256")

    op.drop_index(
        "uq_org_roster_approval_active_email",
        table_name="organization_roster_approval",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_index(
        "uq_org_roster_approval_active_claimed_doctor",
        table_name="organization_roster_approval",
        postgresql_where=sa.text(
            "revoked_at IS NULL AND claimed_doctor_id IS NOT NULL"
        ),
    )
    op.drop_index(
        "organization_roster_approval_target_practice_id_idx",
        table_name="organization_roster_approval",
    )
    op.drop_index(
        "organization_roster_approval_retain_until_idx",
        table_name="organization_roster_approval",
    )
    op.drop_index(
        "organization_roster_approval_organization_id_idx",
        table_name="organization_roster_approval",
    )
    op.drop_table("organization_roster_approval")

    op.drop_constraint(
        op.f("practice_organization_id_fkey"),
        "practice",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_practice_id_organization_id", "practice", type_="unique"
    )
    op.drop_column("practice", "organization_id")

    op.drop_index(
        op.f("provider_organization_status_idx"),
        table_name="provider_organization",
    )
    op.drop_index(
        op.f("provider_organization_slug_idx"),
        table_name="provider_organization",
    )
    op.drop_index(
        op.f("provider_organization_email_domain_idx"),
        table_name="provider_organization",
    )
    op.drop_table("provider_organization")
