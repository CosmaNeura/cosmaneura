import uuid

from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types.typeid import TypeIDType
from sqlmodel import Field


class VisitAudioFile(
    BaseModel, TimestampsMixin, TypeIDMixin("visit_audio_file"), table=True
):
    """
    Link to the blob for a specific visit audio file

    1. User drops file into upload area
    2. User hits upload which hits the backend API which generates a signed upload URL
    3. User uploads file to signed upload URL, does not hit the backend API for upload
    """

    user_id: uuid.UUID = Field(
        sa_type=TypeIDType("usr"), foreign_key="user.id", nullable=False
    )
    azure_file_name: str
