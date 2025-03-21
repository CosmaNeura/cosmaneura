from activemodel import BaseModel
from activemodel.mixins import (
    PydanticJSONMixin,
    SoftDeletionMixin,
    TimestampsMixin,
    TypeIDMixin,
)
from activemodel.types import TypeIDType
from app.models.intake.doctor_intake_schema import DoctorIntakeSchema
from app.models.intake.intake import IntakeQuestion
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship

from ..doctor import Doctor


class DoctorIntakeExample(
    BaseModel,
    PydanticJSONMixin,
    TimestampsMixin,
    SoftDeletionMixin,
    TypeIDMixin("doctor_intake_example"),
    table=True,
):
    """
    When onboarding, doctors will upload PDFs of their patients best intake forms. The fields in this model will be extracted from these PDF(s)

    OpenAI is used to extract text content from uploaded PDFs, therefore we don't have the raw text content from the PDF
    which is why it's not included here.
    """

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    doctor_intake_schema_id: TypeIDType = DoctorIntakeSchema.foreign_key()
    doctor_intake_schema: DoctorIntakeSchema = Relationship()

    questions: list[IntakeQuestion] = Field(sa_type=JSONB)
    "list of questions imported from the intake examples"
