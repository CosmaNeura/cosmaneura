from dataclasses import dataclass
from uuid import UUID

from decouple import config

AGENT_API_ACCESS_ROLE = "AgentApi.Access"
SCRIBE_TRANSCRIPT_WRITE_ROLE = "Scribe.Transcript.Write"


@dataclass(frozen=True)
class EntraServiceAuthSettings:
    enabled: bool
    tenant_id: str
    audience: str
    require_nbf: bool = False

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @property
    def jwks_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"

    def is_complete(self) -> bool:
        try:
            UUID(self.tenant_id)
            UUID(self.audience)
        except ValueError:
            return False
        return True


@dataclass(frozen=True)
class EntraServiceAuthPolicy:
    """Caller and app-role restrictions for one Entra-protected workload API."""

    settings: EntraServiceAuthSettings
    allowed_client_ids: frozenset[str]
    required_role: str

    def is_complete(self) -> bool:
        if (
            not self.settings.is_complete()
            or not self.allowed_client_ids
            or not self.required_role
        ):
            return False

        try:
            for client_id in self.allowed_client_ids:
                UUID(client_id)
        except ValueError:
            return False
        return True


def _client_id_allowlist(value: str) -> frozenset[str]:
    return frozenset(
        client_id.strip() for client_id in value.split(",") if client_id.strip()
    )


SCRIBE_SERVICE_AUTH_SETTINGS = EntraServiceAuthSettings(
    enabled=config("SCRIBE_HANDOFF_ENABLED", default=False, cast=bool),
    tenant_id=config("ENTRA_TENANT_ID", default="", cast=str),
    audience=config("SCRIBE_API_AUDIENCE", default="", cast=str),
)

SCRIBE_SERVICE_AUTH_POLICY = EntraServiceAuthPolicy(
    settings=SCRIBE_SERVICE_AUTH_SETTINGS,
    allowed_client_ids=_client_id_allowlist(
        config("SCRIBE_CALLER_CLIENT_ID", default="", cast=str)
    ),
    required_role=SCRIBE_TRANSCRIPT_WRITE_ROLE,
)

AGENT_API_AUTH_SETTINGS = EntraServiceAuthSettings(
    enabled=config("AGENT_API_ENABLED", default=False, cast=bool),
    tenant_id=config("ENTRA_TENANT_ID", default="", cast=str),
    audience=config("AGENT_API_AUDIENCE", default="", cast=str),
    require_nbf=True,
)

AGENT_API_AUTH_POLICY = EntraServiceAuthPolicy(
    settings=AGENT_API_AUTH_SETTINGS,
    allowed_client_ids=_client_id_allowlist(
        config("AGENT_API_SOVEREIGN_CLIENT_IDS", default="", cast=str)
    ),
    required_role=AGENT_API_ACCESS_ROLE,
)
