from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.appointment import Appointment
from sqlmodel import Field, UniqueConstraint


class ScribeTranscriptDelivery(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("scribe_delivery"),
    table=True,
):
    """Non-PHI receipt that makes the Scribe handoff safe to retry."""

    __table_args__ = (
        UniqueConstraint(
            "caller_client_id",
            "idempotency_key",
            name="uq_scribe_delivery_caller_idempotency_key",
        ),
        UniqueConstraint(
            "caller_client_id",
            "scribe_session_id",
            name="uq_scribe_delivery_caller_session",
        ),
    )

    caller_client_id: str = Field(index=True, max_length=64)
    idempotency_key: str = Field(max_length=255)
    scribe_session_id: UUID = Field(index=True)
    appointment_id: TypeIDType = Appointment.foreign_key()
    transcript_id: TypeIDType = AIVisitTranscript.foreign_key()
    payload_sha256: str = Field(max_length=64)
    pipeline_dispatched_at: datetime | None = Field(
        default=None,
        nullable=True,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
