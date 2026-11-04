"""Dark activation rollout, restricted to explicitly selected Core practices."""

from dataclasses import dataclass

from decouple import config

from app.configuration.authority import (
    AUTHORITY_KERNEL_SETTINGS,
    AuthorityKernelSettings,
)
from app.configuration.scribe_native import native_authority_settings


@dataclass(frozen=True)
class ScribeActivationSettings:
    enabled: bool
    practice_refs: frozenset[str]
    authority: AuthorityKernelSettings

    def allows_practice(self, practice_ref: str) -> bool:
        return self.enabled and practice_ref in self.practice_refs


SCRIBE_ACTIVATION_SETTINGS = ScribeActivationSettings(
    enabled=config("SCRIBE_AUTOMATIC_ACTIVATION_ENABLED", default=False, cast=bool),
    practice_refs=frozenset(
        value.strip()
        for value in config(
            "SCRIBE_ACTIVATION_PRACTICE_REFS", default="", cast=str
        ).split(",")
        if value.strip()
    ),
    authority=native_authority_settings(AUTHORITY_KERNEL_SETTINGS),
)
