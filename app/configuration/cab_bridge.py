"""Dedicated dormant CAB channel; never inherit another workload's caller."""

from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

from decouple import config

from app.configuration.service_auth import (
    EntraServiceAuthPolicy,
    EntraServiceAuthSettings,
)


@dataclass(frozen=True)
class CabBridgeSettings:
    enabled: bool = False
    origin: str = ""
    audience: str = ""
    managed_identity_client_id: str = ""
    async_enabled: bool = False

    def is_complete(self):
        try:
            p = urlsplit(self.origin)
            return (
                p.scheme == "https"
                and bool(p.hostname)
                and p.port in (None, 443)
                and not p.username
                and not p.password
                and not p.query
                and not p.fragment
                and p.path == ""
                and not any(c.isspace() for c in self.origin)
                and str(UUID(self.audience)) == self.audience
                and str(UUID(self.managed_identity_client_id))
                == self.managed_identity_client_id
            )
        except (ValueError, TypeError):
            return False


CAB_AUTHORIZE_POLICY = EntraServiceAuthPolicy(
    settings=EntraServiceAuthSettings(
        enabled=config("CORE_CAB_AUTHORIZE_ENABLED", default=False, cast=bool),
        tenant_id=config("CORE_CAB_AUTHORIZE_TENANT_ID", default="", cast=str),
        audience=config("CORE_CAB_AUTHORIZE_AUDIENCE", default="", cast=str),
        require_nbf=True,
    ),
    allowed_client_ids=frozenset(
        filter(None, [config("CORE_CAB_AUTHORIZE_CALLER_ID", default="", cast=str)])
    ),
    required_role="Core.CabOperation.Authorize",
    log_namespace="cab_authorizer",
)
CAB_BRIDGE_SETTINGS = CabBridgeSettings(
    enabled=config("CORE_CAB_BRIDGE_ENABLED", default=False, cast=bool),
    async_enabled=config("CORE_CAB_ASYNC_ENABLED", default=False, cast=bool),
    origin=config("CORE_CAB_ORIGIN", default="", cast=str),
    audience=config("CORE_CAB_AUDIENCE", default="", cast=str),
    managed_identity_client_id=config(
        "CORE_CAB_MANAGED_IDENTITY_CLIENT_ID", default="", cast=str
    ),
)
