from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, Field, Relationship

from .recall_calendar import RecallCalendar

if TYPE_CHECKING:
    from .appointment import Appointment


class RecallEvent(
    BaseModel, TimestampsMixin, TypeIDMixin("recall_event"), table=True
):
    """
    Represents a calendar event synced from Google/Microsoft Calendar.

    We store the full event JSON to enable future filtering logic.
    When a bot is scheduled for this event, we track that status.
    When a recording is ready, we link it to an Appointment.
    """

    recall_calendar_id: TypeIDType = RecallCalendar.foreign_key()
    recall_calendar: RecallCalendar = Relationship()

    external_event_id: str = Field(unique=True, index=True)
    "Unique identifier from Google/Microsoft Calendar. Used to prevent duplicates."

    appointment_id: TypeIDType | None = Field(
        default=None,
        sa_column=Column(
            TypeIDType("appointment"),
            ForeignKey("appointment.id"),
            nullable=True,
        ),
    )
    "Linked Appointment when recording is processed. Nullable because we sync events before recordings are ready."

    appointment: "Appointment | None" = Relationship()

    event_start_time: datetime = Field(
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    "Start time of the calendar event."

    event_end_time: datetime = Field(
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    "End time of the calendar event."

    event_title: str | None = Field(default=None, nullable=True)
    "Title/summary of the calendar event."

    event_description: dict = Field(
        default_factory=dict,
        sa_type=JSONB,
    )
    "Full event JSON from Google/Microsoft Calendar API. Stored for future filtering logic."

    bot_scheduled: bool = Field(default=False)
    "Whether a Recall AI bot has been scheduled for this event."

    recall_bot_id: str | None = Field(default=None, nullable=True)
    "Recall AI's bot ID after scheduling. Used for cancellation/rescheduling."

    recording_ready: bool = Field(default=False)
    "Whether the recording data has arrived from Recall AI. Used for UI queuing."
