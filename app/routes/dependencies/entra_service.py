from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Protocol
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient, PyJWTError

from app import log
from app.configuration.service_auth import (
    AGENT_API_AUTH_SETTINGS,
    SCRIBE_CAPTURE_AUTH_SETTINGS,
    SERVICE_AUTH_SETTINGS,
    SOVEREIGN_TOOL_AUTH_SETTINGS,
)


@dataclass(frozen=True)
class EntraServicePrincipal:
    client_id: str


TokenDecoder = Callable[[str], dict[str, Any]]


class EntraAuthPolicy(Protocol):
    @property
    def enabled(self) -> bool: ...

    @property
    def tenant_id(self) -> str: ...

    @property
    def audience(self) -> str: ...

    @property
    def issuer(self) -> str: ...

    @property
    def jwks_url(self) -> str: ...

    @property
    def allowed_client_ids(self) -> frozenset[str]: ...

    @property
    def required_role(self) -> str: ...

    @property
    def log_namespace(self) -> str: ...

    @property
    def require_nbf(self) -> bool: ...

    def is_complete(self) -> bool: ...


class EntraTokenDecoder:
    """Verify Entra v2 access tokens against the tenant's published signing keys."""

    def __init__(self, settings: EntraAuthPolicy):
        self.settings = settings
        self.jwks_client = PyJWKClient(settings.jwks_url, cache_keys=True)

    def __call__(self, token: str) -> dict[str, Any]:
        signing_key = self.jwks_client.get_signing_key_from_jwt(token)
        required_claims = ["aud", "exp", "iat", "iss", "sub", "tid"]
        if self.settings.require_nbf:
            required_claims.append("nbf")
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=self.settings.audience,
            issuer=self.settings.issuer,
            options={"require": required_claims},
        )


def _authentication_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Service authentication failed.",
        headers={"WWW-Authenticate": "Bearer"},
    )


class EntraServiceAuthenticator:
    """Authorize an explicitly allowlisted app-only caller with one app role."""

    def __init__(
        self,
        settings: EntraAuthPolicy,
        decoder: TokenDecoder | None = None,
    ):
        self.settings = settings
        self.decoder = decoder

    def __call__(
        self,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None,
            Depends(HTTPBearer(auto_error=False)),
        ],
    ) -> EntraServicePrincipal:
        if not self.settings.enabled or not self.settings.is_complete():
            log.warning(
                "service authentication rejected",
                action=f"{self.settings.log_namespace}.authentication_rejected",
                error_class="service_auth_not_configured",
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Service authentication is not configured.",
            )

        if credentials is None or credentials.scheme.lower() != "bearer":
            log.warning(
                "service authentication rejected",
                action=f"{self.settings.log_namespace}.authentication_rejected",
                error_class="missing_bearer_token",
            )
            raise _authentication_error()

        decoder = self.decoder or EntraTokenDecoder(self.settings)
        try:
            claims = decoder(credentials.credentials)
        except (PyJWTError, ValueError, TypeError):
            log.warning(
                "service authentication rejected",
                action=f"{self.settings.log_namespace}.authentication_rejected",
                error_class="invalid_access_token",
            )
            raise _authentication_error() from None

        raw_client_id = claims.get("azp")
        try:
            client_id = (
                str(UUID(raw_client_id)) if isinstance(raw_client_id, str) else None
            )
        except ValueError:
            client_id = None

        if claims.get("tid") != self.settings.tenant_id:
            log.warning(
                "service authentication rejected",
                action=f"{self.settings.log_namespace}.authentication_rejected",
                error_class="tenant_mismatch",
                actual_caller_client_id=client_id,
            )
            raise _authentication_error()

        roles = claims.get("roles")
        if (
            claims.get("idtyp") != "app"
            or client_id not in self.settings.allowed_client_ids
            or not isinstance(roles, list)
            or self.settings.required_role not in roles
        ):
            log.warning(
                "service authorization rejected",
                action=f"{self.settings.log_namespace}.authorization_rejected",
                error_class="app_claims_not_authorized",
                actual_caller_client_id=client_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Service caller is not authorized.",
            )
        assert client_id is not None
        return EntraServicePrincipal(client_id=client_id)


authenticate_scribe_service_request = EntraServiceAuthenticator(SERVICE_AUTH_SETTINGS)
authenticate_agent_api_request = EntraServiceAuthenticator(AGENT_API_AUTH_SETTINGS)
authenticate_sovereign_tool_request = EntraServiceAuthenticator(
    SOVEREIGN_TOOL_AUTH_SETTINGS
)
authenticate_scribe_capture_request = EntraServiceAuthenticator(
    SCRIBE_CAPTURE_AUTH_SETTINGS
)
