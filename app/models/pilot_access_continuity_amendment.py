"""Append-only evidence for additive legacy-pilot continuity amendments.

The original environment cohort remains a closed 47-production/6-staging
record.  A separately reviewed omission is represented by one amendment batch
and one identity-evidence row per newly issued grandfather grant.  These rows
record authorization provenance only; they never create a Doctor, practice
membership, BAA acceptance, entitlement, or onboarding state.
"""

from datetime import datetime

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field


class PilotAccessContinuityAmendment(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("pilot_access_amendment"),
    table=True,
):
    """One hash-confirmed, environment-bound omission amendment batch."""

    __table_args__ = (
        sa.CheckConstraint(
            "environment IN ('staging', 'production')",
            name="ck_pilot_access_amendment_environment",
        ),
        sa.CheckConstraint(
            "clerk_issuer = btrim(clerk_issuer) "
            "AND clerk_issuer LIKE 'https://%' "
            "AND right(clerk_issuer, 1) <> '/'",
            name="ck_pilot_access_amendment_clerk_issuer",
        ),
        sa.CheckConstraint(
            "manifest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_pilot_access_amendment_manifest_sha256",
        ),
        sa.CheckConstraint(
            "plan_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_pilot_access_amendment_plan_sha256",
        ),
        sa.CheckConstraint(
            "account_count > 0",
            name="ck_pilot_access_amendment_account_count",
        ),
    )

    environment: str = Field(max_length=16)
    clerk_issuer: str = Field(max_length=255)
    source_ref: str = Field(max_length=255)
    manifest_sha256: str = Field(min_length=64, max_length=64)
    plan_sha256: str = Field(unique=True, min_length=64, max_length=64)
    applied_by_ref: str = Field(max_length=255)
    reason: str = Field(sa_column=Column(sa.String(length=512), nullable=False))
    account_count: int
    applied_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore


class PilotAccessContinuityAmendmentIdentity(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("pilot_access_amendment_identity"),
    table=True,
):
    """Exact identity evidence for one omission grant."""

    __table_args__ = (
        sa.CheckConstraint(
            "clerk_issuer = btrim(clerk_issuer) "
            "AND clerk_issuer LIKE 'https://%' "
            "AND right(clerk_issuer, 1) <> '/'",
            name="ck_pilot_access_amendment_identity_clerk_issuer",
        ),
        sa.CheckConstraint(
            "left(clerk_subject, 5) = 'user_'",
            name="ck_pilot_access_amendment_identity_clerk_subject",
        ),
        sa.CheckConstraint(
            "email_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_pilot_access_amendment_identity_email_sha256",
        ),
        sa.CheckConstraint(
            "continuity_basis = 'reviewed_operational_user_omission'",
            name="ck_pilot_access_amendment_identity_basis",
        ),
        sa.CheckConstraint(
            "safe_prelogin IS FALSE AND doctor_id_snapshot IS NOT NULL",
            name="ck_pilot_access_amendment_identity_account_state",
        ),
        sa.UniqueConstraint(
            "amendment_id",
            "clerk_subject",
            name="uq_pilot_access_amendment_identity_subject",
        ),
        sa.Index(
            "uq_pilot_access_amendment_identity_issuer_subject",
            "clerk_issuer",
            "clerk_subject",
            unique=True,
        ),
        sa.Index(
            "uq_pilot_access_amendment_identity_email_sha256",
            "email_sha256",
            unique=True,
        ),
        sa.Index(
            "uq_pilot_access_amendment_identity_doctor",
            "doctor_id_snapshot",
            unique=True,
            postgresql_where=sa.text("doctor_id_snapshot IS NOT NULL"),
        ),
    )

    amendment_id: TypeIDType = Field(
        sa_type=TypeIDType("pilot_access_amendment"),  # type: ignore
        foreign_key="pilot_access_continuity_amendment.id",
    )
    grant_id: TypeIDType = Field(
        unique=True,
        sa_type=TypeIDType("pilot_access_grant"),  # type: ignore
        foreign_key="pilot_access_grandfather_grant.id",
    )
    clerk_issuer: str = Field(max_length=255)
    clerk_subject: str = Field(max_length=255)
    email_sha256: str = Field(min_length=64, max_length=64)
    doctor_id_snapshot: TypeIDType = Field(
        sa_type=TypeIDType("doctor"),  # type: ignore
        foreign_key="doctor.id",
    )
    practice_id_snapshot: TypeIDType | None = Field(
        default=None,
        sa_type=TypeIDType("practice"),  # type: ignore
        foreign_key="practice.id",
    )
    safe_prelogin: bool = Field(default=False)
    continuity_basis: str = Field(max_length=64)
