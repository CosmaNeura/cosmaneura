"""Provider-neutral, chart-free Visit Prep source projection, not roster identity."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

from app.services.visit_prep_command_contract import RecipientInput

from activemodel.types import TypeIDType


class BookingSourceUnavailable(ValueError):
    def __init__(self):
        super().__init__("Visit Prep booking is unavailable.")


@dataclass(frozen=True, repr=False)
class BookingSourceScope:
    source: Literal["acuity"]
    connection_id: UUID
    practice_id: TypeIDType
    owner_practice_member_id: TypeIDType
    owner_doctor_id: TypeIDType
    physician_display_name: str = field(repr=False)

    def __repr__(self):
        return "BookingSourceScope(<redacted>)"


@dataclass(frozen=True, repr=False)
class NormalizedVisitPrepBooking:
    booking_digest: str = field(repr=False)
    source_snapshot_hmac: str = field(repr=False)
    recipient: RecipientInput = field(repr=False)
    scheduled_at: datetime
    timezone_name: str

    def __repr__(self):
        return "NormalizedVisitPrepBooking(<redacted>)"


@dataclass(frozen=True, repr=False)
class CurrentVisitPrepBookingState:
    booking_digest: str = field(repr=False)
    source_snapshot_hmac: str = field(repr=False)
    observed_at: datetime
    active_booking: NormalizedVisitPrepBooking | None = field(repr=False)

    def __repr__(self):
        return "CurrentVisitPrepBookingState(<redacted>)"
