from datetime import datetime
from enum import Enum

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from app.models.doctor import Doctor
from sqlmodel import Column, Field, Relationship, UniqueConstraint


class ZoomMeetingStatus(str, Enum):
    scheduled = "scheduled"
    started = "started"
    ended = "ended"
    cancelled = "cancelled"


class ZoomMeeting(BaseModel, TimestampsMixin, TypeIDMixin("zmtg"), table=True):
    __table_args__ = (
        UniqueConstraint(
            "doctor_id", "zoom_meeting_id", name="uq_zoom_meeting_doctor_meeting"
        ),
        sa.CheckConstraint(
            "capture_authorization_occurrence_digest IS NULL OR "
            "capture_authorization_occurrence_digest ~ '^[0-9a-f]{64}$'",
            name="ck_zoom_meeting_capture_authorization_occurrence_digest",
        ),
        sa.CheckConstraint(
            "(capture_authorization_occurrence_digest IS NULL AND "
            "capture_authorization_expires_at IS NULL AND "
            "capture_authorization_consumed_at IS NULL) OR "
            "(appointment_id IS NOT NULL AND "
            "capture_authorization_occurrence_digest IS NOT NULL AND "
            "capture_authorization_expires_at IS NOT NULL)",
            name="ck_zoom_meeting_capture_authorization_complete",
        ),
    )

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    zoom_meeting_id: str = Field(index=True)
    "Zoom's numeric meeting ID, stored as string"

    topic: str
    "Meeting topic/title from Zoom"

    start_time: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
    "Scheduled start time, timezone-aware"

    duration: int
    "Meeting duration in minutes"

    join_url: str
    "Zoom join URL for attendees"

    status: ZoomMeetingStatus = Field(default=ZoomMeetingStatus.scheduled)
    "Lifecycle status of the meeting"

    appointment_id: TypeIDType | None = Field(
        default=None,
        sa_column=Column(TypeIDType("appointment"), nullable=True),
    )
    "Soft reference to Appointment retained for schedule-to-note navigation"

    capture_authorization_occurrence_digest: str | None = Field(
        default=None,
        max_length=64,
    )
    "HMAC digest of the one Zoom occurrence authorized to capture this appointment"

    capture_authorization_expires_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    "Deadline after which this capture authorization fails closed"

    capture_authorization_consumed_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    "Set atomically before the authorized occurrence creates a Recall bot"
