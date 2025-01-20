import typing as t

from pydantic import computed_field

from activemodel import BaseModel
from activemodel.mixins import (
    PydanticJSONMixin,
    SoftDeletionMixin,
    TimestampsMixin,
    TypeIDMixin,
)
from activemodel.types import TypeIDType
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import joinedload
from sqlmodel import Field, Relationship

from .doctor import Doctor

if t.TYPE_CHECKING:
    from .doctor_note_example import DoctorNoteExample


class DoctorNoteSchema(
    BaseModel,
    PydanticJSONMixin,
    TypeIDMixin("doctor_note_schema"),
    TimestampsMixin,
    SoftDeletionMixin,
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

    examples: list["DoctorNoteExample"] = Relationship(
        back_populates="doctor_note_schema"
    )

    @computed_field
    @property
    def example_count(self) -> int:
        # TODO joinedload is probably not the right approach here
        # TODO(mbianco) this took a long time to understand, document this upstream
        return self.select().options(joinedload(self.__class__.examples)).count()
