"""Default-off account discovery and encounter selection; no authority issuance."""

from dataclasses import dataclass

from decouple import config


@dataclass(frozen=True)
class CompanionEncounterSettings:
    enabled: bool = False

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("invalid_companion_encounter_settings")


COMPANION_ENCOUNTER_SETTINGS = CompanionEncounterSettings(
    enabled=config("COMPANION_ENCOUNTER_READ_ENABLED", default=False, cast=bool),
)
