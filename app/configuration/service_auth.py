from dataclasses import dataclass, replace
from uuid import UUID

from decouple import config

AGENT_API_ACCESS_ROLE = "AgentApi.Access"
SCRIBE_TRANSCRIPT_WRITE_ROLE = "Scribe.Transcript.Write"


@dataclass(frozen=True)
class EntraServiceAuthSettings:
    enabled: bool
    tenant_id: str
    audience: str
    scribe_client_id: str = ""
    scribe_reconciler_client_id: str = ""
    require_nbf: bool = False

    @property
    def required_role(self) -> str:
        return SCRIBE_TRANSCRIPT_WRITE_ROLE

    @property
    def log_namespace(self) -> str:
        return "scribe_handoff"

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


@dataclass(frozen=True)
class EntraServiceAuthPolicy:
    """Reusable caller/role policy retained for focused unit construction."""

    settings: EntraServiceAuthSettings
    allowed_client_ids: frozenset[str]
    required_role: str
    required_client_count: int = 1
    log_namespace: str = "service_auth"

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    @property
    def tenant_id(self) -> str:
        return self.settings.tenant_id

    @property
    def audience(self) -> str:
        return self.settings.audience

    @property
    def issuer(self) -> str:
        return self.settings.issuer

    @property
    def jwks_url(self) -> str:
        return self.settings.jwks_url

    @property
    def require_nbf(self) -> bool:
        return self.settings.require_nbf

    def is_complete(self) -> bool:
        try:
            UUID(self.tenant_id)
            UUID(self.audience)
            canonical_ids = frozenset(
                str(UUID(client_id)) for client_id in self.allowed_client_ids
            )
        except (ValueError, TypeError, AttributeError):
            return False
        return (
            canonical_ids == self.allowed_client_ids
            and len(canonical_ids) == self.required_client_count
            and bool(self.required_role)
        )


@dataclass(frozen=True)
class EntraAgentAuthSettings:
    """Fail-closed policy for the provider Sovereign staging workload."""

    enabled: bool
    tenant_id: str
    audience: str
    sovereign_client_ids: frozenset[str]
    required_client_count: int = 1
    require_nbf: bool = True

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @property
    def jwks_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"

    @property
    def allowed_client_ids(self) -> frozenset[str]:
        return self.sovereign_client_ids

    @property
    def required_role(self) -> str:
        return AGENT_API_ACCESS_ROLE

    @property
    def log_namespace(self) -> str:
        return "agent_api"

    def is_complete(self) -> bool:
        try:
            UUID(self.tenant_id)
            UUID(self.audience)
            canonical_ids = frozenset(
                str(UUID(client_id)) for client_id in self.sovereign_client_ids
            )
        except (ValueError, TypeError, AttributeError):
            return False
        return (
            len(canonical_ids) == self.required_client_count
            and canonical_ids == self.sovereign_client_ids
        )


def _client_id_allowlist(value: str) -> frozenset[str]:
    """Canonicalize UUIDs while retaining invalid input so startup fails closed."""

    client_ids: set[str] = set()
    for candidate in value.split(","):
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            client_ids.add(str(UUID(candidate)))
        except (ValueError, TypeError, AttributeError):
            client_ids.add(candidate)
    return frozenset(client_ids)


SERVICE_AUTH_SETTINGS = EntraServiceAuthSettings(
    enabled=config("SCRIBE_HANDOFF_ENABLED", default=False, cast=bool),
    tenant_id=config("ENTRA_TENANT_ID", default="", cast=str),
    audience=config("SCRIBE_API_AUDIENCE", default="", cast=str),
    scribe_client_id=config("SCRIBE_CALLER_CLIENT_ID", default="", cast=str),
    scribe_reconciler_client_id=config(
        "SCRIBE_RECONCILER_CALLER_CLIENT_ID", default="", cast=str
    ),
)


# Optional additional receiver, with the existing one-caller policy unchanged
# when unset. This deployment-owned value is not accepted from request data.
_AGENT_SCRIBE_RECEIVER = config("AGENT_API_SCRIBE_RECEIVER_CLIENT_ID", default="", cast=str).strip()
_AGENT_SOVEREIGN_IDS = _client_id_allowlist(
    config("AGENT_API_SOVEREIGN_CLIENT_IDS", default="", cast=str)
)
_AGENT_PROVIDER_IDS = _AGENT_SOVEREIGN_IDS
_AGENT_CAPTURE_IDS: frozenset[str] = frozenset()
_AGENT_REQUIRED_CLIENT_COUNT = 1
if _AGENT_SCRIBE_RECEIVER:
    try:
        _receiver_id = UUID(_AGENT_SCRIBE_RECEIVER)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("agent_api_receiver_configuration_invalid") from None
    if not _receiver_id.int or len(_AGENT_SOVEREIGN_IDS) != 1 or str(_receiver_id) in _AGENT_SOVEREIGN_IDS:
        raise ValueError("agent_api_receiver_configuration_invalid")
    _AGENT_SOVEREIGN_IDS = _AGENT_SOVEREIGN_IDS | {str(_receiver_id)}
    _AGENT_CAPTURE_IDS = frozenset({str(_receiver_id)})
    _AGENT_REQUIRED_CLIENT_COUNT = 2

AGENT_API_AUTH_SETTINGS = EntraAgentAuthSettings(
    enabled=config("AGENT_API_ENABLED", default=False, cast=bool),
    tenant_id=config("ENTRA_TENANT_ID", default="", cast=str),
    audience=config("AGENT_API_AUDIENCE", default="", cast=str),
    sovereign_client_ids=_AGENT_SOVEREIGN_IDS,
    required_client_count=_AGENT_REQUIRED_CLIENT_COUNT,
)

# Context resolution and conversation receipts are shared protocol operations.
# Feature endpoints use separate callers even when both hold AgentApi.Access.
SOVEREIGN_TOOL_AUTH_SETTINGS = replace(
    AGENT_API_AUTH_SETTINGS,
    sovereign_client_ids=_AGENT_PROVIDER_IDS,
    required_client_count=1,
)
SCRIBE_CAPTURE_AUTH_SETTINGS = replace(
    AGENT_API_AUTH_SETTINGS,
    sovereign_client_ids=_AGENT_CAPTURE_IDS,
    required_client_count=1,
)
