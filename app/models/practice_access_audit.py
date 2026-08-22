"""Append-only evidence for privileged practice access changes.

These rows contain operational metadata only, never BAA text or PHI. They are
the durable evidence behind CLI changes that can grant entitlement or make a
negotiated BAA effective. Application code intentionally has no update/delete
path for this model.
"""

from datetime import datetime
from enum import Enum

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field

from .practice import Practice


class PracticeAccessAction(str, Enum):
    billing_exemption_set = "billing_exemption_set"
    billing_exemption_cleared = "billing_exemption_cleared"
    negotiated_baa_recorded = "negotiated_baa_recorded"


class PracticeAccessAudit(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("practice_access_audit"),
    table=True,
):
    """Append-only operator evidence for an access-affecting practice change."""

    practice_id: TypeIDType = Practice.foreign_key()
    action: str = Field(index=True, max_length=64)
    operator_ref: str = Field(max_length=255)
    reason: str = Field(
        sa_column=Column(sa.String(length=512), nullable=False),
    )
    effective_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    expires_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    document_reference: str | None = Field(
        default=None,
        sa_column=Column(sa.String(length=255), nullable=True),
    )
