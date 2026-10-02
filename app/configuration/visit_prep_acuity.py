"""Default-off Acuity booking source; no service calls or account provisioning."""

import json
from dataclasses import dataclass, field
from uuid import UUID
from zoneinfo import ZoneInfo

from decouple import config
from typeid import TypeID

from app.configuration.visit_prep import _parse_bool
from app.services.visit_prep_command_contract import _normalized_text


@dataclass(frozen=True)
class AcuityCalendarBinding:
    calendar_id: int
    practice_id: str
    owner_practice_member_id: str
    owner_doctor_id: str
    timezone_name: str
    physician_display_name: str


@dataclass(frozen=True, repr=False)
class AcuityBookingSettings:
    enabled: bool = False
    connection_id: UUID | None = None
    account_id: str = field(default="", repr=False)
    api_key: str = field(default="", repr=False)
    calendars: tuple[AcuityCalendarBinding, ...] = ()

    def __repr__(self):
        return "AcuityBookingSettings(<redacted>)"


def load_acuity_booking_settings(values=None) -> AcuityBookingSettings:
    def read(name, default=""):
        return (
            values.get(name, default)
            if values is not None
            else config(name, default=default)
        )

    def unique_object(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError
        return result

    try:
        if not _parse_bool(read("VISIT_PREP_ACUITY_ENABLED", "false")):
            return AcuityBookingSettings()
        connection = UUID(read("VISIT_PREP_ACUITY_CONNECTION_ID"))
        account, key = (
            read("VISIT_PREP_ACUITY_ACCOUNT_ID"),
            read("VISIT_PREP_ACUITY_API_KEY"),
        )
        if (
            not connection.int
            or not isinstance(account, str)
            or not account.isascii()
            or not account.isdigit()
            or not 1 <= len(account) <= 20
            or int(account) <= 0
            or account != str(int(account))
        ):
            raise ValueError
        if (
            not isinstance(key, str)
            or not 16 <= len(key) <= 256
            or not key.isascii()
            or any(not 33 <= ord(c) <= 126 for c in key)
        ):
            raise ValueError
        encoded = read("VISIT_PREP_ACUITY_CALENDAR_BINDINGS", "[]")
        if not isinstance(encoded, str) or len(encoded) > 16384:
            raise ValueError
        entries = json.loads(encoded, object_pairs_hook=unique_object)
        if not isinstance(entries, list) or not 1 <= len(entries) <= 20:
            raise ValueError
        bindings = []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != set(
                AcuityCalendarBinding.__dataclass_fields__
            ):
                raise ValueError
            binding = AcuityCalendarBinding(**entry)
            if (
                type(binding.calendar_id) is not int
                or not 0 < binding.calendar_id <= 9_223_372_036_854_775_807
            ):
                raise ValueError
            for value, prefix in (
                (binding.practice_id, "practice"),
                (binding.owner_practice_member_id, "practice_member"),
                (binding.owner_doctor_id, "doctor"),
            ):
                parsed = TypeID.from_string(value)
                if (
                    parsed.prefix != prefix
                    or str(parsed) != value
                    or not parsed.uuid.int
                ):
                    raise ValueError
            ZoneInfo(binding.timezone_name)
            if (
                _normalized_text(
                    binding.physician_display_name,
                    maximum=160,
                    message="booking configuration invalid",
                )
                != binding.physician_display_name
            ):
                raise ValueError
            bindings.append(binding)
        if len({b.calendar_id for b in bindings}) != len(bindings):
            raise ValueError
        return AcuityBookingSettings(True, connection, account, key, tuple(bindings))
    except (ValueError, TypeError, AttributeError, KeyError, RecursionError):
        return AcuityBookingSettings()


SETTINGS = load_acuity_booking_settings()
