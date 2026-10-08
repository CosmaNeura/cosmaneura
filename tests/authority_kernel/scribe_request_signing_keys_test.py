"""Real JWT verification with synthetic public-key HTTP responses only."""

import contextvars
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException, Request
from jwt.algorithms import RSAAlgorithm

from app.configuration.service_auth import (
    EntraAgentAuthSettings,
    EntraServiceAuthPolicy,
    EntraServiceAuthSettings,
)
from app.routes import scribe_native_capture as route
from app.routes.dependencies import entra_service as entra


@pytest.fixture
def workload(monkeypatch):
    policy = EntraAgentAuthSettings(
        enabled=True,
        tenant_id=str(uuid4()),
        audience=str(uuid4()),
        sovereign_client_ids=frozenset({str(uuid4())}),
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(RSAAlgorithm.to_jwk(key.public_key())) | {
        "kid": "synthetic-key",
        "use": "sig",
        "alg": "RS256",
    }
    keys, fetches = [public], []

    def open_keys(request, **kwargs):
        fetches.append(request.full_url)
        return BytesIO(json.dumps({"keys": keys}).encode())

    monkeypatch.setattr(
        "jwt.jwks_client.urllib.request.build_opener",
        lambda *handlers: SimpleNamespace(open=open_keys),
    )
    monkeypatch.setattr(
        route, "authenticate_agent_api_request", entra.EntraServiceAuthenticator(policy)
    )
    capture_policy = EntraServiceAuthPolicy(
        settings=EntraServiceAuthSettings(
            enabled=True,
            tenant_id=policy.tenant_id,
            audience=policy.audience,
            require_nbf=True,
        ),
        allowed_client_ids=policy.allowed_client_ids,
        required_role="Scribe.Capture",
    )
    monkeypatch.setattr(
        route,
        "authenticate_scribe_capture_request",
        entra.EntraServiceAuthenticator(capture_policy),
    )

    def token(**changes):
        now = int(time.time())
        return jwt.encode(
            {
                "iss": policy.issuer,
                "aud": policy.audience,
                "tid": policy.tenant_id,
                "azp": next(iter(policy.allowed_client_ids)),
                "sub": "synthetic-service",
                "idtyp": "app",
                "roles": [policy.required_role, capture_policy.required_role],
                "iat": now,
                "nbf": now,
                "exp": now + 300,
                **changes,
            },
            key,
            algorithm="RS256",
            headers={"kid": "synthetic-key"},
        )

    def check(value):
        return route.authenticate_native_workload(
            Request(
                {
                    "type": "http",
                    "headers": [
                        (b"authorization", b"Bearer synthetic-human-token"),
                        (
                            b"x-cosmaneura-workload-authorization",
                            ("Bearer " + value).encode(),
                        ),
                    ],
                }
            )
        )

    return SimpleNamespace(
        policy=policy, key=key, keys=keys, fetches=fetches, token=token, check=check
    )


def test_native_policies_fetch_once_but_next_request_fetches_fresh(workload):
    for count in (1, 2):
        assert (
            workload.check(workload.token()).client_id
            in workload.policy.allowed_client_ids
        )
        assert len(workload.fetches) == count
        assert entra._request_keys.get() is None
    workload.keys.clear()
    with pytest.raises(HTTPException) as error:
        workload.check(workload.token())
    assert error.value.status_code == 401
    assert len(workload.fetches) == 3
    assert entra._request_keys.get() is None


@pytest.mark.parametrize(
    "changes,status",
    [
        ({"roles": ["AgentApi.Access"]}, 403),
        ({"roles": ["Scribe.Capture"]}, 403),
        ({"aud": "wrong"}, 401),
        ({"iss": "https://wrong.invalid"}, 401),
        ({"azp": str(uuid4())}, 403),
        ({"idtyp": "user"}, 403),
        ({"exp": 1}, 401),
        ({"nbf": int(time.time()) + 3600}, 401),
    ],
)
def test_every_policy_and_lifetime_check_still_required(workload, changes, status):
    with pytest.raises(HTTPException) as error:
        workload.check(workload.token(**changes))
    assert error.value.status_code == status
    assert len(workload.fetches) == 1
    assert entra._request_keys.get() is None


def test_second_policy_reverifies_signature_and_its_own_audience(workload):
    with entra.request_signing_keys():
        assert entra.EntraTokenDecoder(workload.policy)(workload.token())
        other = replace(workload.policy, audience=str(uuid4()))
        with pytest.raises(jwt.InvalidAudienceError):
            entra.EntraTokenDecoder(other)(workload.token())
        wrong = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        claims = jwt.decode(workload.token(), options={"verify_signature": False})
        token = jwt.encode(
            claims, wrong, algorithm="RS256", headers={"kid": "synthetic-key"}
        )
        with pytest.raises(jwt.InvalidSignatureError):
            entra.EntraTokenDecoder(workload.policy)(token)
    assert len(workload.fetches) == 1


def test_rotated_key_is_accepted_on_the_next_native_request(workload):
    assert workload.check(workload.token())
    rotated = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    workload.keys[:] = [
        json.loads(RSAAlgorithm.to_jwk(rotated.public_key()))
        | {"kid": "rotated-key", "use": "sig", "alg": "RS256"}
    ]
    claims = jwt.decode(workload.token(), options={"verify_signature": False})
    token = jwt.encode(
        claims, rotated, algorithm="RS256", headers={"kid": "rotated-key"}
    )
    assert workload.check(token)
    assert len(workload.fetches) == 2


def test_concurrent_native_requests_own_separate_key_clients(workload):
    token = workload.token()
    with ThreadPoolExecutor(max_workers=2) as workers:
        assert all(workers.map(workload.check, [token, token]))
    assert len(workload.fetches) == 2
    assert entra._request_keys.get() is None


def test_tenant_namespaces_and_nested_contexts_do_not_share_clients(workload):
    with entra.request_signing_keys():
        first = entra.EntraTokenDecoder(workload.policy)
        assert entra.EntraTokenDecoder(workload.policy).jwks_client is first.jwks_client
        other = replace(workload.policy, tenant_id=str(uuid4()))
        assert entra.EntraTokenDecoder(other).jwks_client is not first.jwks_client
        with entra.request_signing_keys():
            assert (
                entra.EntraTokenDecoder(workload.policy).jwks_client
                is not first.jwks_client
            )
        assert entra.EntraTokenDecoder(workload.policy).jwks_client is first.jwks_client
        isolated = contextvars.Context().run(entra.EntraTokenDecoder, workload.policy)
        assert isolated.jwks_client is not first.jwks_client
    assert entra._request_keys.get() is None
