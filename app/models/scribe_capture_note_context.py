"""Immutable clinician notebook provenance attached to a saved Core draft."""

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from app.models.ai_visit_note import AIVisitNote
from app.models.scribe_capture_binding import ScribeCaptureBinding
from sqlmodel import Field


class ScribeCaptureNoteContext(
    BaseModel, TimestampsMixin, TypeIDMixin("scribe_note_context"), table=True
):
    __table_args__ = (
        sa.UniqueConstraint("note_id", name="uq_scribe_note_context_note"),
    )
    binding_id: TypeIDType = ScribeCaptureBinding.foreign_key()
    note_id: TypeIDType = AIVisitNote.foreign_key()
    scratch_notes: dict = Field(sa_type=sa.JSON, nullable=False)
