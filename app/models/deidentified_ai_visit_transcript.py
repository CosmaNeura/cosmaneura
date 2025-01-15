from activemodel import BaseModel
from activemodel.mixins import PydanticJSONMixin, TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from .ai_visit_transcript import TranscriptEntry
from .deidentified_ai_visit_note import DeidentifiedAIVisitNote


class DeidentifiedAIVisitTranscript(
    BaseModel,
    TypeIDMixin("deid_transcript"),
    TimestampsMixin,
    table=True,
):
    """
    Contents of an AIVisitTranscript after being run through Azure's deidentification service.
    """

    transcript_data: list[TranscriptEntry] = Field(sa_type=JSONB())  # type: ignore
    deidentified_visit_note_id: TypeIDType = DeidentifiedAIVisitNote.foreign_key()
