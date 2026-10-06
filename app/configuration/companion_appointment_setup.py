"""Explicit, default-off onboarding of existing Core appointment references."""

from dataclasses import dataclass

from decouple import config

from app.configuration.authority import AUTHORITY_KERNEL_SETTINGS


@dataclass(frozen=True)
class CompanionAppointmentSetupSettings:
    enabled: bool = False

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError("invalid_companion_appointment_setup_settings")
        if self.enabled and (
            not AUTHORITY_KERNEL_SETTINGS.enabled
            or not AUTHORITY_KERNEL_SETTINGS.is_complete()
        ):
            raise ValueError("companion_appointment_setup_authority_required")


COMPANION_APPOINTMENT_SETUP_SETTINGS = CompanionAppointmentSetupSettings(
    enabled=config("COMPANION_APPOINTMENT_SETUP_ENABLED", default=False, cast=bool),
)
