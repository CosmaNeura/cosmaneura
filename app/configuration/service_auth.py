from dataclasses import dataclass
from uuid import UUID

from decouple import config

SCRIBE_TRANSCRIPT_WRITE_ROLE = "Scribe.Transcript.Write"


@dataclass(frozen=True)
class EntraServiceAuthSettings:
    enabled: bool
    tenant_id: str
    audience: str
    scribe_client_id: str
    scribe_reconciler_client_id: str

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @property
    def jwks_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"

    @property
    def allowed_client_ids(self) -> frozenset[str]:
        """Return the canonical, deployment-owned physical caller allowlist."""

        try:
            return frozenset(
                {
                    str(UUID(self.scribe_client_id)),
                    str(UUID(self.scribe_reconciler_client_id)),
                }
            )
        except (ValueError, TypeError, AttributeError):
            return frozenset()

    def is_complete(self) -> bool:
        try:
            UUID(self.tenant_id)
            UUID(self.audience)
            UUID(self.scribe_client_id)
            UUID(self.scribe_reconciler_client_id)
        except (ValueError, TypeError, AttributeError):
            return False
        return len(self.allowed_client_ids) == 2


SERVICE_AUTH_SETTINGS = EntraServiceAuthSettings(
    enabled=config("SCRIBE_HANDOFF_ENABLED", default=False, cast=bool),
    tenant_id=config("ENTRA_TENANT_ID", default="", cast=str),
    audience=config("SCRIBE_API_AUDIENCE", default="", cast=str),
    scribe_client_id=config("SCRIBE_CALLER_CLIENT_ID", default="", cast=str),
    scribe_reconciler_client_id=config(
        "SCRIBE_RECONCILER_CALLER_CLIENT_ID", default="", cast=str
    ),
)
