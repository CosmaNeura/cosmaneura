from pydantic import BaseModel as PydanticBaseModel

from activemodel import BaseModel
from activemodel.mixins import (
    PydanticJSONMixin,
    SoftDeletionMixin,
    TimestampsMixin,
    TypeIDMixin,
)
from activemodel.types import TypeIDType
from app.models.patient import Patient
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship

from .appointment import Appointment
from .doctor import Doctor


class TranscriptEntry(PydanticBaseModel):
    speaker: str
    content: str

    startTime: int
    "in milliseconds"

    endTime: int
    "in milliseconds"


class AIVisitTranscript(
    BaseModel,
    PydanticJSONMixin,
    TimestampsMixin,
    SoftDeletionMixin,
    TypeIDMixin("ai_visit_transcript"),
    table=True,
):
    """
    Contents of the note are persisted for a long period of time and then eventually deleted.

    We use soft deletion so we have a clear idea of when the note was deleted.
    """

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    appointment_id: TypeIDType = Appointment.foreign_key()
    appointment: Appointment = Relationship()

    patient_id: TypeIDType = Patient.foreign_key()
    patient: Patient = Relationship()

    duration: int | None
    "total length of the transcript, in seconds"

    transcript_data: list[TranscriptEntry] = Field(sa_type=JSONB())  # type: ignore
