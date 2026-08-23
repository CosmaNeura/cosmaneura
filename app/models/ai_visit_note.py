from datetime import datetime

from pydantic import computed_field

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import PydanticJSONMixin, TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.doctor_note_schema import DoctorNoteSchema
from sqlalchemy.dialects.postgresql import JSON
from sqlmodel import Field, Relationship

from .appointment import Appointment
from .doctor import Doctor

HISTORICAL_DUPLICATE_SUPERSESSION = "migration:a81c9d7e4f20"
REGENERATED_NOTE_SUPERSESSION = "regenerated_from_newer_transcript"
ADMIN_RESTORE_SUPERSESSION = "admin_restore"


class AIVisitNote(
    BaseModel,
    PydanticJSONMixin,
    TimestampsMixin,
    TypeIDMixin("visit_note"),
    table=True,
):
    """
    This represents the note summary for a single specific note schema. A given appointment will have multiple
    AI visit notes.

    Generated versions are retained. Exactly one version per appointment and
    schema is current; prior outputs are marked with supersession metadata and
    remain available for audited administrative recovery.
    """

    __table_args__ = (
        sa.CheckConstraint(
            "(superseded_at IS NULL AND supersession_reason IS NULL) OR "
            "(superseded_at IS NOT NULL AND supersession_reason IS NOT NULL "
            "AND char_length(supersession_reason) BETWEEN 1 AND 64)",
            name="ck_ai_visit_note_supersession_complete",
        ),
        sa.Index(
            "uq_ai_visit_note_current_appointment_schema",
            "appointment_id",
            "doctor_note_schema_id",
            unique=True,
            postgresql_where=sa.text("superseded_at IS NULL"),
        ),
    )

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    appointment_id: TypeIDType = Appointment.foreign_key()
    appointment: Appointment = Relationship()

    ai_transcript_id: TypeIDType = AIVisitTranscript.foreign_key()
    ai_transcript: AIVisitTranscript = Relationship(
        sa_relationship_kwargs={"foreign_keys": "AIVisitNote.ai_transcript_id"}
    )

    restore_frontier_transcript_id: TypeIDType | None = AIVisitTranscript.foreign_key(
        nullable=True, exclude=True
    )
    "Latest active transcript protected by an administrative version restore."

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

    superseded_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
        exclude=True,
    )
    "When set, this generated version is retained as history, not current output."

    supersession_reason: str | None = Field(
        default=None,
        max_length=64,
        exclude=True,
    )
    "Bounded, non-PHI code explaining why this version stopped being current."
