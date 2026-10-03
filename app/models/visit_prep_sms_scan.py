"""One operational scan position; never patient authority or retained clinical data."""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from activemodel import BaseModel
from sqlmodel import Column, Field


class VisitPrepSMSScan(BaseModel, table=True):
    __table_args__ = (
        sa.CheckConstraint("name='visit_prep_sms_v1'", name="name_shape"),
        sa.CheckConstraint(
            "version BETWEEN 0 AND 9223372036854775807", name="version_shape"
        ),
    )
    name: str = Field(primary_key=True, max_length=32)
    after_id: UUID | None = Field(default=None, repr=False, exclude=True)
    version: int = Field(default=0, sa_column=Column(sa.BigInteger(), nullable=False))
    checked_at: datetime = Field(
        sa_column=Column(sa.DateTime(timezone=True), nullable=False)
    )
