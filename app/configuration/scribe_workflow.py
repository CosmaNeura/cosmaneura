from dataclasses import dataclass

from decouple import config

SCRIBE_SERVICE_TIMEOUT_SECONDS_LIMIT = 60


@dataclass(frozen=True)
class ScribeWorkflowSettings:
    enabled: bool
    base_url: str
    scope: str
    managed_identity_client_id: str
    timeout_seconds: float

    def __post_init__(self) -> None:
        if not 1 <= self.timeout_seconds <= SCRIBE_SERVICE_TIMEOUT_SECONDS_LIMIT:
            raise ValueError(
                "Scribe service timeout must be between 1 and "
                f"{SCRIBE_SERVICE_TIMEOUT_SECONDS_LIMIT} seconds"
            )

    def is_complete(self) -> bool:
        return bool(
            self.base_url.startswith("https://")
            and self.scope.endswith("/.default")
            and self.managed_identity_client_id
        )


SCRIBE_WORKFLOW_SETTINGS = ScribeWorkflowSettings(
    enabled=config("SCRIBE_WORKFLOW_ENABLED", default=False, cast=bool),
    base_url=config("SCRIBE_SERVICE_BASE_URL", default="", cast=str).rstrip("/"),
    scope=config("SCRIBE_SERVICE_SCOPE", default="", cast=str),
    managed_identity_client_id=config(
        "SCRIBE_MANAGED_IDENTITY_CLIENT_ID", default="", cast=str
    ),
    # Terraform supplies this value to the Container App. The matching fallback keeps
    # local and other non-Terraform entry points on the same request-timeout contract.
    timeout_seconds=config(
        "SCRIBE_SERVICE_TIMEOUT_SECONDS",
        default=SCRIBE_SERVICE_TIMEOUT_SECONDS_LIMIT,
        cast=float,
    ),
)
