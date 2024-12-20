import uuid

from pydantic import BaseModel as PydanticBaseModel

from activemodel import BaseModel
from activemodel.mixins import SoftDeletionMixin, TimestampsMixin, TypeIDMixin
from app.models.patient import Patient
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship

from .appointment import Appointment
from .user import Doctor


class TranscriptEntry(PydanticBaseModel):
    speaker: str
    content: str
    timestamp: str | None


# TODO move to AIVisitNote
class AIVisitTranscript(
    BaseModel,
    TypeIDMixin("ai_visit_transcript"),
    TimestampsMixin,
    SoftDeletionMixin,
    table=True,
):
    """
    Contents of the note are persisted for a long period of time and then eventually deleted.

    We use soft deletion so we have a clear idea of when the note was deleted.
    """

    doctor_id: uuid.UUID = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    appointment_id: uuid.UUID = Appointment.foreign_key()
    appointment: Appointment = Relationship()

    patient_id: uuid.UUID = Patient.foreign_key()
    patient: Patient = Relationship()

    duration: int | None
    "total length of the transcript"

    transcript_data: list[TranscriptEntry] = Field(sa_type=JSONB())  # type: ignore
