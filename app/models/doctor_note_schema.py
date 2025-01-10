from activemodel.types import TypeIDType
import typing as t
import uuid

from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from app.models.patient import Patient
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship

from .appointment import Appointment
from .doctor import Doctor

if t.TYPE_CHECKING:
    from .doctor_note_example import DoctorNoteExample


class DoctorNoteSchema(
    BaseModel,
    TypeIDMixin("doctor_note_schema"),
    TimestampsMixin,
    table=True,
):
    """
    A description of the fields required for a specific note type and instructions on what information goes in each field
    """

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    note_type: str
    "user generated name of the note type"

    fields: dict[str, str] = Field(sa_type=JSONB())  # type: ignore
    "key is field name, value is instruction"

    # TODO Mike will handle back_populates
    # examples: list["DoctorNoteExample"] = Relationship(back_populates="")
