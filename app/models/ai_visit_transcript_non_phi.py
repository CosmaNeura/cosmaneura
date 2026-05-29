from activemodel import BaseModel
from activemodel.mixins import PydanticJSONMixin, TypeIDMixin
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from .ai_visit_transcript import TranscriptEntry


class AIVisitTranscriptNonPHI(
    BaseModel,
    PydanticJSONMixin,
    TypeIDMixin("ai_visit_transcript_non_phi"),
    table=True,
):
    """De-identified copy of an AIVisitTranscript's transcript_data.

    Intentionally carries no foreign keys to patient/doctor/appointment
    and no timestamp columns. Both would let timing or relationship
    metadata reattach this row to the original PHI row it was derived
    from. This dataset exists to improve model outputs without
    exposing individual visits.
    """

    data: list[TranscriptEntry] = Field(sa_type=JSONB)
