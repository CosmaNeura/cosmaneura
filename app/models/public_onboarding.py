"""Durable, non-PHI evidence for public practice onboarding.

Legal acceptances and credential review events are append-only. Invitations
persist only a SHA-256 digest of bearer material; the normalized invitee email
is retained so an authorized practice owner can safely identify and revoke an
invitation after a reload. Their database guard permits exactly one accepted
or revoked transition.
"""

from datetime import datetime
from enum import Enum

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field

from .doctor import Doctor
from .practice import CredentialState, Practice, PracticeMember


class LegalDocumentKind(str, Enum):
    terms = "terms"
    privacy = "privacy"


class PublicLegalAcceptance(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("public_legal_acceptance"),
    table=True,
):
    """Append-only acceptance of one exact public legal document edition."""

    __table_args__ = (
        sa.UniqueConstraint(
            "practice_id",
            "document_kind",
            "document_version",
            "content_sha256",
            name="uq_public_legal_acceptance_document",
        ),
        sa.CheckConstraint(
            "document_kind IN ('terms', 'privacy')",
            name="ck_public_legal_acceptance_document_kind",
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_public_legal_acceptance_content_sha256",
        ),
        sa.CheckConstraint(
            "signer_role = 'owner'",
            name="ck_public_legal_acceptance_signer_role",
        ),
        sa.CheckConstraint(
            "char_length(btrim(signature_name)) > 0 "
            "AND char_length(btrim(signature_title)) > 0 "
            "AND authority_confirmed IS TRUE "
            "AND char_length(btrim(signer_authority)) > 0",
            name="ck_public_legal_acceptance_signer_authority",
        ),
        sa.Index(
            "public_legal_acceptance_practice_kind_idx",
            "practice_id",
            "document_kind",
        ),
    )

    practice_id: TypeIDType = Practice.foreign_key()
    document_kind: str = Field(max_length=16)
    document_version: str = Field(max_length=64)
    content_sha256: str = Field(min_length=64, max_length=64)
    signer_doctor_id: TypeIDType = Doctor.foreign_key()
    signer_email: str = Field(max_length=320)
    signer_role: str = Field(max_length=16)
    signature_name: str = Field(max_length=120)
    signature_title: str = Field(max_length=120)
    authority_confirmed: bool
    signer_authority: str = Field(max_length=255)
    accepted_at_utc: datetime = Field(
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )


class CredentialReviewAudit(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("credential_review_audit"),
    table=True,
):
    """Append-only evidence for one independent credential-state transition."""

    __table_args__ = (
        sa.CheckConstraint(
            "from_state IN ('pending', 'approved', 'rejected', 'needs_review')",
            name="ck_credential_review_audit_from_state",
        ),
        sa.CheckConstraint(
            "to_state IN ('approved', 'rejected', 'needs_review')",
            name="ck_credential_review_audit_to_state",
        ),
        sa.CheckConstraint(
            "from_state <> to_state",
            name="ck_credential_review_audit_state_changed",
        ),
        sa.CheckConstraint(
            "evidence_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_credential_review_audit_evidence_sha256",
        ),
        sa.CheckConstraint(
            "event_digest ~ '^[0-9a-f]{64}$'",
            name="ck_credential_review_audit_event_digest",
        ),
        sa.CheckConstraint(
            "char_length(btrim(reviewer_ref)) > 0 "
            "AND char_length(btrim(verification_method)) > 0 "
            "AND char_length(btrim(reason_code)) > 0",
            name="ck_credential_review_audit_review_evidence",
        ),
        sa.Index(
            "credential_review_audit_member_time_idx",
            "practice_member_id",
            "reviewed_at",
        ),
    )

    practice_member_id: TypeIDType = PracticeMember.foreign_key()
    from_state: str = Field(max_length=16)
    to_state: str = Field(max_length=16)
    reviewer_ref: str = Field(max_length=255)
    reviewer_doctor_id: TypeIDType | None = Field(
        default=None,
        sa_type=TypeIDType("doctor"),  # type: ignore
        foreign_key="doctor.id",
    )
    verification_method: str = Field(max_length=128)
    evidence_sha256: str = Field(min_length=64, max_length=64)
    reason_code: str = Field(max_length=128)
    reviewed_at: datetime = Field(
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    event_digest: str = Field(min_length=64, max_length=64, unique=True)


class PracticeInvitation(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("practice_invitation"),
    table=True,
):
    """Hashed, single-use invitation with immutable terminal evidence."""

    __table_args__ = (
        sa.UniqueConstraint(
            "practice_id",
            "idempotency_key_digest",
            name="uq_practice_invitation_idempotency_key",
        ),
        sa.CheckConstraint(
            "token_digest ~ '^[0-9a-f]{64}$'",
            name="ck_practice_invitation_token_digest",
        ),
        sa.CheckConstraint(
            "invitee_email = lower(btrim(invitee_email)) "
            "AND position('@' IN invitee_email) > 1",
            name="ck_practice_invitation_normalized_email",
        ),
        sa.CheckConstraint(
            "invitee_email_digest ~ '^[0-9a-f]{64}$'",
            name="ck_practice_invitation_email_digest",
        ),
        sa.CheckConstraint(
            "idempotency_key_digest ~ '^[0-9a-f]{64}$'",
            name="ck_practice_invitation_idempotency_key_digest",
        ),
        sa.CheckConstraint(
            "role = 'workforce'",
            name="ck_practice_invitation_role",
        ),
        sa.CheckConstraint(
            "expires_at > issued_at",
            name="ck_practice_invitation_expiry",
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
            name="ck_practice_invitation_terminal_state",
        ),
        sa.CheckConstraint(
            "accepted_by_doctor_id IS NULL "
            "OR accepted_by_doctor_id <> invited_by_doctor_id",
            name="ck_practice_invitation_distinct_acceptor",
        ),
        sa.Index(
            "practice_invitation_practice_email_idx",
            "practice_id",
            "invitee_email_digest",
        ),
        sa.Index("practice_invitation_expires_at_idx", "expires_at"),
    )

    practice_id: TypeIDType = Practice.foreign_key()
    invited_by_doctor_id: TypeIDType = Field(
        sa_type=TypeIDType("doctor"),  # type: ignore
        foreign_key="doctor.id",
    )
    role: str = Field(default="workforce", max_length=16)
    token_digest: str = Field(min_length=64, max_length=64, unique=True)
    invitee_email: str = Field(max_length=320)
    invitee_email_digest: str = Field(min_length=64, max_length=64)
    idempotency_key_digest: str = Field(min_length=64, max_length=64)
    issued_at: datetime = Field(
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    expires_at: datetime = Field(
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    accepted_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    accepted_by_doctor_id: TypeIDType | None = Field(
        default=None,
        sa_type=TypeIDType("doctor"),  # type: ignore
        foreign_key="doctor.id",
    )
    revoked_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    revoked_by_doctor_id: TypeIDType | None = Field(
        default=None,
        sa_type=TypeIDType("doctor"),  # type: ignore
        foreign_key="doctor.id",
    )
    revocation_reason: str | None = Field(
        default=None,
        sa_column=Column(sa.String(length=512), nullable=True),
    )


__all__ = [
    "CredentialReviewAudit",
    "CredentialState",
    "LegalDocumentKind",
    "PracticeInvitation",
    "PublicLegalAcceptance",
]
