"""Durable phase journal for one creator-bound Scribe capture admission."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from app.models.scribe_capture_binding import ScribeCaptureBinding
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Column, Field


class ScribeCaptureAdmission(
    BaseModel, TimestampsMixin, TypeIDMixin("scribe_admission"), table=True
):
    """Receipts survive retries and revocation; they never authorize more work."""

    __table_args__ = (
        sa.CheckConstraint("configured_capture IS NULL OR jsonb_typeof(configured_capture) = 'object'",
                           name="ck_scribe_configured_capture_object"),
        sa.UniqueConstraint("binding_id", name="uq_scribe_admission_binding"),
        sa.UniqueConstraint(
            "scribe_tenant_id", "session_id", name="uq_scribe_admission_session"
        ),
        sa.CheckConstraint(
            "status IN ('consent_pending', 'consent_unconfirmed', 'session_pending', "
            "'session_unconfirmed', 'ready', 'consent_unavailable')",
            name="ck_scribe_admission_status",
        ),
        sa.CheckConstraint(
            "(lease_token IS NULL AND lease_phase IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_token IS NOT NULL AND lease_phase IN ('consent', 'session') "
            "AND lease_expires_at IS NOT NULL)",
            name="ck_scribe_admission_lease",
        ),
        sa.CheckConstraint(
            "(consent_id IS NULL) = (consent_receipt IS NULL) AND "
            "(session_id IS NULL) = (session_receipt IS NULL) AND "
            "(session_id IS NULL OR consent_id IS NOT NULL) AND "
            "(status <> 'ready' OR session_id IS NOT NULL)",
            name="ck_scribe_admission_receipts",
        ),
        sa.CheckConstraint(
            "length(request_sha256) = 64 AND length(client_key_sha256) = 64",
            name="ck_scribe_admission_digests",
        ),
    )

    binding_id: TypeIDType = ScribeCaptureBinding.foreign_key()
    scribe_tenant_id: UUID
    request_sha256: str = Field(max_length=64)
    client_key_sha256: str = Field(max_length=64)
    consent_key: UUID = Field(default_factory=uuid4, unique=True)
    session_key: UUID = Field(default_factory=uuid4, unique=True)
    consent_payload: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    configured_capture: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSONB(none_as_null=True), nullable=True)
    )
    consent_id: UUID | None = Field(default=None, nullable=True)
    consent_receipt: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSONB(none_as_null=True), nullable=True)
    )
    session_id: UUID | None = Field(default=None, nullable=True)
    session_receipt: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSONB(none_as_null=True), nullable=True)
    )
    status: str = Field(default="consent_pending", max_length=32)
    lease_token: UUID | None = Field(default=None, nullable=True)
    lease_phase: str | None = Field(default=None, nullable=True, max_length=16)
    lease_expires_at: datetime | None = Field(
        default=None,
        nullable=True,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    last_error_code: str | None = Field(default=None, nullable=True, max_length=64)
