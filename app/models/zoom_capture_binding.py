"""Immutable encounter authority for one confirmed Recall bot recording."""

from datetime import datetime

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.zoom_meeting import ZoomMeeting
from sqlmodel import Field


class ZoomCaptureBinding(
    BaseModel, TimestampsMixin, TypeIDMixin("zoom_capture_binding"), table=True
):
    __table_args__ = (
        sa.UniqueConstraint("zoom_meeting_id", name="uq_zoom_capture_binding_meeting"),
        sa.UniqueConstraint("bot_digest", name="uq_zoom_capture_binding_bot"),
        sa.UniqueConstraint(
            "recording_digest", name="uq_zoom_capture_binding_recording"
        ),
        sa.CheckConstraint(
            "occurrence_digest ~ '^[0-9a-f]{64}$' AND bot_digest ~ '^[0-9a-f]{64}$' "
            "AND (recording_digest IS NULL OR recording_digest ~ '^[0-9a-f]{64}$')",
            name="ck_zoom_capture_binding_digests",
        ),
        sa.CheckConstraint(
            "(terminal_status IS NULL AND terminal_updated_at IS NULL) OR "
            "(terminal_status IS NOT NULL "
            "AND terminal_status IN ('permission_denied', 'ended', 'failed') "
            "AND terminal_updated_at IS NOT NULL)",
            name="ck_zoom_capture_binding_terminal",
        ),
    )

    zoom_meeting_id: TypeIDType = ZoomMeeting.foreign_key()
    doctor_id: TypeIDType = Doctor.foreign_key()
    appointment_id: TypeIDType = Appointment.foreign_key()
    patient_id: TypeIDType = Patient.foreign_key()
    occurrence_digest: str = Field(max_length=64)
    authorization_consumed_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    bot_digest: str = Field(max_length=64)
    recording_digest: str | None = Field(default=None, max_length=64)
    terminal_status: str | None = Field(default=None, max_length=32)
    terminal_updated_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
