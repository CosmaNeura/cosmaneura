"""Invoice-only commerce state. Never an input to clinical access."""

from datetime import datetime
from uuid import uuid4

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field

from .practice import Practice


class PracticeInvoiceBilling(BaseModel, TimestampsMixin, table=True):
    __tablename__ = "practice_invoice_billing"  # type: ignore[assignment]

    practice_id: TypeIDType = Practice.foreign_key(primary_key=True)
    account_reference: str = Field(default_factory=lambda: uuid4().hex, unique=True)
    stripe_customer_id: str | None = Field(default=None, unique=True)
    stripe_subscription_id: str | None = Field(default=None, unique=True)
    price_tier: str
    quantity: int = Field(ge=1, le=20)
    subscription_status: str | None = None
    last_invoice_status: str | None = None
    last_event_created_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    quantity_sync_required: bool = False
    quantity_sync_version: int = 0
    operation_version: int = 1
    days_until_due: int = Field(default=30, ge=1, le=365)
    operator_ref: str = Field(max_length=255)
    reason: str = Field(sa_column=Column(sa.String(512), nullable=False))
