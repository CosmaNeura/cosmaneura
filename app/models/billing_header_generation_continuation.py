"""Append-only verified expiry evidence for one explicitly consented intent."""

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.types import TypeIDType
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field


class BillingHeaderGenerationContinuation(BaseModel, table=True):
    __table_args__ = (
        sa.CheckConstraint(
            "purpose = 'header.generation-continue'", name="ck_billing_continue_purpose"
        ),
        sa.CheckConstraint(
            "request_sha256 ~ '^[a-f0-9]{64}$'", name="ck_billing_continue_digest"
        ),
        sa.CheckConstraint(
            "authorized_until > created_at", name="ck_billing_continue_expiry"
        ),
        sa.UniqueConstraint(
            "action_id", "authorized_until", name="uq_billing_continue_expiry"
        ),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True, sa_type=sa.Uuid)
    action_id: TypeIDType = Field(
        sa_type=TypeIDType("billing_action"), foreign_key="billing_header_action.id"
    )
    purpose: str = Field(default="header.generation-continue", max_length=32)
    request_sha256: str = Field(max_length=64)
    actor: dict = Field(sa_type=JSONB, exclude=True, repr=False)
    created_at: datetime = Field(
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False)
    )
    authorized_until: datetime = Field(
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False)
    )
