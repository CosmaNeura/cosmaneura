import typing as t

from pydantic import BaseModel as PydanticBaseModel
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

from ..doctor import Doctor

if t.TYPE_CHECKING:
    from .doctor_intake_example import DoctorIntakeExample


class IntakeSchemaQuestion(PydanticBaseModel):
    answer_type: t.Literal["FREE_FORM", "MULTIPLE_CHOICE", "MULTIPLE_CHOICE_WITH_OTHER"]
    "type of answer extracted from the intake PDF"

    answer_options: list[str] | None = None
    "required only for multiple-choice types"

    question: str
    "question to ask the patient"

    # TODO not currently in use, but could be used in the future for follow-up questions
    required_details: str
    "information that we expect to be included as a full answer to the question, based on the uploaded PDF upload"


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

    questions: list[IntakeSchemaQuestion] = Field(sa_type=JSONB)
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
