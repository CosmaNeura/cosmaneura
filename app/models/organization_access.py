"""Organization-level pilot access and exact roster approvals.

The organization access code is a shared enrollment secret, not an
authorization record.  Only its scrypt salt and derived digest are persisted;
an authenticated provider must also match an active exact-email roster row.
"""

from datetime import datetime

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field


class ProviderOrganization(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("provider_organization"),
    table=True,
):
    """An organization above one or more practice-level access tenants."""

    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('active', 'suspended')",
            name="ck_provider_organization_status",
        ),
        sa.CheckConstraint(
            "email_domain = lower(btrim(email_domain)) "
            "AND position('@' in email_domain) = 0",
            name="ck_provider_organization_email_domain_normalized",
        ),
        sa.CheckConstraint(
            "char_length(access_code_salt) = 32",
            name="ck_provider_organization_access_code_salt_length",
        ),
        sa.CheckConstraint(
            "char_length(access_code_digest) = 64",
            name="ck_provider_organization_access_code_digest_length",
        ),
        sa.CheckConstraint(
            "access_code_version > 0",
            name="ck_provider_organization_access_code_version_positive",
        ),
    )

    legal_name: str = Field(max_length=160)
    slug: str = Field(unique=True, index=True, max_length=64)
    email_domain: str = Field(unique=True, index=True, max_length=253)
    status: str = Field(default="active", index=True, max_length=16)

    access_code_salt: str = Field(min_length=32, max_length=32)
    access_code_digest: str = Field(
        unique=True,
        min_length=64,
        max_length=64,
    )
    access_code_version: int = Field(default=1)
    access_code_created_at: datetime = Field(
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )


class OrganizationRosterApproval(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("organization_roster_approval"),
    table=True,
):
    """One exact-email organization approval and its access lifecycle.

    This row is authorization provenance, not a workforce attestation.  Claim
    and revocation mutate its lifecycle fields only through privileged service
    code; append-only operator evidence belongs in ``PracticeAccessAudit``.
    """

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["target_practice_id", "organization_id"],
            ["practice.id", "practice.organization_id"],
            name="fk_org_roster_approval_practice_organization",
        ),
        sa.CheckConstraint(
            "approved_email = lower(btrim(approved_email))",
            name="ck_org_roster_approval_email_normalized",
        ),
        sa.CheckConstraint(
            "position('@' in approved_email) > 1",
            name="ck_org_roster_approval_email_shape",
        ),
        sa.CheckConstraint(
            "char_length(source_sha256) = 64",
            name="ck_org_roster_approval_source_sha256_length",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > approved_at",
            name="ck_org_roster_approval_expiry_after_approval",
        ),
        sa.CheckConstraint(
            "(claimed_doctor_id IS NULL AND claimed_at IS NULL "
            "AND claimed_code_version IS NULL) OR "
            "(claimed_doctor_id IS NOT NULL AND claimed_at IS NOT NULL "
            "AND claimed_code_version IS NOT NULL "
            "AND claimed_code_version > 0)",
            name="ck_org_roster_approval_claim_complete",
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL AND revoked_by_ref IS NULL "
            "AND revocation_reason IS NULL AND retain_until IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_by_ref IS NOT NULL "
            "AND revocation_reason IS NOT NULL AND retain_until IS NOT NULL "
            "AND retain_until >= revoked_at + interval '7 years')",
            name="ck_org_roster_approval_revocation_complete",
        ),
        sa.Index(
            "uq_org_roster_approval_active_email",
            "approved_email",
            unique=True,
            postgresql_where=sa.text("revoked_at IS NULL"),
        ),
        sa.Index(
            "uq_org_roster_approval_active_claimed_doctor",
            "claimed_doctor_id",
            unique=True,
            postgresql_where=sa.text(
                "revoked_at IS NULL AND claimed_doctor_id IS NOT NULL"
            ),
        ),
        sa.Index(
            "organization_roster_approval_organization_id_idx",
            "organization_id",
        ),
        sa.Index(
            "organization_roster_approval_target_practice_id_idx",
            "target_practice_id",
        ),
        sa.Index(
            "organization_roster_approval_retain_until_idx",
            "retain_until",
        ),
    )

    organization_id: TypeIDType = ProviderOrganization.foreign_key()
    target_practice_id: TypeIDType = Field(
        sa_type=TypeIDType("practice"),  # type: ignore
    )
    approved_email: str = Field(max_length=320)

    approved_at: datetime = Field(
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    approved_by_ref: str = Field(max_length=255)
    source_ref: str = Field(max_length=255)
    source_sha256: str = Field(min_length=64, max_length=64)
    expires_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )

    claimed_doctor_id: TypeIDType | None = Field(
        default=None,
        sa_type=TypeIDType("doctor"),  # type: ignore
        foreign_key="doctor.id",
    )
    claimed_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    claimed_code_version: int | None = None

    revoked_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    revoked_by_ref: str | None = Field(default=None, max_length=255)
    revocation_reason: str | None = Field(
        default=None,
        sa_column=Column(sa.String(length=512), nullable=True),
    )
    retain_until: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
