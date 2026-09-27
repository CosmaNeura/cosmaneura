"""Separate, default-off read gate; it grants no capture or edit authority."""

from dataclasses import dataclass

from decouple import config


@dataclass(frozen=True)
class CompanionRecordSettings:
    enabled: bool = False

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("invalid_companion_record_settings")


COMPANION_RECORD_SETTINGS = CompanionRecordSettings(
    enabled=config("COMPANION_RECORD_READ_ENABLED", default=False, cast=bool),
)
