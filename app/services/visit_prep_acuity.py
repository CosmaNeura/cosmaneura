"""Acuity boundary: verify bytes, fetch current booking, discard clinical fields.

Native notifications have no unique event version. Never treat their body hash
or action as authoritative current state; re-fetch the appointment instead.
"""

import base64
import hashlib
import hmac
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import cast
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

import httpx
from typeid import TypeID

from app.configuration import visit_prep_acuity
from app.configuration.visit_prep_acuity import (
    AcuityBookingSettings,
    AcuityCalendarBinding,
)
from app.services.visit_prep_booking_contract import (
    BookingSourceScope,
    CurrentVisitPrepBookingState,
    NormalizedVisitPrepBooking,
)
from app.services.visit_prep_booking_contract import (
    BookingSourceUnavailable as AcuityBookingUnavailable,
)
from app.services.visit_prep_command_contract import (
    RecipientInput,
    _normalize_recipient,
    _normalized_schedule,
)
from app.utils.encryption import pseudonymous_digest

from activemodel.types import TypeIDType


@dataclass(frozen=True, repr=False)
class CurrentAcuityBooking:
    appointment_id: int
    calendar_id: int
    recipient: RecipientInput = field(repr=False)
    scheduled_at: datetime
    timezone_name: str

    def __repr__(self):
        return "CurrentAcuityBooking(<redacted>)"


@dataclass(frozen=True, repr=False)
class CurrentAcuityCancellation:
    appointment_id: int
    calendar_id: int

    def __repr__(self):
        return "CurrentAcuityCancellation(<redacted>)"


def require_current_booking_source(
    binding: AcuityCalendarBinding, settings: AcuityBookingSettings
) -> None:
    if (
        not settings.enabled
        or settings.connection_id is None
        or settings != visit_prep_acuity.SETTINGS
        or binding not in settings.calendars
    ):
        raise AcuityBookingUnavailable()


def project_current_booking(
    *,
    booking: CurrentAcuityBooking,
    binding: AcuityCalendarBinding,
    settings: AcuityBookingSettings,
) -> tuple[BookingSourceScope, NormalizedVisitPrepBooking]:
    """Keep Acuity identifiers and compatible source hashing inside its adapter."""
    if booking.calendar_id != binding.calendar_id:
        raise AcuityBookingUnavailable()
    source, key = _source_reference(booking.appointment_id, binding, settings)
    recipient = _normalize_recipient(booking.recipient)
    scheduled, timezone = _normalized_schedule(
        booking.scheduled_at,
        booking.timezone_name,
        now=datetime.now(UTC),
        require_future=False,
    )
    # Preserve the already-published receipt format; do not re-key old bookings.
    snapshot = pseudonymous_digest(
        json.dumps(
            {
                "booking": key,
                "owner": str(source.owner_practice_member_id),
                "practice": str(source.practice_id),
                "calendar": booking.calendar_id,
                "recipient": asdict(recipient),
                "scheduled": scheduled.isoformat(),
                "timezone": timezone,
            },
            sort_keys=True,
        ),
        context="visit-prep-booking-snapshot-v1",
    )
    return source, NormalizedVisitPrepBooking(
        key, snapshot, booking.recipient, booking.scheduled_at, booking.timezone_name
    )


def _source_reference(
    appointment_id: int, binding: AcuityCalendarBinding, settings: AcuityBookingSettings
) -> tuple[BookingSourceScope, str]:
    require_current_booking_source(binding, settings)
    assert settings.connection_id is not None
    source = BookingSourceScope(
        source="acuity",
        connection_id=settings.connection_id,
        practice_id=cast(TypeIDType, TypeID.from_string(binding.practice_id)),
        owner_practice_member_id=cast(
            TypeIDType, TypeID.from_string(binding.owner_practice_member_id)
        ),
        owner_doctor_id=cast(TypeIDType, TypeID.from_string(binding.owner_doctor_id)),
        physician_display_name=binding.physician_display_name,
    )
    key = pseudonymous_digest(
        json.dumps(
            {
                "source": source.source,
                "connection": str(source.connection_id),
                "appointment": appointment_id,
            },
            sort_keys=True,
        ),
        context="visit-prep-booking-reference-v1",
    )
    return source, key


def project_current_booking_state(
    *,
    booking: CurrentAcuityBooking | CurrentAcuityCancellation,
    binding: AcuityCalendarBinding,
    settings: AcuityBookingSettings,
    observed_at: datetime,
) -> tuple[BookingSourceScope, CurrentVisitPrepBookingState]:
    if isinstance(booking, CurrentAcuityBooking):
        source, active = project_current_booking(
            booking=booking, binding=binding, settings=settings
        )
        return source, CurrentVisitPrepBookingState(
            active.booking_digest, active.source_snapshot_hmac, observed_at, active
        )
    if booking.calendar_id != binding.calendar_id:
        raise AcuityBookingUnavailable()
    source, key = _source_reference(booking.appointment_id, binding, settings)
    snapshot = pseudonymous_digest(
        json.dumps(
            {
                "booking": key,
                "owner": str(source.owner_practice_member_id),
                "practice": str(source.practice_id),
                "calendar": binding.calendar_id,
                "canceled": True,
            },
            sort_keys=True,
        ),
        context="visit-prep-booking-snapshot-v1",
    )
    return source, CurrentVisitPrepBookingState(key, snapshot, observed_at, None)


def verify_scheduled_notification(
    body: bytes, signature: str, settings: AcuityBookingSettings
):
    return verify_booking_notification(body, signature, settings, scheduled_only=True)


def verify_booking_notification(
    body: bytes,
    signature: str,
    settings: AcuityBookingSettings,
    *,
    scheduled_only: bool = False,
):
    if (
        not settings.enabled
        or settings.connection_id is None
        or not settings.api_key
        or not 1 <= len(body) <= 1024
    ):
        raise AcuityBookingUnavailable()
    expected = base64.b64encode(
        hmac.new(settings.api_key.encode("ascii"), body, hashlib.sha256).digest()
    ).decode("ascii")
    if (
        not isinstance(signature, str)
        or len(signature) != 44
        or not signature.isascii()
        or not hmac.compare_digest(expected, signature)
    ):
        raise AcuityBookingUnavailable()
    try:
        pairs = parse_qsl(
            body.decode("ascii"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=4,
        )
        fields = dict(pairs)
        if (
            len(fields) != len(pairs)
            or set(fields) != {"action", "id", "calendarID", "appointmentTypeID"}
            or fields["action"]
            not in (
                {"scheduled"}
                if scheduled_only
                else {"changed", "rescheduled", "canceled"}
            )
        ):
            raise ValueError
        numbers = []
        for name in ("id", "calendarID", "appointmentTypeID"):
            raw = fields[name]
            if (
                not raw.isascii()
                or not raw.isdigit()
                or not 1 <= len(raw) <= 19
                or raw != str(int(raw))
                or not 0 < int(raw) <= 9_223_372_036_854_775_807
            ):
                raise ValueError
            numbers.append(int(raw))
        return numbers[0], numbers[1]
    except (ValueError, TypeError, UnicodeError):
        pass
    raise AcuityBookingUnavailable() from None


def _object(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise AcuityBookingUnavailable()
    return value


def fetch_current_booking(
    *,
    appointment_id: int,
    binding: AcuityCalendarBinding,
    settings: AcuityBookingSettings,
) -> CurrentAcuityBooking:
    booking = fetch_current_booking_state(
        appointment_id=appointment_id, binding=binding, settings=settings
    )
    if not isinstance(booking, CurrentAcuityBooking):
        raise AcuityBookingUnavailable()
    return booking


def fetch_current_booking_state(
    *,
    appointment_id: int,
    binding: AcuityCalendarBinding,
    settings: AcuityBookingSettings,
) -> CurrentAcuityBooking | CurrentAcuityCancellation:
    # Fixed origin/path, no redirects, SDK logging, caller URL, transport retry,
    # forms history, patient chart lookup or raw exception propagation.
    try:
        if (
            not settings.enabled
            or settings != visit_prep_acuity.SETTINGS
            or binding not in settings.calendars
            or type(appointment_id) is not int
            or not 0 < appointment_id <= 9_223_372_036_854_775_807
        ):
            raise ValueError
        started = monotonic()
        with httpx.Client(
            timeout=httpx.Timeout(8.0, connect=3.0),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            with client.stream(
                "GET",
                f"https://acuityscheduling.com/api/v1/appointments/{appointment_id}",
                auth=(settings.account_id, settings.api_key),
                headers={"Accept": "application/json"},
            ) as response:
                if response.status_code != 200:
                    raise ValueError
                raw = bytearray()
                for chunk in response.iter_bytes():
                    if len(raw) + len(chunk) > 65536 or monotonic() - started >= 8:
                        raise ValueError
                    raw.extend(chunk)
        if monotonic() - started >= 8:
            raise ValueError
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
        if (
            not isinstance(value, dict)
            or type(value.get("id")) is not int
            or value["id"] != appointment_id
            or type(value.get("calendarID")) is not int
            or value["calendarID"] != binding.calendar_id
        ):
            raise ValueError
        canceled = value.get("canceled", False)
        if type(canceled) is not bool:
            raise ValueError
        if canceled:
            # An identity-only tombstone is enough; never require or copy PHI.
            return CurrentAcuityCancellation(appointment_id, binding.calendar_id)
        scheduled = datetime.fromisoformat(value["datetime"])
        scheduled, timezone = _normalized_schedule(
            scheduled,
            binding.timezone_name,
            now=datetime.now(UTC),
            require_future=False,
        )
        recipient = RecipientInput(
            first_name=value["firstName"],
            role="patient",
            email=value.get("email") or None,
            phone=value.get("phone") or None,
        )
        _normalize_recipient(recipient)
        return CurrentAcuityBooking(
            appointment_id,
            binding.calendar_id,
            recipient,
            scheduled.astimezone(ZoneInfo(timezone)),
            timezone,
        )
    except Exception:
        # Provider responses may contain names, forms, contacts and credentials.
        # Raise outside the exception context so none reaches error telemetry.
        pass
    raise AcuityBookingUnavailable() from None
