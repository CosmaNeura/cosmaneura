"""Reversible rollout gate for the provider-entered encounter workflow."""

from dataclasses import dataclass

from decouple import config


@dataclass(frozen=True)
class ScribeEncounterSettings:
    enabled: bool = False


SCRIBE_ENCOUNTER_SETTINGS = ScribeEncounterSettings(
    enabled=config("SCRIBE_ENCOUNTER_CREATION_ENABLED", default=False, cast=bool),
)
