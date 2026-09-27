"""Independent release control for Core's unlaunched lab toolkit."""

from decouple import config

from app.services.reference_toolkit_gate import reference_toolkit_enabled_for

REFERENCE_LABS_ENABLED = config("REFERENCE_LABS_ENABLED", default=False, cast=bool)


def reference_labs_enabled_for(email: str | None) -> bool:
    """Lab activation can only narrow the existing reference-toolkit cohort."""
    return bool(REFERENCE_LABS_ENABLED and reference_toolkit_enabled_for(email))
