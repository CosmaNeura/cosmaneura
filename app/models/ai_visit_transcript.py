import uuid

from pydantic import BaseModel as PydanticBaseModel

from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from app.models.patient import Patient
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship

from .appointment import Appointment
from .user import User


class TranscriptEntry(PydanticBaseModel):
    speaker: str
    content: str
    timestamp: str | None


# TODO move to AIVisitNote
class AIVisitTranscript(
    BaseModel,
    TypeIDMixin("ai_visit_transcript"),
    TimestampsMixin,
    # SoftDeletionMixin,
    table=True,
):
    """
    Contents of the note are persisted for a long period of time and then eventually deleted.

    We use soft deletion so we have a clear idea of when the note was deleted.
    """

    user_id: uuid.UUID = Field(foreign_key="user.id", nullable=False)
    user: User = Relationship()

    appointment_id: uuid.UUID = Field(foreign_key="appointment.id", nullable=False)
    appointment: Appointment = Relationship()

    patient_id: uuid.UUID = Field(foreign_key="patient.id", nullable=False)
    patient: Patient = Relationship()

    duration: int | None
    "total length of the transcript"

    transcript_data: list[TranscriptEntry] = Field(sa_type=JSONB())  # type: ignore
