from activemodel import BaseModel
from activemodel.mixins import PydanticJSONMixin, TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from app.models.doctor_note_schema import DoctorNoteSchema
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship

from .doctor import Doctor


class DoctorNoteExample(
    BaseModel,
    PydanticJSONMixin,
    TypeIDMixin("doctor_note_example"),
    TimestampsMixin,
    table=True,
):
    """
    When onboarding, doctors will upload PDFs of their best notes. The fields in this model will be extracted from the PDF.

    OpenAI is used to extract text content from uploaded PDFs, therefore we don't have the raw text content from the PDF
    which is why it's not included here.
    """

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    doctor_note_schema_id: TypeIDType = DoctorNoteSchema.foreign_key()
    doctor_note_schema: DoctorNoteSchema = Relationship()

    field_examples: dict[str, str] = Field(sa_type=JSONB())  # type: ignore
    "key is field name, value is example content for a specific field"

    # TODO the field examples need to be deidentified with surrogates
