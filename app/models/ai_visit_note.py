import uuid

from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.doctor_note_schema import DoctorNoteSchema
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
    This represents the note summary for a single specific note schema. A given appointment will have multiple
    AI visit notes.

    Contents of the note are persisted for a long period of time and then eventually deleted.

    We use soft deletion so we have a clear idea of when the note was deleted.
    """

    user_id: uuid.UUID = Field(foreign_key="user.id", nullable=False)
    user: User = Relationship()

    appointment_id: uuid.UUID = Field(foreign_key="appointment.id", nullable=False)
    appointment: Appointment = Relationship()

    ai_transcript_id: uuid.UUID = Field(
        foreign_key="ai_transcript_id.id", nullable=False
    )
    ai_transcript: AIVisitTranscript = Relationship()

    doctor_note_schema_id: uuid.UUID
    doctor_note_schema: DoctorNoteSchema = Relationship()

    # TODO computed prop of the note schema name that gets rendered via API

    note: dict[str, str] = Field(sa_type=JSONB())  # type: ignore
    "key is name of section, value is markdown content"
