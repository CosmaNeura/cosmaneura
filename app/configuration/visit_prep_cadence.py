"""Centrally managed standard practice timing, read-only to providers.

No provider setting writer, patient data, environment mutation or send gate.
The owner-approved default is 21:00–08:00 local quiet hours and four-hour channel
spacing. A partial or malformed override disables standard dispatch. All eligible
practices use the same configured standard for now.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta

from decouple import config

_VERSION = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_INTEGER = re.compile(r"^(0|[1-9][0-9]?)$")


@dataclass(frozen=True)
class VisitPrepCadenceSettings:
    version: str
    quiet_start_hour: int
    quiet_end_hour: int
    minimum_channel_spacing_hours: int

    def __post_init__(self) -> None:
        if (
            type(self.version) is not str
            or _VERSION.fullmatch(self.version) is None
            or any(
                type(hour) is not int or not 0 <= hour <= 23
                for hour in (self.quiet_start_hour, self.quiet_end_hour)
            )
            or self.quiet_start_hour == self.quiet_end_hour
            or type(self.minimum_channel_spacing_hours) is not int
            or not 1 <= self.minimum_channel_spacing_hours <= 48
        ):
            raise ValueError("Visit Prep standard timing is invalid.")

    @property
    def quiet_hours(self) -> tuple[int, int]:
        return self.quiet_start_hour, self.quiet_end_hour

    @property
    def minimum_channel_spacing(self) -> timedelta:
        return timedelta(hours=self.minimum_channel_spacing_hours)

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(
            {
                "contract": "visit_prep.standard_cadence.v1",
                "version": self.version,
                "quiet_hours": self.quiet_hours,
                "minimum_channel_spacing_hours": self.minimum_channel_spacing_hours,
                "timezone_basis": "current_appointment",
                "steps": [
                    "booking_email",
                    "booking_sms",
                    "unopened_48h_sms",
                    "unfinished_12h_sms",
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        return hashlib.sha256(encoded).hexdigest()


DEFAULT_VISIT_PREP_CADENCE = VisitPrepCadenceSettings(
    version="standard-20261002",
    quiet_start_hour=21,
    quiet_end_hour=8,
    minimum_channel_spacing_hours=4,
)
_NAMES = (
    "VISIT_PREP_STANDARD_CADENCE_VERSION",
    "VISIT_PREP_STANDARD_QUIET_START_HOUR",
    "VISIT_PREP_STANDARD_QUIET_END_HOUR",
    "VISIT_PREP_STANDARD_CHANNEL_SPACING_HOURS",
)


def load_visit_prep_cadence_settings(
    values: Mapping[str, object] | None = None,
) -> VisitPrepCadenceSettings | None:
    """Use Core's existing configuration mechanism; never load partial rules."""

    absent = object()
    raw_values = {
        name: values.get(name, absent)
        if values is not None
        else config(name, default=absent)
        for name in _NAMES
    }
    if all(raw is absent for raw in raw_values.values()):
        return DEFAULT_VISIT_PREP_CADENCE
    if any(raw is absent for raw in raw_values.values()):
        return None

    def integer(name: str) -> int:
        raw = raw_values[name]
        if type(raw) is not str or _INTEGER.fullmatch(raw) is None:
            raise ValueError("Visit Prep standard timing is invalid.")
        return int(raw)

    try:
        version = raw_values["VISIT_PREP_STANDARD_CADENCE_VERSION"]
        if type(version) is not str:
            return None
        return VisitPrepCadenceSettings(
            version=version,
            quiet_start_hour=integer("VISIT_PREP_STANDARD_QUIET_START_HOUR"),
            quiet_end_hour=integer("VISIT_PREP_STANDARD_QUIET_END_HOUR"),
            minimum_channel_spacing_hours=integer(
                "VISIT_PREP_STANDARD_CHANNEL_SPACING_HOURS"
            ),
        )
    except (AttributeError, TypeError, ValueError):
        return None


VISIT_PREP_CADENCE_SETTINGS = load_visit_prep_cadence_settings()


def current_standard_cadence() -> VisitPrepCadenceSettings | None:
    """Only centrally loaded, strictly validated policy is authoritative."""
    value = VISIT_PREP_CADENCE_SETTINGS
    if type(value) is not VisitPrepCadenceSettings:
        return None
    try:
        value.__post_init__()
    except (AttributeError, TypeError, ValueError):
        return None
    return value
