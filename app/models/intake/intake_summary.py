from activemodel import BaseModel
from activemodel.mixins import (
    PydanticJSONMixin,
    SoftDeletionMixin,
    TimestampsMixin,
    TypeIDMixin,
)
from activemodel.types import TypeIDType
from app.models.intake.intake import Intake
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship

from ..doctor import Doctor


class IntakeSummary(
    BaseModel,
    PydanticJSONMixin,
    TimestampsMixin,
    SoftDeletionMixin,
    TypeIDMixin("intake_summary"),
    table=True,
):
    """
    After an intake is complete, we generate a summary of the answers for the doctor's review.
    """

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    intake_id: TypeIDType = Intake.foreign_key()
    intake: Intake = Relationship()

    summary: dict[str, str] = Field(sa_type=JSONB)
    "key value dictionary of the AI summary"
