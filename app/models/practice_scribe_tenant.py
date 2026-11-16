"""Core-owned Scribe practice identifier; never a client-supplied authority."""

from uuid import UUID, uuid4

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from app.models.practice import Practice
from sqlmodel import Field


class PracticeScribeTenant(
    BaseModel, TimestampsMixin, TypeIDMixin("practice_scribe_tenant"), table=True
):
    __table_args__ = (
        sa.CheckConstraint(
            "scribe_tenant_id <> '00000000-0000-0000-0000-000000000000'",
            name="ck_practice_scribe_tenant_nonzero",
        ),
    )

    practice_id: TypeIDType = Practice.foreign_key(unique=True)
    scribe_tenant_id: UUID = Field(default_factory=uuid4, unique=True)
