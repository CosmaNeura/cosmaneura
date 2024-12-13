import uuid

from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship

from .appointment import Appointment
from .user import User


class SoftDeletionMixin:
    pass


class AIVisitNote(
    BaseModel, TypeIDMixin("visit_note"), TimestampsMixin, SoftDeletionMixin, table=True
):
    """
    Contents of the note are persisted for a long period of time and then eventually deleted.

    We use soft deletion so we have a clear idea of when the note was deleted.
    """

    user_id: uuid.UUID = Field(foreign_key="user.id", nullable=False)
    user: User = Relationship()

    appointment_id: uuid.UUID = Field(foreign_key="appointment.id", nullable=False)
    appointment: Appointment = Relationship()

    ai_transcript_id: uuid.UUID
    # = Field(
    #     foreign_key="ai_transcript_id.id", nullable=False
    # )

    note_schema_id: uuid.UUID

    note: dict = Field(sa_type=JSONB())  # type: ignore
