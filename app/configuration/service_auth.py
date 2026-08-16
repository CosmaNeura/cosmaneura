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
        except (ValueError, TypeError, AttributeError):
            return False
        return True


@dataclass(frozen=True)
class EntraServiceAuthPolicy:
    """Caller and app-role restrictions for one Entra-protected workload API."""

    settings: EntraServiceAuthSettings
    allowed_client_ids: frozenset[str]
    required_role: str
    required_client_count: int = 1
    log_namespace: str = "service_auth"

    def is_complete(self) -> bool:
        if (
            not self.settings.is_complete()
            or not self.allowed_client_ids
            or not self.required_role
            or len(self.allowed_client_ids) != self.required_client_count
        ):
            return False

        try:
            for client_id in self.allowed_client_ids:
                UUID(client_id)
        except (ValueError, TypeError, AttributeError):
            return False
        return True


def _client_id_allowlist(*values: str) -> frozenset[str]:
    """Canonicalize UUIDs while retaining invalid values so policy fails closed."""

    client_ids: set[str] = set()
    for value in values:
        for candidate in value.split(","):
            candidate = candidate.strip()
            if not candidate:
                continue
            try:
                client_ids.add(str(UUID(candidate)))
            except (ValueError, TypeError, AttributeError):
                client_ids.add(candidate)
    return frozenset(client_ids)


SCRIBE_SERVICE_AUTH_SETTINGS = EntraServiceAuthSettings(
    enabled=config("SCRIBE_HANDOFF_ENABLED", default=False, cast=bool),
    tenant_id=config("ENTRA_TENANT_ID", default="", cast=str),
    audience=config("SCRIBE_API_AUDIENCE", default="", cast=str),
)

SCRIBE_SERVICE_AUTH_POLICY = EntraServiceAuthPolicy(
    settings=SCRIBE_SERVICE_AUTH_SETTINGS,
    allowed_client_ids=_client_id_allowlist(
        config("SCRIBE_CALLER_CLIENT_ID", default="", cast=str),
        config("SCRIBE_RECONCILER_CALLER_CLIENT_ID", default="", cast=str),
    ),
    required_role=SCRIBE_TRANSCRIPT_WRITE_ROLE,
    required_client_count=2,
    log_namespace="scribe_handoff",
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
    log_namespace="agent_api",
)
