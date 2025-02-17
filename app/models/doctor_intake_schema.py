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
from app.models.intake import IntakeQuestion
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import joinedload
from sqlmodel import Field, Relationship

from .doctor import Doctor

if t.TYPE_CHECKING:
    from .doctor_intake_example import DoctorIntakeExample


class DoctorIntakeSchema(
    BaseModel,
    PydanticJSONMixin,
    TimestampsMixin,
    SoftDeletionMixin,
    TypeIDMixin("doctor_intake_schema"),
    table=True,
):
    """
    A description of the fields required for a specific note type and instructions on what information goes in each field.

    This is extracted from a list of intake examples.
    """

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    questions: list[IntakeQuestion] = Field(sa_type=JSONB())  # type: ignore
    "list of questions imported from the intake examples"

    examples: list["DoctorIntakeExample"] = Relationship(
        back_populates="doctor_intake_schema"
    )

    @computed_field
    @property
    def example_count(self) -> int:
        # TODO joinedload is probably not the right approach here
        # TODO(mbianco) this took a long time to understand, document this upstream
        return self.select().options(joinedload(self.__class__.examples)).count()
