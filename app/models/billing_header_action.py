"""A verified provider action, not a reusable workload or source permission."""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TypeIDMixin
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field


class BillingHeaderAction(BaseModel, TypeIDMixin("billing_action"), table=True):
    __table_args__ = (
        sa.CheckConstraint(
            "request_sha256 ~ '^[a-f0-9]{64}$' AND operation_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_billing_action_hashes",
        ),
        sa.CheckConstraint(
            "purpose IN ('completed-note.accept','header.read','header.generate','header.save','header.generation-status')",
            name="ck_billing_action_purpose",
        ),
        sa.CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + "
            "CASE WHEN generation_id IS NULL THEN interval '120 seconds' "
            "ELSE interval '360 seconds' END",
            name="ck_billing_action_lifetime",
        ),
        sa.CheckConstraint(
            "generation_id IS NULL OR purpose = 'header.generate'",
            name="ck_billing_action_generation",
        ),
        sa.CheckConstraint(
            "completion_status IS NULL OR (generation_id IS NOT NULL AND finished_at IS NOT NULL "
            "AND completion_status IN ('succeeded','failed','unknown'))",
            name="ck_billing_action_completion",
        ),
        sa.CheckConstraint(
            "(NOT continuation_consent AND initial_authorized_until IS NULL) OR "
            "(continuation_consent AND generation_id IS NOT NULL AND purpose = 'header.generate' "
            "AND initial_authorized_until IS NOT NULL AND initial_authorized_until > created_at AND initial_authorized_until <= expires_at)",
            name="ck_billing_action_continuation",
        ),
        sa.Index(
            "uq_billing_action_generation",
            "operation_id",
            "generation_id",
            unique=True,
            postgresql_where=sa.text("generation_id IS NOT NULL"),
        ),
        sa.Index(
            "ix_billing_action_request", "operation_id", "request_sha256", "purpose"
        ),
    )
    operation_id: UUID = Field(
        sa_type=sa.Uuid, foreign_key="billing_header_prepared_operation.operation_id"
    )
    purpose: str = Field(max_length=32)
    request_sha256: str = Field(max_length=64)
    operation_sha256: str = Field(max_length=64)
    actor: dict = Field(sa_type=JSONB, exclude=True, repr=False)
    created_at: datetime = Field(
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False)
    )
    expires_at: datetime = Field(
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False)
    )
    generation_id: UUID | None = Field(default=None, sa_type=sa.Uuid)
    continuation_consent: bool = Field(default=False, nullable=False)
    initial_authorized_until: datetime | None = Field(
        default=None, sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True)
    )
    completion_status: str | None = Field(default=None, max_length=16)
    finished_at: datetime | None = Field(
        default=None, sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True)
    )
