from pydantic import BaseModel as PydanticBaseModel
from typing import List, Optional, Literal
from activemodel import BaseModel
from activemodel.mixins import (
    PydanticJSONMixin,
    SoftDeletionMixin,
    TimestampsMixin,
    TypeIDMixin,
)
from activemodel.types import TypeIDType
from sqlalchemy.dialects.postgresql import JSON
from sqlmodel import Field, Relationship

from .doctor import Doctor

# This must match the schema in the prompt in the `extract_intake` job.
class QuestionSchema(PydanticBaseModel):
    content: str
    answerType: Literal["FREE_FORM", "MULTIPLE_CHOICE", "MULTIPLE_CHOICE_WITH_OTHER"]
    answerOptions: Optional[List[str]] = None  # Required only for multiple-choice types

class DoctorIntakeSchema(
    BaseModel,
    PydanticJSONMixin,
    TimestampsMixin,
    SoftDeletionMixin,
    TypeIDMixin("doctor_note_schema"),
    table=True,
):
    """
    The schema for a doctor's intake form.
    """

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    form_type: str
    "user generated name of the form type"

    questions: List[QuestionSchema] = Field(sa_type=JSON)
    "questions asked by the form"
