"""Durable, environment-bound support view-as sessions and audit evidence.

The signed browser session carries only an opaque session id and a rotating
nonce.  Authority remains server-side and is rooted in an explicit
``SuperadminCapabilityGrant``.  No Clerk subject, email address, token, or PHI
is copied into the view-as session or its append-only audit trail.
"""

from datetime import datetime

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field


class ViewAsSession(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("view_as_session"),
    table=True,
):
    """One short-lived, server-side actor-to-target support session."""

    __table_args__ = (
        sa.CheckConstraint(
            "environment IN ('staging', 'production')",
            name="ck_view_as_session_environment",
        ),
        sa.CheckConstraint(
            "actor_doctor_id <> target_doctor_id",
            name="ck_view_as_session_distinct_doctors",
        ),
        sa.CheckConstraint(
            "actor_issuer_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_view_as_session_actor_issuer_sha256",
        ),
        sa.CheckConstraint(
            "actor_subject_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_view_as_session_actor_subject_sha256",
        ),
        sa.CheckConstraint(
            "clerk_session_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_view_as_session_clerk_session_sha256",
        ),
        sa.CheckConstraint(
            "browser_nonce_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_view_as_session_browser_nonce_sha256",
        ),
        sa.CheckConstraint(
            "absolute_expires_at > started_at",
            name="ck_view_as_session_expiry_after_start",
        ),
        sa.CheckConstraint(
            "(ended_at IS NULL AND end_reason IS NULL) OR "
            "(ended_at IS NOT NULL AND ended_at >= started_at "
            "AND end_reason IS NOT NULL)",
            name="ck_view_as_session_end_complete",
        ),
        sa.CheckConstraint(
            "end_reason IS NULL OR end_reason IN "
            "('operator_exit', 'rotated', 'expired', "
            "'capability_revoked', 'session_binding_changed')",
            name="ck_view_as_session_end_reason",
        ),
        sa.Index(
            "uq_view_as_session_active_actor",
            "environment",
            "actor_doctor_id",
            unique=True,
            postgresql_where=sa.text("ended_at IS NULL"),
        ),
        sa.Index("view_as_session_expiry_idx", "absolute_expires_at"),
    )

    environment: str = Field(max_length=16)
    capability_grant_id: TypeIDType = Field(
        sa_type=TypeIDType("superadmin_capability"),  # type: ignore
        foreign_key="superadmin_capability_grant.id",
    )
    actor_doctor_id: TypeIDType = Field(
        sa_type=TypeIDType("doctor"),  # type: ignore
        foreign_key="doctor.id",
    )
    target_doctor_id: TypeIDType = Field(
        sa_type=TypeIDType("doctor"),  # type: ignore
        foreign_key="doctor.id",
    )
    actor_issuer_sha256: str = Field(min_length=64, max_length=64)
    actor_subject_sha256: str = Field(min_length=64, max_length=64)
    clerk_session_sha256: str = Field(min_length=64, max_length=64)
    browser_nonce_sha256: str = Field(min_length=64, max_length=64)
    started_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    absolute_expires_at: datetime = Field(
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    ended_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    end_reason: str | None = Field(
        default=None,
        sa_column=Column(sa.String(length=32), nullable=True),
    )


class ViewAsAuditEvent(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("view_as_audit"),
    table=True,
):
    """Append-only, value-minimized evidence for view-as lifecycle changes."""

    __table_args__ = (
        sa.CheckConstraint(
            "environment IN ('staging', 'production')",
            name="ck_view_as_audit_environment",
        ),
        sa.CheckConstraint(
            "event_type IN ('began', 'ended', 'expired', 'stale_cleared', 'rejected')",
            name="ck_view_as_audit_event_type",
        ),
        sa.CheckConstraint(
            "outcome IN ('accepted', 'denied')",
            name="ck_view_as_audit_outcome",
        ),
        sa.CheckConstraint(
            "event_digest ~ '^[0-9a-f]{64}$'",
            name="ck_view_as_audit_event_digest",
        ),
        sa.Index("view_as_audit_occurred_at_idx", "occurred_at"),
    )

    environment: str = Field(max_length=16)
    event_type: str = Field(max_length=32, index=True)
    outcome: str = Field(max_length=16)
    reason_code: str = Field(max_length=48)
    occurred_at: datetime = Field(
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    event_digest: str = Field(min_length=64, max_length=64)
    session_id: TypeIDType | None = Field(
        default=None,
        sa_type=TypeIDType("view_as_session"),  # type: ignore
        foreign_key="view_as_session.id",
    )
    capability_grant_id: TypeIDType | None = Field(
        default=None,
        sa_type=TypeIDType("superadmin_capability"),  # type: ignore
        foreign_key="superadmin_capability_grant.id",
    )
    actor_doctor_id: TypeIDType = Field(
        sa_type=TypeIDType("doctor"),  # type: ignore
        foreign_key="doctor.id",
    )
    target_doctor_id: TypeIDType | None = Field(
        default=None,
        sa_type=TypeIDType("doctor"),  # type: ignore
        foreign_key="doctor.id",
    )
