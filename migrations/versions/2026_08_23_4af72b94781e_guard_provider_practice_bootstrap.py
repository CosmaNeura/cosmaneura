"""Guard provider-practice bootstrap and organization domain attachment.

Revision ID: 4af72b94781e
Revises: ab25569de9c6
Create Date: 2026-08-23 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "4af72b94781e"
down_revision: Union[str, None] = "ab25569de9c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _assert_existing_rows_are_compatible() -> None:
    """Fail before DDL with aggregate-only diagnostics for legacy rows."""

    connection = op.get_bind()
    counts = {
        "invalid_member_roles": connection.execute(
            sa.text(
                "SELECT count(*) FROM practice_member "
                "WHERE role NOT IN ('owner', 'workforce')"
            )
        ).scalar_one(),
        "invalid_roster_claim_bindings": connection.execute(
            sa.text(
                "SELECT count(*) "
                "FROM practice_member AS member "
                "LEFT JOIN organization_roster_approval AS approval "
                "ON approval.id = member.organization_roster_approval_id "
                "WHERE member.organization_roster_approval_id IS NOT NULL "
                "AND (approval.id IS NULL "
                "OR approval.target_practice_id IS DISTINCT FROM member.practice_id "
                "OR approval.claimed_doctor_id IS DISTINCT FROM member.doctor_id)"
            )
        ).scalar_one(),
        "invalid_organization_domains": connection.execute(
            sa.text(
                "SELECT count(*) "
                "FROM practice "
                "JOIN provider_organization "
                "ON provider_organization.id = practice.organization_id "
                "WHERE practice.organization_id IS NOT NULL "
                "AND practice.email_domain IS DISTINCT FROM "
                "provider_organization.email_domain"
            )
        ).scalar_one(),
    }
    if any(counts.values()):
        summary = ", ".join(f"{name}={count}" for name, count in counts.items())
        raise RuntimeError(
            "provider practice bootstrap migration preflight failed: " + summary
        )


def upgrade() -> None:
    _assert_existing_rows_are_compatible()
    op.add_column(
        "practice",
        sa.Column(
            "negotiated_baa_required",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
            comment=(
                "Fail-closed administrative marker: this practice may never "
                "use clickwrap BAA."
            ),
        ),
    )
    op.create_check_constraint(
        "ck_practice_negotiated_baa_required_no_clickwrap",
        "practice",
        "NOT negotiated_baa_required OR ("
        "baa_version IS NULL "
        "AND baa_accepted_at IS NULL "
        "AND baa_accepted_by_doctor_id IS NULL "
        "AND baa_signature_name IS NULL "
        "AND baa_signature_title IS NULL "
        "AND trial_ends_at IS NULL)",
    )

    op.create_unique_constraint(
        "uq_provider_organization_id_email_domain",
        "provider_organization",
        ["id", "email_domain"],
    )
    op.create_foreign_key(
        "fk_practice_organization_domain",
        "practice",
        "provider_organization",
        ["organization_id", "email_domain"],
        ["id", "email_domain"],
    )
    op.create_index(
        "practice_organization_id_idx",
        "practice",
        ["organization_id"],
        unique=False,
    )

    op.create_index(
        "uq_practice_access_audit_provider_bootstrap_ref",
        "practice_access_audit",
        ["document_reference"],
        unique=True,
        postgresql_where=sa.text(
            "action = 'provider_practice_bootstrapped' "
            "AND document_reference IS NOT NULL"
        ),
    )

    op.create_unique_constraint(
        "uq_org_roster_approval_claim_binding",
        "organization_roster_approval",
        ["id", "target_practice_id", "claimed_doctor_id"],
    )
    op.create_foreign_key(
        "fk_practice_member_organization_claim_binding",
        "practice_member",
        "organization_roster_approval",
        ["organization_roster_approval_id", "practice_id", "doctor_id"],
        ["id", "target_practice_id", "claimed_doctor_id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_check_constraint(
        "ck_practice_member_role",
        "practice_member",
        "role IN ('owner', 'workforce')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_practice_member_role",
        "practice_member",
        type_="check",
    )
    op.drop_constraint(
        "fk_practice_member_organization_claim_binding",
        "practice_member",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_org_roster_approval_claim_binding",
        "organization_roster_approval",
        type_="unique",
    )

    op.drop_index(
        "uq_practice_access_audit_provider_bootstrap_ref",
        table_name="practice_access_audit",
        postgresql_where=sa.text(
            "action = 'provider_practice_bootstrapped' "
            "AND document_reference IS NOT NULL"
        ),
    )

    op.drop_index("practice_organization_id_idx", table_name="practice")
    op.drop_constraint(
        "fk_practice_organization_domain",
        "practice",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_provider_organization_id_email_domain",
        "provider_organization",
        type_="unique",
    )

    op.drop_constraint(
        "ck_practice_negotiated_baa_required_no_clickwrap",
        "practice",
        type_="check",
    )
    op.drop_column("practice", "negotiated_baa_required")
