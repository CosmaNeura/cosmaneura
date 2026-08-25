"""Environment-scoped, identity-bound superadmin capability evidence.

The grant row is the revocable authorization state.  The companion audit row
is append-only and deliberately contains only opaque references and digests;
neither model stores an email address, token, or clinical data.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field


class SuperadminCapabilityEnvironment(str, Enum):
    staging = "staging"
    production = "production"


class SuperadminCapabilityKind(str, Enum):
    platform_admin = "platform_admin"
    view_as = "view_as"


class SuperadminCapabilityAuditAction(str, Enum):
    granted = "granted"
    revoked = "revoked"


class SuperadminCapabilityGrant(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("superadmin_capability"),
    table=True,
):
    """One independently revocable capability for one immutable identity."""

    __table_args__ = (
        sa.CheckConstraint(
            "environment IN ('staging', 'production')",
            name="ck_superadmin_capability_grant_environment",
        ),
        sa.CheckConstraint(
            "capability_kind IN ('platform_admin', 'view_as')",
            name="ck_superadmin_capability_grant_kind",
        ),
        sa.CheckConstraint(
            "left(clerk_issuer, 8) = 'https://' AND right(clerk_issuer, 1) <> '/'",
            name="ck_superadmin_capability_grant_issuer",
        ),
        sa.CheckConstraint(
            "left(clerk_subject, 5) = 'user_'",
            name="ck_superadmin_capability_grant_subject",
        ),
        sa.CheckConstraint(
            "issuer_sha256 ~ '^[0-9a-f]{64}$' "
            "AND subject_sha256 ~ '^[0-9a-f]{64}$' "
            "AND verified_primary_email_sha256 ~ '^[0-9a-f]{64}$' "
            "AND source_sha256 ~ '^[0-9a-f]{64}$' "
            "AND grant_plan_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_superadmin_capability_grant_digests",
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
            name="ck_superadmin_capability_grant_revocation_complete",
        ),
        sa.Index(
            "uq_superadmin_capability_active_identity_kind",
            "environment",
            "capability_kind",
            "clerk_issuer",
            "clerk_subject",
            unique=True,
            postgresql_where=sa.text("revoked_at IS NULL"),
        ),
        sa.Index(
            "uq_superadmin_capability_active_doctor_kind",
            "environment",
            "capability_kind",
            "doctor_id",
            unique=True,
            postgresql_where=sa.text("revoked_at IS NULL"),
        ),
        sa.Index(
            "superadmin_capability_grant_retain_until_idx",
            "retain_until",
        ),
    )

    environment: SuperadminCapabilityEnvironment = Field(
        sa_column=Column(sa.String(length=16), nullable=False),
    )
    capability_kind: SuperadminCapabilityKind = Field(
        sa_column=Column(sa.String(length=32), nullable=False),
    )
    clerk_issuer: str = Field(max_length=255)
    clerk_subject: str = Field(max_length=255)
    doctor_id: TypeIDType = Field(
        sa_column=Column(
            TypeIDType("doctor"),
            sa.ForeignKey("doctor.id", ondelete="RESTRICT"),
            nullable=False,
        ),
    )

    issuer_sha256: str = Field(min_length=64, max_length=64)
    subject_sha256: str = Field(min_length=64, max_length=64)
    verified_primary_email_sha256: str = Field(min_length=64, max_length=64)
    source_ref: str = Field(max_length=255)
    source_sha256: str = Field(min_length=64, max_length=64)
    grant_plan_sha256: str = Field(min_length=64, max_length=64)
    granted_by_ref: str = Field(max_length=255)
    grant_reason_code: str = Field(max_length=64)
    granted_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore

    revoked_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    revoked_by_ref: str | None = Field(default=None, max_length=255)
    revocation_reason_code: str | None = Field(default=None, max_length=64)
    revocation_plan_sha256: str | None = Field(default=None, max_length=64)
    retain_until: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )


class SuperadminCapabilityAudit(
    BaseModel,
    TypeIDMixin("superadmin_capability_audit"),
    table=True,
):
    """Append-only, value-minimized evidence for a capability transition."""

    __table_args__ = (
        sa.CheckConstraint(
            "action IN ('granted', 'revoked')",
            name="ck_superadmin_capability_audit_action",
        ),
        sa.CheckConstraint(
            "environment IN ('staging', 'production')",
            name="ck_superadmin_capability_audit_environment",
        ),
        sa.CheckConstraint(
            "capability_kind IN ('platform_admin', 'view_as')",
            name="ck_superadmin_capability_audit_kind",
        ),
        sa.CheckConstraint(
            "identity_binding_sha256 ~ '^[0-9a-f]{64}$' "
            "AND evidence_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_superadmin_capability_audit_digests",
        ),
        sa.UniqueConstraint(
            "grant_id",
            "action",
            name="uq_superadmin_capability_audit_grant_action",
        ),
    )

    grant_id: TypeIDType = Field(
        sa_column=Column(
            TypeIDType("superadmin_capability"),
            sa.ForeignKey("superadmin_capability_grant.id", ondelete="RESTRICT"),
            nullable=False,
        ),
    )
    action: SuperadminCapabilityAuditAction = Field(
        sa_column=Column(sa.String(length=16), nullable=False),
    )
    environment: SuperadminCapabilityEnvironment = Field(
        sa_column=Column(sa.String(length=16), nullable=False),
    )
    capability_kind: SuperadminCapabilityKind = Field(
        sa_column=Column(sa.String(length=32), nullable=False),
    )
    identity_binding_sha256: str = Field(min_length=64, max_length=64)
    evidence_sha256: str = Field(min_length=64, max_length=64)
    operator_ref: str = Field(max_length=255)
    reason_code: str = Field(max_length=64)
    occurred_at: datetime = Field(
        index=True,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    recorded_at: datetime = Field(
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )


@sa.event.listens_for(SuperadminCapabilityAudit, "before_update")
def _prevent_superadmin_capability_audit_update(*_: object) -> None:
    raise RuntimeError("Superadmin capability audit events are append-only.")


@sa.event.listens_for(SuperadminCapabilityAudit, "before_delete")
def _prevent_superadmin_capability_audit_delete(*_: object) -> None:
    raise RuntimeError("Superadmin capability audit events are append-only.")
