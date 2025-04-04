from pydantic import computed_field

from activemodel import BaseModel
from activemodel.mixins import PydanticJSONMixin, TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.doctor_note_schema import DoctorNoteSchema
from sqlalchemy.dialects.postgresql import JSON
from sqlmodel import Field, Relationship

from .appointment import Appointment
from .doctor import Doctor


class AIVisitNote(
    BaseModel,
    PydanticJSONMixin,
    TimestampsMixin,
    TypeIDMixin("visit_note"),
    table=True,
    # TODO add deleted add mixin
):
    """
    This represents the note summary for a single specific note schema. A given appointment will have multiple
    AI visit notes.

    Contents of the note are persisted for a long period of time and then eventually deleted.

    We use soft deletion so we have a clear idea of when the note was deleted.
    """

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    appointment_id: TypeIDType = Appointment.foreign_key()
    appointment: Appointment = Relationship()

    ai_transcript_id: TypeIDType = AIVisitTranscript.foreign_key()
    ai_transcript: AIVisitTranscript = Relationship()

    doctor_note_schema_id: TypeIDType = DoctorNoteSchema.foreign_key()
    doctor_note_schema: DoctorNoteSchema = Relationship(
        sa_relationship_kwargs={"lazy": "joined"}
    )

    @computed_field
    @property
    def note_type(self) -> str:
        return self.doctor_note_schema.note_type

    # Order of note fields REALLY matters, so we cannot use JSONB, which will
    # remove the ordering when it serializes to binary.
    note: dict[str, str] = Field(sa_type=JSON)
    "key is name of section, value is markdown content"
