"""Add durable public onboarding evidence.

Revision ID: e7a9c3d5f102
Revises: d0a4c8e2f611
Create Date: 2026-08-27 16:15:00.000000
"""

from collections.abc import Sequence

import activemodel.types.typeid
import sqlalchemy as sa
from alembic import op


revision: str = "e7a9c3d5f102"
down_revision: str | None = "d0a4c8e2f611"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id(prefix: str) -> sa.Column:
    return sa.Column(
        "id",
        activemodel.types.typeid.TypeIDType(prefix=prefix),
        nullable=False,
        comment=f"TypeID with prefix: {prefix}",
    )


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
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
    )


def _typeid(name: str, prefix: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name,
        activemodel.types.typeid.TypeIDType(prefix=prefix),
        nullable=nullable,
    )


def _refuse_populated_downgrade() -> None:
    """Never discard public onboarding evidence or provenance silently."""

    if op.get_context().as_sql:
        raise RuntimeError(
            "public onboarding evidence downgrade is unsupported in offline mode"
        )
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "LOCK TABLE public_legal_acceptance, practice_invitation, practice "
            "IN ACCESS EXCLUSIVE MODE"
        )
    )
    counts = {
        "legal_acceptances": connection.execute(
            sa.text("SELECT count(*) FROM public_legal_acceptance")
        ).scalar_one(),
        "practice_invitations": connection.execute(
            sa.text("SELECT count(*) FROM practice_invitation")
        ).scalar_one(),
        "public_practices": connection.execute(
            sa.text(
                "SELECT count(*) FROM practice "
                "WHERE acquisition_source = 'public_self_service' "
                "OR public_pilot_cohort IS TRUE"
            )
        ).scalar_one(),
    }
    if any(counts.values()):
        summary = ", ".join(f"{name}={count}" for name, count in counts.items())
        raise RuntimeError(
            "refusing to discard public onboarding evidence: " + summary
        )


def upgrade() -> None:
    op.add_column(
        "practice",
        sa.Column(
            "acquisition_source",
            sa.String(length=32),
            server_default="managed",
            nullable=False,
            comment="Enrollment provenance for migration fencing; authorization-neutral.",
        ),
    )
    # The temporary default backfills every pre-existing practice as managed.
    # Remove it in the same transaction so a pre-gate binary cannot create a
    # practice while the database is already on this schema.
    op.alter_column(
        "practice",
        "acquisition_source",
        existing_type=sa.String(length=32),
        existing_nullable=False,
        server_default=None,
    )
    op.create_check_constraint(
        "ck_practice_acquisition_source",
        "practice",
        "acquisition_source IN ('managed', 'public_self_service')",
    )
    op.create_check_constraint(
        "ck_practice_public_self_service_no_npi",
        "practice",
        "acquisition_source <> 'public_self_service' OR npi IS NULL",
    )
    op.add_column(
        "practice",
        sa.Column(
            "public_pilot_cohort",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
            comment="Explicit capability-rollout cohort; false is the safe default.",
        ),
    )

    op.create_table(
        "public_legal_acceptance",
        _id("public_legal_acceptance"),
        *_timestamps(),
        _typeid("practice_id", "practice"),
        sa.Column("document_kind", sa.String(length=16), nullable=False),
        sa.Column("document_version", sa.String(length=64), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        _typeid("signer_doctor_id", "doctor"),
        sa.Column("signer_email", sa.String(length=320), nullable=False),
        sa.Column("signer_role", sa.String(length=16), nullable=False),
        sa.Column("signature_name", sa.String(length=120), nullable=False),
        sa.Column("signature_title", sa.String(length=120), nullable=False),
        sa.Column("authority_confirmed", sa.Boolean(), nullable=False),
        sa.Column("signer_authority", sa.String(length=255), nullable=False),
        sa.Column("accepted_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "document_kind IN ('terms', 'privacy')",
            name=op.f(
                "public_legal_acceptance_ck_public_legal_acceptance_document_kind"
            ),
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f(
                "public_legal_acceptance_ck_public_legal_acceptance_content_sha256"
            ),
        ),
        sa.CheckConstraint(
            "signer_role = 'owner'",
            name=op.f(
                "public_legal_acceptance_ck_public_legal_acceptance_signer_role"
            ),
        ),
        sa.CheckConstraint(
            "char_length(btrim(signature_name)) > 0 "
            "AND char_length(btrim(signature_title)) > 0 "
            "AND authority_confirmed IS TRUE "
            "AND char_length(btrim(signer_authority)) > 0",
            name=op.f(
                "public_legal_acceptance_ck_public_legal_acceptance_signer_authority"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["practice_id"],
            ["practice.id"],
            name=op.f("public_legal_acceptance_practice_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["signer_doctor_id"],
            ["doctor.id"],
            name=op.f("public_legal_acceptance_signer_doctor_id_fkey"),
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("public_legal_acceptance_pkey")
        ),
        sa.UniqueConstraint(
            "practice_id",
            "document_kind",
            "document_version",
            "content_sha256",
            name="uq_public_legal_acceptance_document",
        ),
        comment="Append-only acceptance of one exact public legal document edition.",
    )
    op.create_index(
        "public_legal_acceptance_practice_kind_idx",
        "public_legal_acceptance",
        ["practice_id", "document_kind"],
    )

    op.create_table(
        "practice_invitation",
        _id("practice_invitation"),
        *_timestamps(),
        _typeid("practice_id", "practice"),
        _typeid("invited_by_doctor_id", "doctor"),
        sa.Column(
            "role", sa.String(length=16), server_default="workforce", nullable=False
        ),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("invitee_email", sa.String(length=320), nullable=False),
        sa.Column("invitee_email_digest", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key_digest", sa.String(length=64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        _typeid("accepted_by_doctor_id", "doctor", nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        _typeid("revoked_by_doctor_id", "doctor", nullable=True),
        sa.Column("revocation_reason", sa.String(length=512)),
        sa.CheckConstraint(
            "token_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("practice_invitation_ck_practice_invitation_token_digest"),
        ),
        sa.CheckConstraint(
            "invitee_email = lower(btrim(invitee_email)) "
            "AND position('@' IN invitee_email) > 1",
            name=op.f("practice_invitation_ck_practice_invitation_normalized_email"),
        ),
        sa.CheckConstraint(
            "invitee_email_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("practice_invitation_ck_practice_invitation_email_digest"),
        ),
        sa.CheckConstraint(
            "idempotency_key_digest ~ '^[0-9a-f]{64}$'",
            name=op.f(
                "practice_invitation_ck_practice_invitation_idempotency_key_digest"
            ),
        ),
        sa.CheckConstraint(
            "role = 'workforce'",
            name=op.f("practice_invitation_ck_practice_invitation_role"),
        ),
        sa.CheckConstraint(
            "expires_at > issued_at",
            name=op.f("practice_invitation_ck_practice_invitation_expiry"),
        ),
        sa.CheckConstraint(
            "(accepted_at IS NULL AND accepted_by_doctor_id IS NULL "
            "AND revoked_at IS NULL AND revoked_by_doctor_id IS NULL "
            "AND revocation_reason IS NULL) OR "
            "(accepted_at IS NOT NULL AND accepted_by_doctor_id IS NOT NULL "
            "AND revoked_at IS NULL AND revoked_by_doctor_id IS NULL "
            "AND revocation_reason IS NULL) OR "
            "(accepted_at IS NULL AND accepted_by_doctor_id IS NULL "
            "AND revoked_at IS NOT NULL AND revoked_by_doctor_id IS NOT NULL "
            "AND revocation_reason IS NOT NULL)",
            name=op.f("practice_invitation_ck_practice_invitation_terminal_state"),
        ),
        sa.CheckConstraint(
            "accepted_by_doctor_id IS NULL "
            "OR accepted_by_doctor_id <> invited_by_doctor_id",
            name=op.f(
                "practice_invitation_ck_practice_invitation_distinct_acceptor"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["practice_id"],
            ["practice.id"],
            name=op.f("practice_invitation_practice_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_doctor_id"],
            ["doctor.id"],
            name=op.f("practice_invitation_invited_by_doctor_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_doctor_id"],
            ["doctor.id"],
            name=op.f("practice_invitation_accepted_by_doctor_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_doctor_id"],
            ["doctor.id"],
            name=op.f("practice_invitation_revoked_by_doctor_id_fkey"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("practice_invitation_pkey")),
        sa.UniqueConstraint(
            "token_digest", name=op.f("practice_invitation_token_digest_key")
        ),
        sa.UniqueConstraint(
            "practice_id",
            "idempotency_key_digest",
            name="uq_practice_invitation_idempotency_key",
        ),
        comment="Hashed, single-use invitation with immutable terminal evidence.",
    )
    op.create_index(
        "practice_invitation_practice_email_idx",
        "practice_invitation",
        ["practice_id", "invitee_email_digest"],
    )
    op.create_index(
        "practice_invitation_expires_at_idx",
        "practice_invitation",
        ["expires_at"],
    )

    op.execute(
        """
        CREATE FUNCTION cosmas_validate_public_legal_acceptance_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.signer_role IS DISTINCT FROM 'owner'
             OR NOT EXISTS (
               SELECT 1
                 FROM practice_member member
                 JOIN doctor signer ON signer.id = member.doctor_id
                WHERE member.practice_id = NEW.practice_id
                  AND member.doctor_id = NEW.signer_doctor_id
                  AND member.role = 'owner'
                  AND member.seat_state = 'active'
                  AND member.access_revoked_at IS NULL
                  AND signer.deleted_at IS NULL
                  AND lower(btrim(signer.email)) = lower(btrim(NEW.signer_email))
             ) THEN
            RAISE EXCEPTION 'public legal acceptance requires the active practice owner'
              USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER public_legal_acceptance_validate_insert
        BEFORE INSERT ON public_legal_acceptance
        FOR EACH ROW EXECUTE FUNCTION cosmas_validate_public_legal_acceptance_insert()
        """
    )
    op.execute(
        """
        CREATE TRIGGER public_legal_acceptance_reject_mutation
        BEFORE UPDATE OR DELETE ON public_legal_acceptance
        FOR EACH ROW EXECUTE FUNCTION cosmas_reject_append_only_mutation()
        """
    )

    op.execute(
        """
        CREATE FUNCTION cosmas_guard_practice_invitation_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'practice invitation evidence is retained'
              USING ERRCODE = '55000';
          END IF;
          IF OLD.accepted_at IS NOT NULL OR OLD.revoked_at IS NOT NULL THEN
            RAISE EXCEPTION 'terminal practice invitations are immutable'
              USING ERRCODE = '55000';
          END IF;
          IF NEW.id IS DISTINCT FROM OLD.id
             OR NEW.created_at IS DISTINCT FROM OLD.created_at
             OR NEW.practice_id IS DISTINCT FROM OLD.practice_id
             OR NEW.invited_by_doctor_id IS DISTINCT FROM OLD.invited_by_doctor_id
             OR NEW.role IS DISTINCT FROM OLD.role
             OR NEW.token_digest IS DISTINCT FROM OLD.token_digest
             OR NEW.invitee_email IS DISTINCT FROM OLD.invitee_email
             OR NEW.invitee_email_digest IS DISTINCT FROM OLD.invitee_email_digest
             OR NEW.idempotency_key_digest IS DISTINCT FROM OLD.idempotency_key_digest
             OR NEW.issued_at IS DISTINCT FROM OLD.issued_at
             OR NEW.expires_at IS DISTINCT FROM OLD.expires_at THEN
            RAISE EXCEPTION 'practice invitation identity is immutable'
              USING ERRCODE = '55000';
          END IF;
          IF (NEW.accepted_at IS NOT NULL AND NEW.accepted_by_doctor_id IS NOT NULL
              AND NEW.revoked_at IS NULL AND NEW.revoked_by_doctor_id IS NULL
              AND NEW.revocation_reason IS NULL)
             OR (NEW.accepted_at IS NULL AND NEW.accepted_by_doctor_id IS NULL
                 AND NEW.revoked_at IS NOT NULL
                 AND NEW.revoked_by_doctor_id IS NOT NULL
                 AND NEW.revocation_reason IS NOT NULL) THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'practice invitation permits one terminal transition'
            USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER practice_invitation_guard_mutation
        BEFORE UPDATE OR DELETE ON practice_invitation
        FOR EACH ROW EXECUTE FUNCTION cosmas_guard_practice_invitation_mutation()
        """
    )


def downgrade() -> None:
    _refuse_populated_downgrade()
    op.execute(
        "DROP TRIGGER IF EXISTS practice_invitation_guard_mutation "
        "ON practice_invitation"
    )
    op.execute("DROP FUNCTION IF EXISTS cosmas_guard_practice_invitation_mutation()")
    op.execute(
        "DROP TRIGGER IF EXISTS public_legal_acceptance_validate_insert "
        "ON public_legal_acceptance"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS cosmas_validate_public_legal_acceptance_insert()"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS public_legal_acceptance_reject_mutation "
        "ON public_legal_acceptance"
    )

    op.drop_index(
        "practice_invitation_expires_at_idx", table_name="practice_invitation"
    )
    op.drop_index(
        "practice_invitation_practice_email_idx", table_name="practice_invitation"
    )
    op.drop_table("practice_invitation")
    op.drop_index(
        "public_legal_acceptance_practice_kind_idx",
        table_name="public_legal_acceptance",
    )
    op.drop_table("public_legal_acceptance")

    op.drop_constraint(
        "ck_practice_public_self_service_no_npi",
        "practice",
        type_="check",
    )
    op.drop_constraint(
        "ck_practice_acquisition_source",
        "practice",
        type_="check",
    )
    op.drop_column("practice", "public_pilot_cohort")
    op.drop_column("practice", "acquisition_source")
