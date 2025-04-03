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
from sqlalchemy.dialects.postgresql import JSON
from sqlmodel import Field, Relationship, UniqueConstraint

from .doctor import Doctor

if t.TYPE_CHECKING:
    from .doctor_note_example import DoctorNoteExample


class DoctorNoteSchema(
    BaseModel,
    PydanticJSONMixin,
    TimestampsMixin,
    SoftDeletionMixin,
    TypeIDMixin("doctor_note_schema"),
    table=True,
):
    """
    A description of the fields required for a specific note type and instructions on what information goes in each field
    """

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    note_type: str
    "user generated name of the note type"

    # IMPORTANT JSON type is very intentional here, without this we lose ordering
    fields: dict[str, str] = Field(sa_type=JSON)
    "key is field name, value is instruction"

    examples: list["DoctorNoteExample"] = Relationship(
        back_populates="doctor_note_schema"
    )

    __table_args__ = (
        # users should never see two schemas with the same name
        UniqueConstraint("note_type", "doctor_id", name="doctor_and_note_type_unique"),
    )

    @computed_field
    @property
    def example_count(self) -> int:
        # TODO joinedload is probably not the right approach here
        # TODO(mbianco) this took a long time to understand, document this upstream
        return (
            self.where(DoctorNoteSchema.id == self.id, self.__class__.examples)
            # generates a LEFT JOIN against examples table
            # .options(joinedload(self.__class__.examples))
            .count()
        )
