from datetime import datetime
from enum import Enum

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import SoftDeletionMixin, TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Field, Relationship

from .doctor import Doctor


class CalendarProvider(str, Enum):
    google_calendar = "google_calendar"
    microsoft_outlook = "microsoft_outlook"


class RecallCalendar(
    BaseModel, TimestampsMixin, SoftDeletionMixin, TypeIDMixin("recall_calendar"), table=True
):
    """
    Represents a calendar connection to Recall AI.
    
    After a doctor connects their Google or Microsoft calendar via OAuth,
    we create a RecallCalendar record and register it with Recall AI.
    Recall AI then monitors this calendar for events.
    """

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    recall_calendar_id: str | None = Field(default=None, nullable=True)
    "Recall AI's calendar ID after registration. Set when calendar is created in Recall AI."

    provider: CalendarProvider = Field()
    "Calendar provider: google_calendar or microsoft_outlook"

    oauth_refresh_token: str = Field()
    "OAuth refresh token (encrypted at rest). Used to get access tokens for calendar API calls."

    oauth_client_id: str = Field()
    "OAuth client ID from our OAuth app registration with Google/Microsoft."

    is_active: bool = Field(default=True)
    "Whether this calendar connection is active. Set to False when disconnected."

    last_synced_at: datetime | None = Field(
        default=None,
        nullable=True,
        sa_type=sa.DateTime(timezone=True),  # type: ignore
    )
    "Last time we synced events from this calendar."
