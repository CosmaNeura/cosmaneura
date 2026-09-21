from dataclasses import dataclass

from decouple import config


@dataclass(frozen=True)
class SessionPrepSyntheticSettings:
    """Fail-closed controls for the non-PHI Session Prep demonstration."""

    enabled: bool
    context_snapshot_ref: str
    clerk_subject: str
    appointment_id: str
    fixture_id: str

    def is_complete(self) -> bool:
        return bool(
            self.enabled
            and self.context_snapshot_ref
            and self.context_snapshot_ref.startswith("context_synthetic_")
            and self.clerk_subject.startswith("user_")
            and self.appointment_id.startswith("appointment_")
            and self.fixture_id.startswith("synthetic_preview_fixture_")
        )


SESSION_PREP_SYNTHETIC_SETTINGS = SessionPrepSyntheticSettings(
    enabled=config("SESSION_PREP_SYNTHETIC_ENABLED", default=False, cast=bool),
    context_snapshot_ref=config(
        "SESSION_PREP_SYNTHETIC_CONTEXT_SNAPSHOT_REF",
        default="",
        cast=str,
    ),
    clerk_subject=config(
        "SESSION_PREP_SYNTHETIC_CLERK_SUBJECT",
        default="",
        cast=str,
    ),
    appointment_id=config(
        "SESSION_PREP_SYNTHETIC_APPOINTMENT_ID",
        default="",
        cast=str,
    ),
    fixture_id=config(
        "SESSION_PREP_SYNTHETIC_FIXTURE_ID",
        default="",
        cast=str,
    ),
)
