"""Durable, payload-free receipts for Stripe webhook idempotency."""

from datetime import datetime

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field

from .practice import Practice


class StripeEventReceipt(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("stripe_event_receipt"),
    table=True,
):
    """One non-PHI row per event, committed with its mirrored state."""

    event_id: str = Field(
        sa_column=Column(sa.String(length=255), nullable=False, unique=True)
    )
    event_type: str = Field(sa_column=Column(sa.String(length=128), nullable=False))
    event_created_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    practice_id: TypeIDType = Practice.foreign_key()
