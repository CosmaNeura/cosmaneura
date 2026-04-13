from enum import Enum

from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import UniqueConstraint

from app.models.doctor import Doctor


class VideoProvider(str, Enum):
    zoom = "zoom"


class DoctorVideoIntegration(
    BaseModel, TimestampsMixin, TypeIDMixin("dvint"), table=True
):
    __table_args__ = (
        UniqueConstraint("doctor_id", "provider", name="uq_doctor_video_integration_provider"),
    )

    doctor_id: TypeIDType = Doctor.foreign_key()
    "FK to the doctor who owns this integration"

    provider: VideoProvider
    "Video meeting provider (zoom, google_meet, etc.)"

    provider_user_id: str
    "User ID within the provider (e.g. Zoom host_id)"
