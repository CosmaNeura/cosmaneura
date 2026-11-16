from decouple import config

from app.environments import python_environment


def held_medication_writes_enabled() -> bool:
    """Allow new holds only after the production reader rollout is complete.

    This release control does not authorize a caller or change existing held
    rows. Reads, confirmation and explicit resume retain their normal gates.
    """

    return config(
        "HELD_MEDICATION_WRITES_ENABLED",
        default=python_environment() in {"development", "test", "staging"},
        cast=bool,
    )
