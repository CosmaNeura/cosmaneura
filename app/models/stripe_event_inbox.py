"""Durable inbox for verified Stripe events awaiting worker reconciliation."""

from datetime import datetime

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from sqlmodel import Column, Field


class StripeEventInbox(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("stripe_event_inbox"),
    table=True,
):
    """Signed billing event persisted before the webhook returns success."""

    event_id: str = Field(
        sa_column=Column(sa.String(length=255), nullable=False, unique=True)
    )
    event_type: str = Field(sa_column=Column(sa.String(length=128), nullable=False))
    payload_json: str = Field(sa_column=Column(sa.Text(), nullable=False))
    state: str = Field(
        default="pending",
        sa_column=Column(
            sa.String(length=32),
            nullable=False,
            index=True,
            server_default="pending",
        ),
    )
    attempts: int = Field(
        default=0,
        sa_column=Column(sa.Integer(), nullable=False, server_default="0"),
    )
    last_error: str | None = Field(
        default=None,
        sa_column=Column(sa.String(length=512), nullable=True),
    )
    next_attempt_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    processed_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
