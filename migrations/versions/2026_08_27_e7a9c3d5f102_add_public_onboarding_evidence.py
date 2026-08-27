"""Add durable public onboarding and credential evidence.

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
    """Never discard public onboarding or credential evidence silently."""

    if op.get_context().as_sql:
        raise RuntimeError(
            "public onboarding evidence downgrade is unsupported in offline mode"
        )
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "LOCK TABLE public_legal_acceptance, credential_review_audit, "
            "practice_invitation, practice, practice_member "
            "IN ACCESS EXCLUSIVE MODE"
        )
    )
    counts = {
        "legal_acceptances": connection.execute(
            sa.text("SELECT count(*) FROM public_legal_acceptance")
        ).scalar_one(),
        "credential_reviews": connection.execute(
            sa.text("SELECT count(*) FROM credential_review_audit")
        ).scalar_one(),
        "practice_invitations": connection.execute(
            sa.text("SELECT count(*) FROM practice_invitation")
        ).scalar_one(),
        "public_practices": connection.execute(
            sa.text(
                "SELECT count(*) FROM practice "
                "WHERE public_onboarding_required IS TRUE "
                "OR public_pilot_cohort IS TRUE"
            )
        ).scalar_one(),
        "credential_decisions": connection.execute(
            sa.text(
                "SELECT count(*) FROM practice_member "
                "WHERE credential_state <> 'pending' "
                "OR credential_reviewed_at IS NOT NULL "
                "OR credential_reviewer_ref IS NOT NULL "
                "OR credential_verification_method IS NOT NULL "
                "OR credential_evidence_sha256 IS NOT NULL"
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
            "public_onboarding_required",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
            comment="Whether public legal and credential gates apply.",
        ),
    )
    op.add_column(
        "practice",
        sa.Column(
            "public_pilot_cohort",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
            comment="Explicit public-pilot rollout cohort membership.",
        ),
    )

    op.add_column(
        "practice_member",
        sa.Column(
            "credential_state",
            sa.String(),
            server_default="pending",
            nullable=False,
            comment="Fail-closed public clinician credential state.",
        ),
    )
    op.add_column(
        "practice_member",
        sa.Column("credential_reviewed_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "practice_member",
        sa.Column("credential_reviewer_ref", sa.String(length=255)),
    )
    op.add_column(
        "practice_member",
        sa.Column("credential_verification_method", sa.String(length=128)),
    )
    op.add_column(
        "practice_member",
        sa.Column("credential_evidence_sha256", sa.String(length=64)),
    )
    op.create_check_constraint(
        "ck_practice_member_credential_state",
        "practice_member",
        "credential_state IN ('pending', 'approved', 'rejected', 'needs_review')",
    )
    op.create_check_constraint(
        "ck_practice_member_credential_evidence_complete",
        "practice_member",
        "(credential_state = 'pending' "
        "AND credential_reviewed_at IS NULL "
        "AND credential_reviewer_ref IS NULL "
        "AND credential_verification_method IS NULL "
        "AND credential_evidence_sha256 IS NULL) OR "
        "(credential_state <> 'pending' "
        "AND credential_reviewed_at IS NOT NULL "
        "AND credential_reviewer_ref IS NOT NULL "
        "AND credential_verification_method IS NOT NULL "
        "AND credential_evidence_sha256 IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_practice_member_credential_evidence_sha256",
        "practice_member",
        "credential_evidence_sha256 IS NULL OR "
        "credential_evidence_sha256 ~ '^[0-9a-f]{64}$'",
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
        comment="Append-only public Terms or Privacy acceptance evidence.",
    )
    op.create_index(
        "public_legal_acceptance_practice_kind_idx",
        "public_legal_acceptance",
        ["practice_id", "document_kind"],
    )

    op.create_table(
        "credential_review_audit",
        _id("credential_review_audit"),
        *_timestamps(),
        _typeid("practice_member_id", "practice_member"),
        sa.Column("from_state", sa.String(length=16), nullable=False),
        sa.Column("to_state", sa.String(length=16), nullable=False),
        sa.Column("reviewer_ref", sa.String(length=255), nullable=False),
        _typeid("reviewer_doctor_id", "doctor", nullable=True),
        sa.Column("verification_method", sa.String(length=128), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_digest", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "from_state IN ('pending', 'approved', 'rejected', 'needs_review')",
            name=op.f(
                "credential_review_audit_ck_credential_review_audit_from_state"
            ),
        ),
        sa.CheckConstraint(
            "to_state IN ('approved', 'rejected', 'needs_review')",
            name=op.f(
                "credential_review_audit_ck_credential_review_audit_to_state"
            ),
        ),
        sa.CheckConstraint(
            "from_state <> to_state",
            name=op.f(
                "credential_review_audit_ck_credential_review_audit_state_changed"
            ),
        ),
        sa.CheckConstraint(
            "evidence_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f(
                "credential_review_audit_ck_credential_review_audit_evidence_sha256"
            ),
        ),
        sa.CheckConstraint(
            "event_digest ~ '^[0-9a-f]{64}$'",
            name=op.f(
                "credential_review_audit_ck_credential_review_audit_event_digest"
            ),
        ),
        sa.CheckConstraint(
            "char_length(btrim(reviewer_ref)) > 0 "
            "AND char_length(btrim(verification_method)) > 0 "
            "AND char_length(btrim(reason_code)) > 0",
            name=op.f(
                "credential_review_audit_ck_credential_review_audit_review_evidence"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["practice_member_id"],
            ["practice_member.id"],
            name=op.f("credential_review_audit_practice_member_id_fkey"),
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_doctor_id"],
            ["doctor.id"],
            name=op.f("credential_review_audit_reviewer_doctor_id_fkey"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("credential_review_audit_pkey")),
        sa.UniqueConstraint(
            "event_digest",
            name=op.f("credential_review_audit_event_digest_key"),
        ),
        comment="Append-only evidence for credential-state transitions.",
    )
    op.create_index(
        "credential_review_audit_member_time_idx",
        "credential_review_audit",
        ["practice_member_id", "reviewed_at"],
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
            name=op.f(
                "practice_invitation_ck_practice_invitation_token_digest"
            ),
        ),
        sa.CheckConstraint(
            "invitee_email = lower(btrim(invitee_email)) "
            "AND position('@' IN invitee_email) > 1",
            name=op.f(
                "practice_invitation_ck_practice_invitation_normalized_email"
            ),
        ),
        sa.CheckConstraint(
            "invitee_email_digest ~ '^[0-9a-f]{64}$'",
            name=op.f(
                "practice_invitation_ck_practice_invitation_email_digest"
            ),
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
            name=op.f(
                "practice_invitation_ck_practice_invitation_terminal_state"
            ),
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
        comment="Hashed, single-use public practice invitation.",
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
        CREATE FUNCTION cosmas_guard_credential_member_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.credential_state IS DISTINCT FROM 'pending'
             OR NEW.credential_reviewed_at IS NOT NULL
             OR NEW.credential_reviewer_ref IS NOT NULL
             OR NEW.credential_verification_method IS NOT NULL
             OR NEW.credential_evidence_sha256 IS NOT NULL THEN
            RAISE EXCEPTION 'new memberships must begin with pending credentials'
              USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER practice_member_guard_credential_insert
        BEFORE INSERT ON practice_member
        FOR EACH ROW EXECUTE FUNCTION cosmas_guard_credential_member_insert()
        """
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
                 JOIN practice tenant ON tenant.id = member.practice_id
                WHERE member.practice_id = NEW.practice_id
                  AND member.doctor_id = NEW.signer_doctor_id
                  AND member.role = 'owner'
                  AND member.seat_state = 'active'
                  AND member.access_revoked_at IS NULL
                  AND signer.deleted_at IS NULL
                  AND lower(btrim(signer.email)) = lower(btrim(NEW.signer_email))
                  AND tenant.public_onboarding_required IS TRUE
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
        CREATE TRIGGER credential_review_audit_reject_mutation
        BEFORE UPDATE OR DELETE ON credential_review_audit
        FOR EACH ROW EXECUTE FUNCTION cosmas_reject_append_only_mutation()
        """
    )

    op.execute(
        """
        CREATE FUNCTION cosmas_validate_credential_review_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
          current_state text;
          subject_doctor_id uuid;
        BEGIN
          SELECT credential_state, doctor_id
            INTO current_state, subject_doctor_id
            FROM practice_member
           WHERE id = NEW.practice_member_id;
          IF NOT FOUND OR current_state IS DISTINCT FROM NEW.from_state THEN
            RAISE EXCEPTION 'credential review does not match current state'
              USING ERRCODE = '55000';
          END IF;
          IF NEW.reviewer_doctor_id IS NOT NULL
             AND NEW.reviewer_doctor_id = subject_doctor_id THEN
            RAISE EXCEPTION 'clinicians cannot approve their own credentials'
              USING ERRCODE = '55000';
          END IF;
          -- Reviewer identity, verification method, and SLA are deliberately
          -- undecided for this launch slice. Keep every transition closed at
          -- the persistence boundary until a later migration installs the
          -- approved reviewer-authority policy.
          RAISE EXCEPTION 'credential reviewer authorization is not configured'
            USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER credential_review_audit_validate_insert
        BEFORE INSERT ON credential_review_audit
        FOR EACH ROW EXECUTE FUNCTION cosmas_validate_credential_review_insert()
        """
    )
    op.execute(
        """
        CREATE FUNCTION cosmas_guard_credential_member_transition()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.credential_state IS NOT DISTINCT FROM OLD.credential_state
             AND NEW.credential_reviewed_at IS NOT DISTINCT FROM OLD.credential_reviewed_at
             AND NEW.credential_reviewer_ref IS NOT DISTINCT FROM OLD.credential_reviewer_ref
             AND NEW.credential_verification_method IS NOT DISTINCT FROM OLD.credential_verification_method
             AND NEW.credential_evidence_sha256 IS NOT DISTINCT FROM OLD.credential_evidence_sha256 THEN
            RETURN NEW;
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM credential_review_audit audit
             WHERE audit.practice_member_id = OLD.id
               AND audit.from_state = OLD.credential_state
               AND audit.to_state = NEW.credential_state
               AND audit.reviewed_at = NEW.credential_reviewed_at
               AND audit.reviewer_ref = NEW.credential_reviewer_ref
               AND audit.verification_method = NEW.credential_verification_method
               AND audit.evidence_sha256 = NEW.credential_evidence_sha256
          ) THEN
            RAISE EXCEPTION 'credential transition requires matching audit evidence'
              USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER practice_member_guard_credential_transition
        BEFORE UPDATE ON practice_member
        FOR EACH ROW EXECUTE FUNCTION cosmas_guard_credential_member_transition()
        """
    )
    op.execute(
        """
        CREATE FUNCTION cosmas_validate_credential_review_committed()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM practice_member member
             WHERE member.id = NEW.practice_member_id
               AND member.credential_state = NEW.to_state
               AND member.credential_reviewed_at = NEW.reviewed_at
               AND member.credential_reviewer_ref = NEW.reviewer_ref
               AND member.credential_verification_method = NEW.verification_method
               AND member.credential_evidence_sha256 = NEW.evidence_sha256
          ) THEN
            RAISE EXCEPTION 'credential review was not applied to membership'
              USING ERRCODE = '55000';
          END IF;
          RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER credential_review_audit_validate_commit
        AFTER INSERT ON credential_review_audit
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION cosmas_validate_credential_review_committed()
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
        "DROP TRIGGER IF EXISTS credential_review_audit_validate_commit "
        "ON credential_review_audit"
    )
    op.execute("DROP FUNCTION IF EXISTS cosmas_validate_credential_review_committed()")
    op.execute(
        "DROP TRIGGER IF EXISTS practice_member_guard_credential_transition "
        "ON practice_member"
    )
    op.execute("DROP FUNCTION IF EXISTS cosmas_guard_credential_member_transition()")
    op.execute(
        "DROP TRIGGER IF EXISTS credential_review_audit_validate_insert "
        "ON credential_review_audit"
    )
    op.execute("DROP FUNCTION IF EXISTS cosmas_validate_credential_review_insert()")
    op.execute(
        "DROP TRIGGER IF EXISTS public_legal_acceptance_validate_insert "
        "ON public_legal_acceptance"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS cosmas_validate_public_legal_acceptance_insert()"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS practice_member_guard_credential_insert "
        "ON practice_member"
    )
    op.execute("DROP FUNCTION IF EXISTS cosmas_guard_credential_member_insert()")
    op.execute(
        "DROP TRIGGER IF EXISTS credential_review_audit_reject_mutation "
        "ON credential_review_audit"
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
        "credential_review_audit_member_time_idx",
        table_name="credential_review_audit",
    )
    op.drop_table("credential_review_audit")
    op.drop_index(
        "public_legal_acceptance_practice_kind_idx",
        table_name="public_legal_acceptance",
    )
    op.drop_table("public_legal_acceptance")

    op.drop_constraint(
        "ck_practice_member_credential_evidence_sha256",
        "practice_member",
        type_="check",
    )
    op.drop_constraint(
        "ck_practice_member_credential_evidence_complete",
        "practice_member",
        type_="check",
    )
    op.drop_constraint(
        "ck_practice_member_credential_state",
        "practice_member",
        type_="check",
    )
    op.drop_column("practice_member", "credential_evidence_sha256")
    op.drop_column("practice_member", "credential_verification_method")
    op.drop_column("practice_member", "credential_reviewer_ref")
    op.drop_column("practice_member", "credential_reviewed_at")
    op.drop_column("practice_member", "credential_state")
    op.drop_column("practice", "public_pilot_cohort")
    op.drop_column("practice", "public_onboarding_required")
