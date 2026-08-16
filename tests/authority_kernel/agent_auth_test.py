from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jwt import MissingRequiredClaimError

from app.configuration.service_auth import (
    AGENT_API_ACCESS_ROLE,
    SCRIBE_TRANSCRIPT_WRITE_ROLE,
    EntraServiceAuthPolicy,
    EntraServiceAuthSettings,
)
from app.routes.dependencies.entra_service import (
    EntraServiceAuthenticator,
    EntraTokenDecoder,
)

TENANT_ID = str(uuid4())
AUDIENCE = str(uuid4())
SOVEREIGN_CLIENT_ID = str(uuid4())
SCRIBE_CLIENT_ID = str(uuid4())
SCRIBE_RECONCILER_CLIENT_ID = str(uuid4())


def _policy() -> EntraServiceAuthPolicy:
    return EntraServiceAuthPolicy(
        settings=EntraServiceAuthSettings(
            enabled=True,
            tenant_id=TENANT_ID,
            audience=AUDIENCE,
            require_nbf=True,
        ),
        allowed_client_ids=frozenset({SOVEREIGN_CLIENT_ID}),
        required_role=AGENT_API_ACCESS_ROLE,
    )


def _credentials() -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials="synthetic")


def _scribe_policy() -> EntraServiceAuthPolicy:
    return EntraServiceAuthPolicy(
        settings=EntraServiceAuthSettings(
            enabled=True,
            tenant_id=TENANT_ID,
            audience=AUDIENCE,
        ),
        allowed_client_ids=frozenset({SCRIBE_CLIENT_ID, SCRIBE_RECONCILER_CLIENT_ID}),
        required_role=SCRIBE_TRANSCRIPT_WRITE_ROLE,
        required_client_count=2,
        log_namespace="scribe_handoff",
    )


def test_agent_auth_requires_allowlisted_sovereign_and_agent_role() -> None:
    claims = {
        "tid": TENANT_ID,
        "azp": SOVEREIGN_CLIENT_ID,
        "idtyp": "app",
        "roles": [AGENT_API_ACCESS_ROLE],
    }
    authenticator = EntraServiceAuthenticator(_policy(), decoder=lambda token: claims)

    principal = authenticator(_credentials())

    assert principal.client_id == SOVEREIGN_CLIENT_ID


@pytest.mark.parametrize("client_id", [SCRIBE_CLIENT_ID, SCRIBE_RECONCILER_CLIENT_ID])
def test_scribe_auth_preserves_both_physical_callers(client_id: str) -> None:
    claims = {
        "tid": TENANT_ID,
        "azp": client_id,
        "idtyp": "app",
        "roles": [SCRIBE_TRANSCRIPT_WRITE_ROLE],
    }
    authenticator = EntraServiceAuthenticator(
        _scribe_policy(),
        decoder=lambda token: claims,
    )

    principal = authenticator(_credentials())

    assert principal.client_id == client_id


def test_scribe_auth_fails_closed_when_two_caller_allowlist_collapses() -> None:
    policy = EntraServiceAuthPolicy(
        settings=_scribe_policy().settings,
        allowed_client_ids=frozenset({SCRIBE_CLIENT_ID}),
        required_role=SCRIBE_TRANSCRIPT_WRITE_ROLE,
        required_client_count=2,
        log_namespace="scribe_handoff",
    )
    authenticator = EntraServiceAuthenticator(
        policy,
        decoder=lambda token: {},
    )

    with pytest.raises(HTTPException) as error:
        authenticator(_credentials())

    assert error.value.status_code == 503


@pytest.mark.parametrize(
    "claim_overrides",
    [
        {"azp": str(uuid4())},
        {"roles": ["Scribe.Transcript.Write"]},
        {"idtyp": "user"},
    ],
)
def test_agent_auth_rejects_other_callers_roles_and_user_tokens(
    claim_overrides: dict[str, object],
) -> None:
    claims = {
        "tid": TENANT_ID,
        "azp": SOVEREIGN_CLIENT_ID,
        "idtyp": "app",
        "roles": [AGENT_API_ACCESS_ROLE],
        **claim_overrides,
    }
    authenticator = EntraServiceAuthenticator(_policy(), decoder=lambda token: claims)

    with pytest.raises(HTTPException) as error:
        authenticator(_credentials())

    assert error.value.status_code == 403


class _StaticJwksClient:
    def __init__(self, key: object) -> None:
        self.key = key

    def get_signing_key_from_jwt(self, token: str) -> "_StaticJwksClient":
        assert token
        return self


def test_agent_decoder_requires_not_before_claim() -> None:
    private_key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
    settings = _policy().settings
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "aud": settings.audience,
            "iss": settings.issuer,
            "sub": "sovereign-service-principal",
            "tid": TENANT_ID,
            "azp": SOVEREIGN_CLIENT_ID,
            "idtyp": "app",
            "roles": [AGENT_API_ACCESS_ROLE],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    decoder = EntraTokenDecoder(settings)
    decoder.jwks_client = _StaticJwksClient(private_key.public_key())  # type: ignore[assignment]

    with pytest.raises(MissingRequiredClaimError, match="nbf"):
        decoder(token)


def test_shared_decoder_preserves_scribe_tokens_without_not_before() -> None:
    private_key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
    settings = EntraServiceAuthSettings(
        enabled=True,
        tenant_id=TENANT_ID,
        audience=AUDIENCE,
    )
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "aud": settings.audience,
            "iss": settings.issuer,
            "sub": "scribe-service-principal",
            "tid": TENANT_ID,
            "azp": SOVEREIGN_CLIENT_ID,
            "idtyp": "app",
            "roles": ["Scribe.Transcript.Write"],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    decoder = EntraTokenDecoder(settings)
    decoder.jwks_client = _StaticJwksClient(private_key.public_key())  # type: ignore[assignment]

    assert decoder(token)["sub"] == "scribe-service-principal"
