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
            "purpose IN ('completed-note.accept','header.read','header.generate','header.save')",
            name="ck_billing_action_purpose",
        ),
        sa.CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + interval '120 seconds'",
            name="ck_billing_action_lifetime",
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
    finished_at: datetime | None = Field(
        default=None, sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True)
    )
