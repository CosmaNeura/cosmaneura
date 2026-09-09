from datetime import datetime

from app.utils.encryption import EncryptedType

from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from app.models.doctor import Doctor
from sqlalchemy import Column, DateTime
from sqlmodel import Field, UniqueConstraint

from .patient_external_identity import SchedulingProviderSource


class DoctorSchedulingIntegration(
    BaseModel, TimestampsMixin, TypeIDMixin("dsint"), table=True
):
    """
    Per-doctor OAuth grant for a SchedulingProvider adapter (Acuity, Google
    Calendar). Mirrors DoctorVideoIntegration's shape; see
    contracts/scheduling-provider-v1.md.
    """

    __table_args__ = (
        UniqueConstraint(
            "doctor_id", "source", name="uq_doctor_scheduling_integration_source"
        ),
    )

    doctor_id: TypeIDType = Doctor.foreign_key()
    "FK to the doctor who owns this integration"

    source: SchedulingProviderSource
    "which SchedulingProvider adapter this grant is for"

    provider_calendar_ref: str
    "calendar/account identifier within the provider (e.g. Acuity calendarID, Google calendar ID)"

    access_token: str = Field(sa_column=Column(EncryptedType(), nullable=False))
    "OAuth access token — stored encrypted at rest (AES-256/Fernet)"

    refresh_token: str = Field(sa_column=Column(EncryptedType(), nullable=False))
    "OAuth refresh token — stored encrypted at rest (AES-256/Fernet)"

    token_expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    "When the current access_token expires"

    last_backfilled_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    "Set once the one-time historical backfill has completed for this integration"
