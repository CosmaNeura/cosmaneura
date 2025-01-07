import uuid

from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.doctor_note_schema import DoctorNoteSchema
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship

from .appointment import Appointment
from .doctor import Doctor


class AIVisitNote(BaseModel, TypeIDMixin("visit_note"), TimestampsMixin, table=True):
    """
    This represents the note summary for a single specific note schema. A given appointment will have multiple
    AI visit notes.

    Contents of the note are persisted for a long period of time and then eventually deleted.

    We use soft deletion so we have a clear idea of when the note was deleted.
    """

    doctor_id: uuid.UUID = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    appointment_id: uuid.UUID = Appointment.foreign_key()
    appointment: Appointment = Relationship()

    ai_transcript_id: uuid.UUID = AIVisitTranscript.foreign_key()
    ai_transcript: AIVisitTranscript = Relationship()

    doctor_note_schema_id: uuid.UUID = DoctorNoteSchema.foreign_key()
    doctor_note_schema: DoctorNoteSchema = Relationship()

    @property
    def note_type(self) -> str:
        return self.doctor_note_schema.note_type

    note: dict[str, str] = Field(sa_type=JSONB())  # type: ignore
    "key is name of section, value is markdown content"
