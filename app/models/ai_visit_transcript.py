from uuid import UUID

from pydantic import BaseModel as PydanticBaseModel
from pydantic import SerializerFunctionWrapHandler, field_serializer, model_serializer

from activemodel import BaseModel
from activemodel.mixins import (
    PydanticJSONMixin,
    SoftDeletionMixin,
    TimestampsMixin,
    TypeIDMixin,
)
from activemodel.types import TypeIDType
from app.models.patient import Patient
from sqlalchemy import event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import attributes, reconstructor
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

    source_speaker_id: UUID | None = None
    "Transcript-local acoustic identity; never a clinical role or person identity."

    def legacy_data(self) -> dict[str, object]:
        """The existing delivery/deduplication contract excludes display metadata."""
        return {
            "speaker": self.speaker,
            "content": self.content,
            "startTime": self.startTime,
            "endTime": self.endTime,
        }

    @field_serializer("source_speaker_id")
    def serialize_identity(self, value: UUID | None) -> str | None:
        # ActiveModel's JSONB encoder uses model_dump() in Python mode.
        return str(value) if value is not None else None

    @model_serializer(mode="wrap")
    def omit_missing_identity(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if self.source_speaker_id is None:
            data.pop("source_speaker_id", None)
        return data


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

    transcript_data: list[TranscriptEntry] = Field(sa_type=JSONB)

    @reconstructor
    def __transform_dict_to_pydantic__(self):
        """Keep entries typed after either an initial load or a refresh.

        ActiveModel also calls this after save/refresh, so already-parsed entries
        must be accepted. Committed assignment avoids treating a read as an edit.
        """
        entries = self.__dict__.get("transcript_data")
        if entries is not None:
            attributes.set_committed_value(
                self,
                "transcript_data",
                [TranscriptEntry.model_validate(entry) for entry in entries],
            )


@event.listens_for(AIVisitTranscript, "refresh")
def _restore_transcript_entries(target, _context, refreshed_attributes):
    # A partial refresh of another field must preserve pending transcript edits.
    if refreshed_attributes is None or "transcript_data" in refreshed_attributes:
        target.__transform_dict_to_pydantic__()
