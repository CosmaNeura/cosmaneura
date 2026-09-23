"""Separate, default-off authority gate for first-party capture integration."""

from dataclasses import dataclass
from urllib.parse import urlsplit

from decouple import config


@dataclass(frozen=True)
class ScribeCaptureSettings:
    enabled: bool = False
    upload_origins: tuple[str, ...] = ()
    notes_enabled: bool = False
    # Founder-owned, no active cohort; review by 2026-09-30.
    working_transcripts_enabled: bool = False
    # Founder-owned, no active cohort; review by 2026-09-30.
    recovery_enabled: bool = False
    # Founder-owned, no active cohort; review by 2026-09-30.
    source_envelopes_enabled: bool = False
    # Local first-party encounter context, independent of recording admission.
    prep_enabled: bool = False
    # Founder-owned, local synthetic cohort only; review by 2026-10-20.
    long_visits_enabled: bool = False

    def __post_init__(self) -> None:
        for origin in self.upload_origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.port not in (None, 443)
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
                or any(c.isspace() for c in origin)
                or "\\" in origin
            ):
                raise ValueError("Capture uploads require exact HTTPS origins")


SCRIBE_CAPTURE_SETTINGS = ScribeCaptureSettings(
    enabled=config("SCRIBE_CAPTURE_ENABLED", default=False, cast=bool),
    notes_enabled=config("SCRIBE_CAPTURE_NOTES_ENABLED", default=False, cast=bool),
    prep_enabled=config("SCRIBE_ENCOUNTER_PREP_ENABLED", default=False, cast=bool),
    long_visits_enabled=config("SCRIBE_CAPTURE_LONG_VISITS_ENABLED", default=False, cast=bool),
    working_transcripts_enabled=config(
        "SCRIBE_CAPTURE_WORKING_TRANSCRIPTS_ENABLED", default=False, cast=bool
    ),
    recovery_enabled=config("SCRIBE_CAPTURE_RECOVERY_ENABLED", default=False, cast=bool),
    source_envelopes_enabled=config("SCRIBE_CAPTURE_SOURCE_ENABLED", default=False, cast=bool),
    upload_origins=tuple(
        filter(
            None,
            config("SCRIBE_CAPTURE_UPLOAD_ORIGINS", default="", cast=str).split(","),
        )
    ),
)
