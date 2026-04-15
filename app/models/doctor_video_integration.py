from datetime import datetime
from enum import Enum

from app.utils.encryption import EncryptedType

from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from app.models.doctor import Doctor
from sqlalchemy import Column
from sqlmodel import Field, UniqueConstraint


class VideoProvider(str, Enum):
    zoom = "zoom"


class DoctorVideoIntegration(
    BaseModel, TimestampsMixin, TypeIDMixin("dvint"), table=True
):
    __table_args__ = (
        UniqueConstraint(
            "doctor_id", "provider", name="uq_doctor_video_integration_provider"
        ),
    )

    doctor_id: TypeIDType = Doctor.foreign_key()
    "FK to the doctor who owns this integration"

    provider: VideoProvider
    "Video meeting provider (zoom, google_meet, etc.)"

    provider_user_id: str
    "User ID within the provider (e.g. Zoom host_id)"

    access_token: str = Field(sa_column=Column(EncryptedType(), nullable=False))
    "OAuth access token — stored encrypted at rest (AES-256/Fernet)"

    refresh_token: str = Field(sa_column=Column(EncryptedType(), nullable=False))
    "OAuth refresh token — stored encrypted at rest (AES-256/Fernet)"

    token_expires_at: datetime
    "When the current access_token expires"
