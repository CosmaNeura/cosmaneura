"""Durable, per-Clerk-account evidence for legacy pilot access.

The grant is deliberately independent of practice membership, BAA execution,
and practice billing state.  A nullable Doctor/practice association records
what existed when the operator applied the reviewed plan; authorization is
always rooted in the stable Clerk subject and the approved email snapshot.
"""

from datetime import datetime

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field


class PilotAccessGrandfatherGrant(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("pilot_access_grant"),
    table=True,
):
    """One revocable legacy access grant and its retained audit evidence."""

    __table_args__ = (
        sa.CheckConstraint(
            "left(clerk_id, 5) = 'user_'",
            name="ck_pilot_access_grant_clerk_subject",
        ),
        sa.CheckConstraint(
            "email_snapshot = lower(btrim(email_snapshot)) "
            "AND position('@' in email_snapshot) > 1",
            name="ck_pilot_access_grant_email_normalized",
        ),
        sa.CheckConstraint(
            "email_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_pilot_access_grant_email_sha256",
        ),
        sa.CheckConstraint(
            "grant_plan_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_pilot_access_grant_plan_sha256",
        ),
        sa.CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_pilot_access_grant_source_sha256",
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
            name="ck_pilot_access_grant_revocation_complete",
        ),
        sa.Index(
            "uq_pilot_access_grant_active_clerk",
            "clerk_id",
            unique=True,
            postgresql_where=sa.text("revoked_at IS NULL"),
        ),
        sa.Index(
            "uq_pilot_access_grant_active_doctor",
            "doctor_id_snapshot",
            unique=True,
            postgresql_where=sa.text(
                "revoked_at IS NULL AND doctor_id_snapshot IS NOT NULL"
            ),
        ),
        sa.Index(
            "uq_pilot_access_grant_active_email_sha256",
            "email_sha256",
            unique=True,
            postgresql_where=sa.text("revoked_at IS NULL"),
        ),
        sa.Index(
            "pilot_access_grant_retain_until_idx",
            "retain_until",
        ),
    )

    clerk_id: str = Field(max_length=255)
    doctor_id_snapshot: TypeIDType | None = Field(
        default=None,
        sa_column=Column(
            TypeIDType("doctor"),
            sa.ForeignKey("doctor.id"),
            nullable=True,
        ),
    )
    practice_id_snapshot: TypeIDType | None = Field(
        default=None,
        sa_column=Column(
            TypeIDType("practice"),
            sa.ForeignKey("practice.id"),
            nullable=True,
        ),
    )

    email_snapshot: str = Field(max_length=320)
    email_sha256: str = Field(min_length=64, max_length=64)
    source_ref: str = Field(max_length=255)
    source_sha256: str = Field(min_length=64, max_length=64)
    grant_plan_sha256: str = Field(min_length=64, max_length=64)
    granted_by_ref: str = Field(max_length=255)
    grant_reason: str = Field(
        sa_column=Column(sa.String(length=512), nullable=False),
    )
    granted_at: datetime = Field(
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )

    revoked_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    revoked_by_ref: str | None = Field(default=None, max_length=255)
    revocation_reason: str | None = Field(
        default=None,
        sa_column=Column(sa.String(length=512), nullable=True),
    )
    revocation_plan_sha256: str | None = Field(
        default=None,
        sa_column=Column(sa.String(length=64), nullable=True),
    )
    retain_until: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
