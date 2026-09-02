from dataclasses import dataclass

from decouple import config


@dataclass(frozen=True)
class SessionPrepSyntheticSettings:
    """Fail-closed controls for the non-PHI Session Prep demonstration."""

    enabled: bool
    context_snapshot_ref: str

    def is_complete(self) -> bool:
        return bool(
            self.enabled
            and self.context_snapshot_ref
            and self.context_snapshot_ref.startswith("context_synthetic_")
        )


SESSION_PREP_SYNTHETIC_SETTINGS = SessionPrepSyntheticSettings(
    enabled=config("SESSION_PREP_SYNTHETIC_ENABLED", default=False, cast=bool),
    context_snapshot_ref=config(
        "SESSION_PREP_SYNTHETIC_CONTEXT_SNAPSHOT_REF",
        default="",
        cast=str,
    ),
)
