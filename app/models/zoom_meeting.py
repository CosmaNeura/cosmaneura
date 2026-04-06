from datetime import datetime
from enum import Enum

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field

from app.models.doctor import Doctor


class ZoomMeetingStatus(str, Enum):
    scheduled = "scheduled"
    started = "started"
    ended = "ended"
    cancelled = "cancelled"


class ZoomMeeting(BaseModel, TimestampsMixin, TypeIDMixin("zmtg"), table=True):
    doctor_id: TypeIDType = Doctor.foreign_key()
    "FK to Doctor"

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
    "Set by webhook when meeting starts and a Cosma Appointment is created"
