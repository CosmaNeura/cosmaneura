from activemodel import BaseModel
from activemodel.mixins import PydanticJSONMixin, TypeIDMixin
from app.models.ai_visit_transcript import TranscriptEntry
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field


class AIVisitTranscriptNonPHI(
    BaseModel,
    PydanticJSONMixin,
    TypeIDMixin("ai_visit_transcript_non_phi"),
    table=True,
    # intentionally no timestamps or other identifying fields to avoid being able to tie back to original data
):
    """
    De-identified transcript data that cannot be tied back to any patient, doctor, or appointment.

    This model stores transcript content that has been processed through Azure's de-identification
    service to remove all PHI. It intentionally excludes any fields that could be used to link
    back to the original transcript or participants.
    """

    data: list[TranscriptEntry] = Field(sa_type=JSONB)
    "De-identified transcript entries with all PHI removed"
