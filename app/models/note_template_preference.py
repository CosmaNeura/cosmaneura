from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import CheckConstraint, Field

from .doctor import Doctor
from .doctor_note_schema import DoctorNoteSchema


class NoteTemplatePreference(
    BaseModel, TimestampsMixin, TypeIDMixin("note_template_preference"), table=True
):
    """An owner's default starting template; never a saved-note selection."""

    doctor_id: TypeIDType = Doctor.foreign_key(unique=True)
    default_schema_id: TypeIDType | None = DoctorNoteSchema.foreign_key(nullable=True)
    revision: int = Field(default=0, ge=0)

    __table_args__ = (CheckConstraint("revision >= 0", name="revision_nonnegative"),)
