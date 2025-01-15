from pydantic import computed_field

from activemodel import BaseModel
from activemodel.mixins import PydanticJSONMixin, TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from app.models.doctor_note_schema import DoctorNoteSchema
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field


class DeidentifiedAIVisitNote(
    BaseModel,
    TypeIDMixin("deid_visit_note"),
    TimestampsMixin,
    table=True,
):
    """
    Contents of an AIVisitNote after being run through Azure's deidentification service.
    """

    note: dict[str, str] = Field(sa_type=JSONB())  # type: ignore
    note_schema_id = DoctorNoteSchema.foreign_key()
