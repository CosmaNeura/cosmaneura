"""Actual signed JWTs and invented keys; no live identity service or test bearer."""

import json
import runpy
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.configuration import authority, clerk, service_auth
from app.configuration.service_auth import (
    CompanionAuthenticationRejected,
    EntraAgentAuthSettings,
    VerifiedCredentialWindow,
    verify_visit_prep_workload,
)


@pytest.fixture
def signed(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="invented-key", use="sig", alg="RS256")
    policy = EntraAgentAuthSettings(
        enabled=True,
        tenant_id=str(uuid4()),
        audience=str(uuid4()),
        sovereign_client_ids=frozenset({str(uuid4())}),
    )
    now = int(datetime.now(UTC).timestamp())
    workload = {
        "iss": policy.issuer,
        "aud": policy.audience,
        "tid": policy.tenant_id,
        "sub": "invented-workload",
        "azp": next(iter(policy.allowed_client_ids)),
        "idtyp": "app",
        "roles": ["AgentApi.Access"],
        "nbf": now - 10,
        "iat": now - 10,
        "exp": now + 300,
    }
    physician = {
        "iss": "https://visit-prep.clerk.invalid",
        "sub": "user_inventedPhysician",
        "sid": "sess_inventedSession",
        "nbf": now - 10,
        "iat": now - 10,
        "exp": now + 300,
    }
    fetches = []

    def fetch(url, timeout):
        fetches.append((url, timeout))
        return {"keys": [jwk]}

    monkeypatch.setattr(service_auth, "companion_public_jwks", fetch)
    monkeypatch.setattr(clerk, "companion_public_jwks", fetch)
    monkeypatch.setattr(
        authority,
        "AUTHORITY_KERNEL_SETTINGS",
        replace(
            authority.AUTHORITY_KERNEL_SETTINGS,
            enabled=True,
            clerk_issuer=physician["iss"],
        ),
    )

    def encode(claims, *, signing_key=key, headers=None):
        return jwt.encode(
            claims,
            signing_key,
            algorithm="RS256",
            headers=headers or {"kid": "invented-key"},
        )

    return SimpleNamespace(
        policy=policy,
        workload=workload,
        physician=physician,
        now=now,
        encode=encode,
        fetches=fetches,
        jwk=jwk,
    )


def test_both_principals_retain_only_verified_identity_and_expiry(signed):
    workload_token = signed.encode(signed.workload)
    physician_token = signed.encode(signed.physician)
    workload = verify_visit_prep_workload(
        workload_token, settings=signed.policy, remaining_seconds=9
    )
    physician = clerk.verify_visit_prep_physician(
        physician_token, remaining_seconds=2.25
    )
    assert workload.client_id == signed.workload["azp"]
    assert physician.issuer == signed.physician["iss"]
    assert physician.subject == signed.physician["sub"]
    assert physician.session_id == signed.physician["sid"]
    for principal in (workload, physician):
        assert principal.window.valid_at(datetime.fromtimestamp(signed.now + 299, UTC))
        assert not principal.window.valid_at(
            datetime.fromtimestamp(signed.now + 300, UTC)
        )
        assert not principal.window.valid_at(
            datetime.fromtimestamp(signed.now - 11, UTC)
        )
        assert not principal.window.valid_at(datetime.now())
        assert workload_token not in repr(principal) and physician_token not in repr(
            principal
        )
        assert not hasattr(principal, "claims") and not hasattr(principal, "token")
    assert signed.fetches == [
        (signed.policy.jwks_url, 4.0),
        (signed.physician["iss"] + "/.well-known/jwks.json", 2.25),
    ]


@pytest.mark.parametrize("identity", ["workload", "physician"])
@pytest.mark.parametrize("field", ["nbf", "iat", "exp", "iss", "sub"])
def test_each_identity_requires_signed_claims(signed, identity, field):
    claims = dict(getattr(signed, identity))
    del claims[field]
    token = signed.encode(claims)
    with pytest.raises(CompanionAuthenticationRejected) as failure:
        if identity == "workload":
            verify_visit_prep_workload(
                token, settings=signed.policy, remaining_seconds=5
            )
        else:
            clerk.verify_visit_prep_physician(token, remaining_seconds=5)
    assert failure.value.status_code == 401
    assert token not in str(failure.value)


@pytest.mark.parametrize("identity", ["workload", "physician"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("nbf", "0"),
        ("iat", 1.5),
        ("exp", True),
        ("exp", "253402300799"),
        ("iss", "https://wrong-issuer.invalid"),
    ],
)
def test_noncanonical_bounds_and_wrong_issuer_fail(signed, identity, field, value):
    claims = {**getattr(signed, identity), field: value}
    token = signed.encode(claims)
    with pytest.raises(CompanionAuthenticationRejected) as failure:
        if identity == "workload":
            verify_visit_prep_workload(
                token, settings=signed.policy, remaining_seconds=5
            )
        else:
            clerk.verify_visit_prep_physician(token, remaining_seconds=5)
    assert failure.value.status_code == 401


@pytest.mark.parametrize(
    "overrides,status",
    [
        ({"aud": str(uuid4())}, 401),
        ({"aud": ["second-audience"]}, 401),
        ({"tid": str(uuid4())}, 401),
        ({"azp": str(uuid4())}, 403),
        ({"azp": "not-a-uuid"}, 403),
        ({"idtyp": "user"}, 403),
        ({"scp": "patient.read"}, 403),
        ({"roles": ["AgentApiAccess"]}, 403),
        ({"roles": "AgentApi.Access"}, 403),
        ({"roles": ["AgentApi.Access", 1]}, 403),
        ({"roles": ["Scribe.Transcript.Write"]}, 403),
    ],
)
def test_wrong_workload_scope_never_authorizes(signed, overrides, status):
    with pytest.raises(CompanionAuthenticationRejected) as failure:
        verify_visit_prep_workload(
            signed.encode({**signed.workload, **overrides}),
            settings=signed.policy,
            remaining_seconds=5,
        )
    assert failure.value.status_code == status


@pytest.mark.parametrize(
    "overrides,status",
    [
        ({"sub": "not-a-provider"}, 401),
        ({"sid": None}, 401),
        ({"sid": "sess_invented\n"}, 401),
        ({"act": {"sub": "user_support"}}, 403),
        ({"act": None}, 403),
        ({"sts": "pending"}, 403),
    ],
)
def test_physician_session_cannot_be_impersonated_or_pending(signed, overrides, status):
    with pytest.raises(CompanionAuthenticationRejected) as failure:
        clerk.verify_visit_prep_physician(
            signed.encode({**signed.physician, **overrides}),
            remaining_seconds=5,
        )
    assert failure.value.status_code == status


@pytest.mark.parametrize("identity", ["workload", "physician"])
@pytest.mark.parametrize("bound", ["expired", "future"])
def test_time_invalid_credentials_fail_before_any_domain_read(signed, identity, bound):
    claims = dict(getattr(signed, identity))
    claims["exp" if bound == "expired" else "nbf"] = (
        signed.now - 1 if bound == "expired" else signed.now + 100
    )
    with pytest.raises(CompanionAuthenticationRejected) as failure:
        if identity == "workload":
            verify_visit_prep_workload(
                signed.encode(claims), settings=signed.policy, remaining_seconds=5
            )
        else:
            clerk.verify_visit_prep_physician(
                signed.encode(claims), remaining_seconds=5
            )
    assert failure.value.status_code == 401


@pytest.mark.parametrize("identity", ["workload", "physician"])
def test_signature_from_another_key_is_not_authenticated(signed, identity):
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = signed.encode(getattr(signed, identity), signing_key=wrong_key)
    with pytest.raises(CompanionAuthenticationRejected) as failure:
        if identity == "workload":
            verify_visit_prep_workload(
                token, settings=signed.policy, remaining_seconds=5
            )
        else:
            clerk.verify_visit_prep_physician(token, remaining_seconds=5)
    assert failure.value.status_code == 401


@pytest.mark.parametrize("identity", ["workload", "physician"])
@pytest.mark.parametrize(
    "token", ["test:user_test_invented", "bad", "a" * 8193, "a b", "a\tb", "é"]
)
def test_malformed_and_test_bearers_do_not_fetch_keys(signed, identity, token):
    with pytest.raises(CompanionAuthenticationRejected) as failure:
        if identity == "workload":
            verify_visit_prep_workload(
                token, settings=signed.policy, remaining_seconds=5
            )
        else:
            clerk.verify_visit_prep_physician(token, remaining_seconds=5)
    assert failure.value.status_code == 401
    assert signed.fetches == []


@pytest.mark.parametrize("remaining", [0, -1, float("inf"), float("nan"), True])
def test_exhausted_or_invalid_request_budget_never_fetches(signed, remaining):
    with pytest.raises(CompanionAuthenticationRejected) as failure:
        verify_visit_prep_workload(
            signed.encode(signed.workload),
            settings=signed.policy,
            remaining_seconds=remaining,
        )
    assert failure.value.status_code == 504
    assert signed.fetches == []


def test_key_fetch_uncertainty_is_static_and_not_an_authentication_success(
    signed, monkeypatch
):
    def unavailable(*args, **kwargs):
        raise RuntimeError("private SDK sentinel")

    monkeypatch.setattr(service_auth, "companion_public_jwks", unavailable)
    monkeypatch.setattr(clerk, "companion_public_jwks", unavailable)
    for verify in (
        lambda: verify_visit_prep_workload(
            signed.encode(signed.workload), settings=signed.policy, remaining_seconds=5
        ),
        lambda: clerk.verify_visit_prep_physician(
            signed.encode(signed.physician), remaining_seconds=5
        ),
    ):
        with pytest.raises(CompanionAuthenticationRejected) as failure:
            verify()
        assert failure.value.status_code == 503
        assert "sentinel" not in str(failure.value)


def test_disabled_or_missing_receiver_identity_fails_without_fetch(signed):
    for policy in (
        replace(signed.policy, enabled=False),
        replace(signed.policy, sovereign_client_ids=frozenset()),
        replace(
            signed.policy, sovereign_client_ids=frozenset({str(uuid4()), str(uuid4())})
        ),
    ):
        with pytest.raises(CompanionAuthenticationRejected) as failure:
            verify_visit_prep_workload("bad", settings=policy, remaining_seconds=5)
        assert failure.value.status_code == 503
    assert signed.fetches == []


def test_dark_policy_is_separate_from_session_prep_and_generic_tools(monkeypatch):
    companion, sovereign, scribe = (str(uuid4()) for _ in range(3))
    values = {
        "AGENT_API_ENABLED": "true",
        "ENTRA_TENANT_ID": str(uuid4()),
        "AGENT_API_AUDIENCE": str(uuid4()),
        "AGENT_API_SOVEREIGN_CLIENT_IDS": sovereign,
        "AGENT_API_SCRIBE_RECEIVER_CLIENT_ID": scribe,
        "AGENT_API_COMPANION_CLIENT_ID": companion,
        "COMPANION_SESSION_PREP_ENABLED": "true",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("COMPANION_VISIT_PREP_ENABLED", raising=False)
    disabled = runpy.run_path(str(Path(service_auth.__file__)))
    assert not disabled["COMPANION_VISIT_PREP_AUTH_SETTINGS"].enabled
    monkeypatch.setenv("COMPANION_VISIT_PREP_ENABLED", "true")
    enabled = runpy.run_path(str(Path(service_auth.__file__)))
    assert enabled["COMPANION_VISIT_PREP_AUTH_SETTINGS"].enabled
    assert enabled[
        "COMPANION_VISIT_PREP_AUTH_SETTINGS"
    ].allowed_client_ids == frozenset({companion})
    assert enabled["SOVEREIGN_TOOL_AUTH_SETTINGS"].allowed_client_ids == frozenset(
        {sovereign}
    )
    for name in (
        "AGENT_API_AUTH_SETTINGS",
        "SOVEREIGN_TOOL_AUTH_SETTINGS",
        "SCRIBE_CAPTURE_AUTH_SETTINGS",
        "COMPANION_SESSION_PREP_AUTH_SETTINGS",
    ):
        assert asdict(enabled[name]) == asdict(disabled[name])


@pytest.mark.parametrize(
    "body,status,encoding",
    [
        (b'{"keys":[]}', 200, "identity"),
        (b"x" * 65537, 200, "identity"),
        (b'{"keys":[],"keys":[]}', 200, "identity"),
        (b'{"keys":[]}', 302, "identity"),
        (b'{"keys":[]}', 200, "gzip"),
    ],
    ids=["valid", "overflow", "duplicate-key", "redirect", "compressed"],
)
def test_entra_key_transport_has_no_redirect_retry_or_unbounded_body(
    monkeypatch, body, status, encoding
):
    calls = []
    original = httpx.Client

    def transport(request):
        calls.append(request)
        return httpx.Response(
            status,
            stream=httpx.ByteStream(body),
            headers={
                "content-encoding": encoding,
                "location": "https://untrusted.invalid",
            },
        )

    def client(**kwargs):
        assert kwargs == {
            "trust_env": False,
            "follow_redirects": False,
            "timeout": 1.25,
        }
        return original(**kwargs, transport=httpx.MockTransport(transport))

    monkeypatch.setattr(service_auth.httpx, "Client", client)
    if body == b'{"keys":[]}' and status == 200 and encoding == "identity":
        assert service_auth.companion_public_jwks(
            "https://configured.invalid/keys", 1.25
        ) == {"keys": []}
    else:
        with pytest.raises((CompanionAuthenticationRejected, ValueError)):
            service_auth.companion_public_jwks("https://configured.invalid/keys", 1.25)
    assert len(calls) == 1
    assert calls[0].headers["accept-encoding"] == "identity"
    assert "authorization" not in calls[0].headers


def test_window_denies_equal_expiry_and_non_increasing_bounds():
    now = datetime.now(UTC)
    with pytest.raises(CompanionAuthenticationRejected):
        VerifiedCredentialWindow.from_verified_claims({"nbf": 2, "iat": 2, "exp": 2})
    assert not VerifiedCredentialWindow(0, 0, int(now.timestamp())).valid_at(
        now + timedelta(seconds=1)
    )


@pytest.mark.parametrize(
    "kind", ["duplicate", "unknown", "encrypt", "critical", "wrong-alg"]
)
def test_signing_key_selection_never_trusts_token_headers(signed, kind):
    keys = [signed.jwk]
    headers = {"kid": "invented-key"}
    if kind == "duplicate":
        keys *= 2
    elif kind == "unknown":
        headers["kid"] = "not-configured"
    elif kind == "encrypt":
        keys = [{**signed.jwk, "use": "enc"}]
    elif kind == "critical":
        headers["crit"] = ["unhandled"]
    elif kind == "wrong-alg":
        keys = [{**signed.jwk, "alg": "RS512"}]
    with pytest.raises(CompanionAuthenticationRejected) as failure:
        service_auth.companion_signing_key(
            signed.encode(signed.workload, headers=headers), keys
        )
    assert failure.value.status_code == 401


def test_jwt_selected_urls_cannot_change_either_key_authority(signed):
    headers = {
        "kid": "invented-key",
        "jku": "https://attacker.invalid/keys",
        "x5u": "file:///invented",
        "jwk": signed.jwk,
    }
    verify_visit_prep_workload(
        signed.encode(signed.workload, headers=headers),
        settings=signed.policy,
        remaining_seconds=5,
    )
    clerk.verify_visit_prep_physician(
        signed.encode(signed.physician, headers=headers), remaining_seconds=5
    )
    assert [call[0] for call in signed.fetches] == [
        signed.policy.jwks_url,
        signed.physician["iss"] + "/.well-known/jwks.json",
    ]


def test_matching_audience_inside_multiple_audiences_is_not_exact(signed):
    claims = {**signed.workload, "aud": [signed.policy.audience, str(uuid4())]}
    with pytest.raises(CompanionAuthenticationRejected) as failure:
        verify_visit_prep_workload(
            signed.encode(claims), settings=signed.policy, remaining_seconds=5
        )
    assert failure.value.status_code == 401
