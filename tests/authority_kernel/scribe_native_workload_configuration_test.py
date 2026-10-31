"""Scoped native authentication uses offline signed tokens; no external service."""

import runpy
import time
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.configuration import authority, service_auth
from app.configuration import scribe_native as native
from app.routes.dependencies import entra_service

TENANT = "10000000-0000-4000-8000-000000000001"
AUDIENCE = "10000000-0000-4000-8000-000000000002"
RECEIVER = "10000000-0000-4000-8000-000000000003"
FOREIGN = "10000000-0000-4000-8000-000000000004"


def configured(monkeypatch, **overrides: str):
    monkeypatch.setattr(
        native,
        "AUTHORITY_KERNEL_SETTINGS",
        authority.AuthorityKernelSettings(
            False, 900, 300, clerk_issuer="https://clerk.example.invalid"
        ),
    )
    return native.native_workload_settings(
        enabled=True,
        tenant_id=overrides.get("tenant_id", TENANT),
        audience=overrides.get("audience", AUDIENCE),
        client_id=overrides.get("client_id", RECEIVER),
    )


def test_disabled_seam_preserves_every_existing_workload_policy():
    assert not native.SCRIBE_NATIVE_WORKLOAD_AUTH_SETTINGS.enabled
    assert (
        native.SCRIBE_NATIVE_AGENT_AUTH_SETTINGS is service_auth.AGENT_API_AUTH_SETTINGS
    )
    assert (
        native.SCRIBE_NATIVE_CAPTURE_AUTH_SETTINGS
        is service_auth.SCRIBE_CAPTURE_AUTH_SETTINGS
    )
    assert (
        native.native_authority_settings(authority.AUTHORITY_KERNEL_SETTINGS)
        is authority.AUTHORITY_KERNEL_SETTINGS
    )


def test_native_configuration_does_not_enable_global_session_prep(monkeypatch):
    for key, value in {
        "SCRIBE_NATIVE_WORKLOAD_ENABLED": "true",
        "SCRIBE_NATIVE_WORKLOAD_AUDIENCE": AUDIENCE,
        "SCRIBE_NATIVE_WORKLOAD_CLIENT_ID": RECEIVER,
        "ENTRA_TENANT_ID": TENANT,
    }.items():
        monkeypatch.setenv(key, value)
    configured(monkeypatch)
    monkeypatch.setattr(
        authority, "AUTHORITY_KERNEL_SETTINGS", native.AUTHORITY_KERNEL_SETTINGS
    )
    values = runpy.run_path(native.__file__)
    settings = values["SCRIBE_NATIVE_WORKLOAD_AUTH_SETTINGS"]
    assert settings.enabled and settings.is_complete()
    assert settings.allowed_client_ids == frozenset({RECEIVER})
    assert settings.audience == AUDIENCE
    assert values["SCRIBE_NATIVE_AGENT_AUTH_SETTINGS"] is settings
    assert values["SCRIBE_NATIVE_CAPTURE_AUTH_SETTINGS"] is settings
    before = native.AUTHORITY_KERNEL_SETTINGS
    scoped = values["native_authority_settings"](before)
    assert scoped.enabled and scoped.is_complete()
    assert not before.enabled
    assert service_auth.AGENT_API_AUTH_SETTINGS.enabled is False
    assert not authority.AUTHORITY_KERNEL_SETTINGS.enabled


@pytest.mark.parametrize(
    "field,value",
    [
        ("client_id", ""),
        ("audience", ""),
        ("tenant_id", ""),
        ("client_id", "00000000-0000-0000-0000-000000000000"),
        ("audience", "not-a-uuid"),
        ("client_id", "10000000-0000-4000-8000-00000000000A"),
    ],
)
def test_enabled_seam_refuses_missing_or_noncanonical_binding(
    monkeypatch, field, value
):
    with pytest.raises(
        ValueError, match="scribe_native_workload_configuration_invalid"
    ):
        configured(monkeypatch, **{field: value})


def test_native_caller_cannot_reuse_provider_sovereign_identity(monkeypatch):
    monkeypatch.setattr(
        native,
        "SOVEREIGN_TOOL_AUTH_SETTINGS",
        SimpleNamespace(allowed_client_ids={RECEIVER}),
    )
    with pytest.raises(
        ValueError, match="scribe_native_workload_configuration_invalid"
    ):
        configured(monkeypatch)


def test_native_activation_requires_complete_clerk_authority(monkeypatch):
    configured(monkeypatch)
    monkeypatch.setattr(
        native,
        "AUTHORITY_KERNEL_SETTINGS",
        authority.AuthorityKernelSettings(False, 900, 300),
    )
    with pytest.raises(
        ValueError, match="scribe_native_workload_configuration_invalid"
    ):
        native.native_workload_settings(
            enabled=True, tenant_id=TENANT, audience=AUDIENCE, client_id=RECEIVER
        )


@pytest.mark.parametrize(
    "claim,value,status",
    [
        (None, None, None),
        ("aud", FOREIGN, 401),
        ("azp", FOREIGN, 403),
        ("roles", [], 403),
        ("roles", ["Scribe.Transcript.Write"], 403),
        ("signature", "foreign-key", 401),
        ("idtyp", "user", 403),
        ("tid", FOREIGN, 401),
    ],
)
def test_actual_signature_audience_role_and_singleton_authorization(
    monkeypatch, claim, value, status
):
    settings = configured(monkeypatch)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    decoder = entra_service.EntraTokenDecoder(settings)
    monkeypatch.setattr(
        decoder.jwks_client,
        "get_signing_key_from_jwt",
        lambda _: SimpleNamespace(key=key.public_key()),
    )
    now = int(time.time())
    claims = dict(
        iss=settings.issuer,
        aud=AUDIENCE,
        tid=TENANT,
        azp=RECEIVER,
        idtyp="app",
        roles=["AgentApi.Access"],
        sub="synthetic-service",
        iat=now,
        nbf=now,
        exp=now + 60,
    )
    if claim and claim != "signature":
        claims[claim] = value
    signing_key = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        if claim == "signature"
        else key
    )
    token = jwt.encode(
        claims, signing_key, algorithm="RS256", headers={"kid": "offline"}
    )
    authenticate = entra_service.EntraServiceAuthenticator(settings, decoder)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    if status:
        with pytest.raises(HTTPException) as error:
            authenticate(credentials)
        assert error.value.status_code == status
    else:
        assert authenticate(credentials).client_id == RECEIVER


def test_native_facade_uses_scoped_policies_only():
    from app.routes import scribe_native_capture as route

    assert (
        route.authenticate_agent_api_request
        is entra_service.authenticate_scribe_native_agent_request
    )
    assert (
        route.authenticate_scribe_capture_request
        is entra_service.authenticate_scribe_native_capture_request
    )
    assert (
        entra_service.authenticate_agent_api_request.settings
        is service_auth.AGENT_API_AUTH_SETTINGS
    )
    assert (
        entra_service.authenticate_scribe_capture_request.settings
        is service_auth.SCRIBE_CAPTURE_AUTH_SETTINGS
    )


def test_native_projection_is_api_only_and_session_prep_source_is_unchanged():
    root = Path(__file__).resolve().parents[2]
    rollout = (root / "infra/scribe_native_rollout.tf").read_text()
    session_prep = (root / "infra/session_prep_rollout.tf").read_text()
    for key in (
        "SCRIBE_NATIVE_WORKLOAD_ENABLED",
        "SCRIBE_NATIVE_WORKLOAD_AUDIENCE",
        "SCRIBE_NATIVE_WORKLOAD_CLIENT_ID",
    ):
        assert key in rollout and key not in session_prep
    assert (
        session_prep.count("tostring(var.session_prep_production_rollout.enabled)") == 3
    )
    assert (
        "native_authority_settings(auth)"
        in (root / "app/routes/scribe_native_capture.py").read_text()
    )
