"""Real token/JWKS verification; only synthetic JWKS HTTP I/O is replaced."""

import json
import time
from http.client import IncompleteRead
from io import BytesIO
from types import SimpleNamespace
from typing import Annotated
from uuid import uuid4

import jwt
import pytest
from clerk_backend_api.security import VerifyTokenOptions, verify_token
from clerk_backend_api.security.types import TokenVerificationError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from app.configuration.clerk import verified_provider_session
from app.configuration.service_auth import AGENT_API_ACCESS_ROLE, EntraAgentAuthSettings
from app.routes.dependencies.entra_service import (
    EntraServiceAuthenticator,
    EntraServicePrincipal,
    EntraTokenDecoder,
)


def _jwk(key, kid="synthetic-key"):
    return {
        **json.loads(RSAAlgorithm.to_jwk(key.public_key())),
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
    }


@pytest.fixture
def boundary(monkeypatch):
    settings = EntraAgentAuthSettings(
        enabled=True,
        tenant_id=str(uuid4()),
        audience=str(uuid4()),
        sovereign_client_ids=frozenset({str(uuid4())}),
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    keys = [_jwk(key)]
    fetches = []

    def open_jwks(request, *, timeout, context=None):
        assert request.full_url == settings.jwks_url and timeout > 0
        fetches.append(request.full_url)
        return BytesIO(json.dumps({"keys": keys}).encode())

    monkeypatch.setattr(
        "jwt.jwks_client.urllib.request.build_opener",
        lambda *handlers: SimpleNamespace(open=open_jwks),
    )
    decoder = EntraTokenDecoder(settings)
    authenticator = EntraServiceAuthenticator(settings, decoder=decoder)
    app = FastAPI()

    @app.get("/synthetic-auth-check")
    def authenticated(
        principal: Annotated[EntraServicePrincipal, Depends(authenticator)],
    ):
        return {"client_id": principal.client_id}

    with TestClient(app) as client:
        yield SimpleNamespace(
            settings=settings,
            key=key,
            keys=keys,
            fetches=fetches,
            decoder=decoder,
            client=client,
        )


def _claims(boundary):
    now = int(time.time())
    return {
        "iss": boundary.settings.issuer,
        "aud": boundary.settings.audience,
        "sub": "synthetic-service",
        "tid": boundary.settings.tenant_id,
        "azp": next(iter(boundary.settings.allowed_client_ids)),
        "idtyp": "app",
        "roles": [AGENT_API_ACCESS_ROLE],
        "iat": now,
        "nbf": now,
        "exp": now + 300,
    }


def _request(boundary, claims=None, *, key=None, kid="synthetic-key"):
    token = jwt.encode(
        _claims(boundary) if claims is None else claims,
        key or boundary.key,
        algorithm="RS256",
        headers={"kid": kid},
    )
    return boundary.client.get(
        "/synthetic-auth-check", headers={"Authorization": f"Bearer {token}"}
    )


def _unauthorized(response):
    assert response.status_code == 401
    assert response.json() == {"detail": "Service authentication failed."}
    assert response.headers["www-authenticate"] == "Bearer"


def test_signed_service_request_and_cached_key(boundary):
    for _ in range(2):
        response = _request(boundary)
        assert response.status_code == 200
        assert response.json() == {
            "client_id": next(iter(boundary.settings.allowed_client_ids))
        }
    assert len(boundary.fetches) == 1


@pytest.mark.parametrize("claim", ["exp", "iat", "nbf"])
@pytest.mark.parametrize("value", [None, [], {}])
def test_malformed_lifetime_claims_fail_closed(boundary, claim, value):
    _unauthorized(_request(boundary, {**_claims(boundary), claim: value}))


@pytest.mark.parametrize("claim", ["iss", "aud", "sub", "tid", "exp", "iat", "nbf"])
def test_missing_required_claims_fail_closed(boundary, claim):
    claims = _claims(boundary)
    del claims[claim]
    _unauthorized(_request(boundary, claims))


@pytest.mark.parametrize(
    "override,expected",
    [
        ({"iss": "https://wrong.invalid"}, 401),
        ({"aud": "wrong"}, 401),
        ({"tid": str(uuid4())}, 401),
        ({"exp": 1}, 401),
        ({"roles": ["wrong"]}, 403),
        ({"idtyp": "user"}, 403),
        ({"azp": str(uuid4())}, 403),
    ],
)
def test_signed_but_unauthorized_claims_are_rejected(boundary, override, expected):
    response = _request(boundary, {**_claims(boundary), **override})
    assert response.status_code == expected
    if expected == 401:
        _unauthorized(response)


def test_wrong_signature_and_algorithm_fail_closed(boundary):
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _unauthorized(_request(boundary, key=wrong_key))
    token = jwt.encode(
        _claims(boundary),
        "synthetic-secret-only-not-a-real-key",
        algorithm="HS256",
        headers={"kid": "synthetic-key"},
    )
    _unauthorized(
        boundary.client.get(
            "/synthetic-auth-check", headers={"Authorization": f"Bearer {token}"}
        )
    )


def test_deep_payload_is_sanitized_instead_of_uncaught_recursion(boundary):
    payload = b'{"nested":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}"
    token = jwt.PyJWS().encode(
        payload, boundary.key, algorithm="RS256", headers={"kid": "synthetic-key"}
    )
    _unauthorized(
        boundary.client.get(
            "/synthetic-auth-check", headers={"Authorization": f"Bearer {token}"}
        )
    )


@pytest.mark.parametrize("suffix,expected", [("==", 200), ("!!!!", 401)])
def test_signature_padding_compatibility_does_not_accept_junk(
    boundary, suffix, expected
):
    token = jwt.encode(
        _claims(boundary),
        boundary.key,
        algorithm="RS256",
        headers={"kid": "synthetic-key"},
    )
    response = boundary.client.get(
        "/synthetic-auth-check", headers={"Authorization": f"Bearer {token}{suffix}"}
    )
    assert response.status_code == expected
    if expected == 401:
        _unauthorized(response)


def test_malformed_jwks_members_do_not_break_valid_signer(boundary):
    boundary.keys[:0] = [None, {"kty": "RSA", "n": [], "e": "AQAB"}]
    assert _request(boundary).status_code == 200


def test_truncated_jwks_response_is_sanitized(boundary, monkeypatch):
    def truncated(*args, **kwargs):
        raise IncompleteRead(b"synthetic partial response")

    monkeypatch.setattr(
        "jwt.jwks_client.urllib.request.build_opener",
        lambda *handlers: SimpleNamespace(open=truncated),
    )
    _unauthorized(_request(boundary))


def test_unknown_key_refresh_is_bounded_and_rotation_recovers(boundary):
    assert _request(boundary).status_code == 200
    rotated = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    boundary.keys.append(_jwk(rotated, "rotated-key"))
    for _ in range(3):
        _unauthorized(_request(boundary, key=rotated, kid="rotated-key"))
    assert len(boundary.fetches) == 1
    client = boundary.decoder.jwks_client
    client._last_successful_fetch -= client.cooldown_duration + 1
    assert _request(boundary, key=rotated, kid="rotated-key").status_code == 200
    assert _request(boundary).status_code == 200
    assert len(boundary.fetches) == 2


@pytest.mark.parametrize(
    "mutation", [None, "expired", "party", "signature", "malformed-exp"]
)
def test_clerk_sdk_verification_preserves_session_boundary(boundary, mutation):
    public_pem = (
        boundary.key.public_key()
        .public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        .decode()
    )
    options = VerifyTokenOptions(
        jwt_key=public_pem,
        authorized_parties=["https://synthetic.invalid"],
        clock_skew_in_ms=0,
    )
    claims = {
        **_claims(boundary),
        "azp": "https://synthetic.invalid",
        "sub": "user_synthetic",
        "sid": "sess_synthetic",
    }
    del claims["aud"]
    if mutation == "expired":
        claims["exp"] = 1
    elif mutation == "party":
        claims["azp"] = "https://wrong.invalid"
    elif mutation == "malformed-exp":
        claims["exp"] = []
    key = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        if mutation == "signature"
        else boundary.key
    )
    token = jwt.encode(claims, key, algorithm="RS256")
    if mutation is None:
        payload = verify_token(token, options)
        session = verified_provider_session(
            SimpleNamespace(payload=payload, is_signed_in=True),
            expected_issuer=boundary.settings.issuer,
        )
        assert session.subject == "user_synthetic"
        assert session.issuer == boundary.settings.issuer
        assert "sess_synthetic" not in session.session_sha256
        with pytest.raises(ValueError, match="provider_session_invalid"):
            verified_provider_session(
                SimpleNamespace(payload=payload, is_signed_in=True),
                expected_issuer="https://wrong.invalid",
            )
    else:
        with pytest.raises(TokenVerificationError):
            verify_token(token, options)
