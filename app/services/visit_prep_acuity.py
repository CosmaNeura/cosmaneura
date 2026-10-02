"""Acuity boundary: verify bytes, fetch current booking, discard clinical fields.

Native notifications have no unique event version. Never treat their body hash
as the identity of all future changes. This first increment handles scheduled
notifications only; changed/rescheduled/canceled require the later lifecycle lane.
"""

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

import httpx

from app.configuration import visit_prep_acuity
from app.configuration.visit_prep_acuity import (
    AcuityBookingSettings,
    AcuityCalendarBinding,
)
from app.services.visit_prep_command_contract import (
    RecipientInput,
    _normalize_recipient,
    _normalized_schedule,
)


class AcuityBookingUnavailable(ValueError):
    def __init__(self):
        super().__init__("Visit Prep booking is unavailable.")


@dataclass(frozen=True, repr=False)
class CurrentAcuityBooking:
    appointment_id: int
    calendar_id: int
    recipient: RecipientInput = field(repr=False)
    scheduled_at: datetime
    timezone_name: str

    def __repr__(self):
        return "CurrentAcuityBooking(<redacted>)"


def verify_scheduled_notification(
    body: bytes, signature: str, settings: AcuityBookingSettings
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
            or fields["action"] != "scheduled"
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
        if value.get("canceled", False) is not False:
            raise ValueError
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
