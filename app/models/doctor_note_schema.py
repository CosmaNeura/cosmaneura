import uuid

from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from app.models.patient import Patient
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship

from .appointment import Appointment
from .user import User


class DoctorNoteSchema(
    BaseModel,
    TypeIDMixin("doctor_note_schema"),
    TimestampsMixin,
    table=True,
):
    """
    A description of the fields required for a specific note type and instructions on what information goes in each field
    """

    user_id: uuid.UUID = Field(foreign_key="user.id", nullable=False)
    user: User = Relationship()

    note_type: str
    "user generated name of the note type"

    fields: dict[str, str]  = Field(sa_type=JSONB())  # type: ignore
    "key is field name, value is instruction"

    # examples: DoctorNoteExample
