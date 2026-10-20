"""Author-only reminders, separate from every clinical note/source model."""

from datetime import datetime
from uuid import UUID

from app.utils.encryption import EncryptedType

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.authority import AuthorityActor, AuthorityTenant
from app.models.doctor import Doctor
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from sqlmodel import Field


class ScribePrivateNote(
    BaseModel, TimestampsMixin, TypeIDMixin("scribe_private_note"), table=True
):
    __table_args__ = (
        sa.UniqueConstraint(
            "appointment_id",
            "actor_id",
            "tenant_id",
            "provider_link_id",
            name="uq_scribe_private_note_author_visit",
        ),
        sa.CheckConstraint("version > 0", name="ck_scribe_private_note_version"),
    )
    appointment_id: TypeIDType = Appointment.foreign_key()
    doctor_id: TypeIDType = Doctor.foreign_key()
    actor_id: TypeIDType = AuthorityActor.foreign_key()
    tenant_id: TypeIDType = AuthorityTenant.foreign_key()
    provider_link_id: TypeIDType = ScribeCaptureProviderLink.foreign_key()
    text: str = Field(sa_column=sa.Column(EncryptedType(), nullable=False), repr=False)
    version: int
    saved_at: datetime = Field(
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False)
    )


class ScribePrivateNoteReceipt(
    BaseModel, TimestampsMixin, TypeIDMixin("scribe_private_receipt"), table=True
):
    """Retry evidence contains no reminder text or plaintext content hash."""

    __table_args__ = (
        sa.UniqueConstraint(
            "actor_id", "tenant_id", "change_key", name="uq_scribe_private_note_change"
        ),
    )
    appointment_id: TypeIDType = Appointment.foreign_key()
    actor_id: TypeIDType = AuthorityActor.foreign_key()
    tenant_id: TypeIDType = AuthorityTenant.foreign_key()
    provider_link_id: TypeIDType = ScribeCaptureProviderLink.foreign_key()
    change_key: UUID
    request_digest: str = Field(max_length=64)
    saved_version: int
