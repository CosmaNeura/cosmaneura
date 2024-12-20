import uuid

from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types.typeid import TypeIDType
from app.models.appointment import Appointment
from app.models.user import User
from sqlmodel import Field, Relationship


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
        sa_type=TypeIDType("usr"),  # type: ignore
        foreign_key="user.id",
        nullable=False,
    )

    patient_id: uuid.UUID = Field(foreign_key="user.id", nullable=False)
    patient: User = Relationship()

    appointment_id: uuid.UUID = Field(foreign_key="appointment.id", nullable=False)
    appointment: Appointment = Relationship()

    azure_file_name: str
