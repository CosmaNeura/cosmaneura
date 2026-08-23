"""Guard provider-practice bootstrap and organization domain attachment.

Revision ID: 4af72b94781e
Revises: e4b9a63f210d
Create Date: 2026-08-23 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "4af72b94781e"
down_revision: Union[str, None] = "e4b9a63f210d"
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


def _refuse_populated_downgrade() -> None:
    """Do not remove evidence guards after organization access is in use."""

    if op.get_context().as_sql:
        raise RuntimeError(
            "provider organization evidence-guard downgrade is unsupported "
            "in offline mode"
        )
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "LOCK TABLE provider_organization, practice, "
            "organization_roster_approval, practice_member "
            "IN ACCESS EXCLUSIVE MODE"
        )
    )
    counts = {
        "provider_organizations": connection.execute(
            sa.text("SELECT count(*) FROM provider_organization")
        ).scalar_one(),
        "linked_practices": connection.execute(
            sa.text("SELECT count(*) FROM practice WHERE organization_id IS NOT NULL")
        ).scalar_one(),
        "roster_approvals": connection.execute(
            sa.text("SELECT count(*) FROM organization_roster_approval")
        ).scalar_one(),
        "organization_members": connection.execute(
            sa.text(
                "SELECT count(*) FROM practice_member "
                "WHERE organization_roster_approval_id IS NOT NULL"
            )
        ).scalar_one(),
        "negotiated_baa_required_practices": connection.execute(
            sa.text(
                "SELECT count(*) FROM practice WHERE negotiated_baa_required IS TRUE"
            )
        ).scalar_one(),
    }
    if any(counts.values()):
        summary = ", ".join(f"{name}={count}" for name, count in counts.items())
        raise RuntimeError(
            "refusing to remove provider organization evidence guards: " + summary
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

    op.execute(
        """
        CREATE FUNCTION cosmas_guard_organization_roster_approval_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF OLD.revoked_at IS NULL OR OLD.retain_until IS NULL
               OR CURRENT_TIMESTAMP < OLD.retain_until THEN
              RAISE EXCEPTION
                'organization roster approval evidence is still retained'
                USING ERRCODE = '55000';
            END IF;
            RETURN OLD;
          END IF;

          IF OLD.revoked_at IS NOT NULL THEN
            RAISE EXCEPTION
              'revoked organization roster approval evidence is immutable'
              USING ERRCODE = '55000';
          END IF;

          IF NEW.revoked_at IS NOT NULL
             AND NEW.revoked_at < OLD.approved_at THEN
            RAISE EXCEPTION
              'organization roster revocation predates its approval'
              USING ERRCODE = '23514';
          END IF;

          IF NEW.id IS DISTINCT FROM OLD.id
             OR NEW.created_at IS DISTINCT FROM OLD.created_at
             OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
             OR NEW.target_practice_id IS DISTINCT FROM OLD.target_practice_id
             OR NEW.approved_email IS DISTINCT FROM OLD.approved_email
             OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
             OR NEW.approved_by_ref IS DISTINCT FROM OLD.approved_by_ref
             OR NEW.source_ref IS DISTINCT FROM OLD.source_ref
             OR NEW.source_sha256 IS DISTINCT FROM OLD.source_sha256
             OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
             OR (
               OLD.claimed_doctor_id IS NOT NULL
               AND (
                 NEW.claimed_doctor_id IS DISTINCT FROM OLD.claimed_doctor_id
                 OR NEW.claimed_at IS DISTINCT FROM OLD.claimed_at
                 OR NEW.claimed_code_version IS DISTINCT FROM OLD.claimed_code_version
               )
             ) THEN
            RAISE EXCEPTION
              'organization roster approval provenance is immutable'
              USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER organization_roster_approval_guard_mutation
        BEFORE UPDATE OR DELETE ON organization_roster_approval
        FOR EACH ROW
        EXECUTE FUNCTION cosmas_guard_organization_roster_approval_mutation()
        """
    )

    op.execute(
        """
        CREATE FUNCTION cosmas_guard_organization_member_evidence_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF OLD.organization_roster_approval_id IS NOT NULL
               AND (
                 OLD.access_revoked_at IS NULL
                 OR OLD.access_records_retain_until IS NULL
                 OR CURRENT_TIMESTAMP < OLD.access_records_retain_until
               ) THEN
              RAISE EXCEPTION
                'organization membership evidence is still retained'
                USING ERRCODE = '55000';
            END IF;
            RETURN OLD;
          END IF;

          IF OLD.organization_roster_approval_id IS NULL THEN
            RETURN NEW;
          END IF;

          IF OLD.access_revoked_at IS NOT NULL THEN
            RAISE EXCEPTION
              'revoked organization membership evidence is immutable'
              USING ERRCODE = '55000';
          END IF;

          IF NEW.access_revoked_at IS NOT NULL
             AND NEW.access_revoked_at < OLD.organization_authorized_at THEN
            RAISE EXCEPTION
              'organization membership revocation predates its authorization'
              USING ERRCODE = '23514';
          END IF;

          IF NEW.practice_id IS DISTINCT FROM OLD.practice_id
             OR NEW.doctor_id IS DISTINCT FROM OLD.doctor_id
             OR NEW.organization_roster_approval_id IS DISTINCT FROM
                OLD.organization_roster_approval_id
             OR NEW.organization_authorized_at IS DISTINCT FROM
                OLD.organization_authorized_at THEN
            RAISE EXCEPTION
              'organization membership binding is immutable'
              USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER practice_member_organization_evidence_guard_mutation
        BEFORE UPDATE OR DELETE ON practice_member
        FOR EACH ROW
        EXECUTE FUNCTION cosmas_guard_organization_member_evidence_mutation()
        """
    )


def downgrade() -> None:
    _refuse_populated_downgrade()
    op.execute(
        "DROP TRIGGER IF EXISTS practice_member_organization_evidence_guard_mutation "
        "ON practice_member"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "cosmas_guard_organization_member_evidence_mutation()"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS organization_roster_approval_guard_mutation "
        "ON organization_roster_approval"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "cosmas_guard_organization_roster_approval_mutation()"
    )

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
