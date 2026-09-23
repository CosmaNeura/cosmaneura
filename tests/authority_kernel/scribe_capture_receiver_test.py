"""Versioned capture facade tests; no external auth or Scribe calls."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import app.services.scribe_capture_scope as scope
from app.configuration.scribe_capture import ScribeCaptureSettings
from app.routes.agent import agent_api_app
from app.routes.dependencies.entra_service import (
    EntraServicePrincipal,
    authenticate_scribe_capture_request,
)
from app.routes.scribe_capture import get_capture_settings
from app.services import authority

from activemodel.session_manager import aglobal_session

PATH = "/service/v1/agent/scribe/capture-bindings/resolve"


def body():
    return {
        "workspace_id": str(uuid4()),
        "request_nonce": str(uuid4()),
        "appointment_ref": "appointment_01j00000000000000000000000",
    }


def headers():
    return {
        "X-Cosmaneura-Mount-Context": "mctx_synthetic_secret_00000001",
        "X-Cosmaneura-Interaction-Ref": "ix_synthetic_interaction_00001",
        "X-Cosmaneura-Client-Turn-ID": str(uuid4()),
        "X-Cosmaneura-Capture-Version": "1",
    }


def client(*, enabled=True):
    app = FastAPI()
    app.include_router(agent_api_app)
    app.dependency_overrides[aglobal_session] = lambda: None
    app.dependency_overrides[authenticate_scribe_capture_request] = lambda: (
        EntraServicePrincipal(client_id="11111111-1111-4111-8111-111111111111")
    )
    app.dependency_overrides[get_capture_settings] = lambda: ScribeCaptureSettings(
        enabled=enabled
    )
    return TestClient(app)


def test_authentication_runs_before_body_and_capture_service(monkeypatch):
    connection = client()
    assert isinstance(connection.app, FastAPI)

    def deny():
        raise HTTPException(status_code=401)

    connection.app.dependency_overrides[authenticate_scribe_capture_request] = deny
    monkeypatch.setattr(
        scope, "resolve_capture_binding", lambda **_: pytest.fail("scope reached")
    )
    response = connection.post(PATH, content=b'{"private_sentinel":', headers=headers())
    assert response.status_code == 401
    assert "private_sentinel" not in response.text


def test_default_off_does_not_read_body_or_resolve_scope(monkeypatch):
    monkeypatch.setattr(
        scope, "resolve_capture_binding", lambda **_: pytest.fail("scope reached")
    )
    response = client(enabled=False).post(
        PATH, headers=headers(), content=b"private_sentinel"
    )
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert "private_sentinel" not in response.text


@pytest.mark.parametrize(
    "field",
    [
        "actor_id",
        "doctor_id",
        "tenant_id",
        "patient_id",
        "scribe_tenant_id",
        "provider_link_id",
        "binding_ref",
    ],
)
def test_body_identity_and_authority_hints_are_forbidden(monkeypatch, field):
    monkeypatch.setattr(
        scope, "resolve_capture_binding", lambda **_: pytest.fail("scope reached")
    )
    response = client().post(
        PATH, headers=headers(), json={**body(), field: "private_sentinel"}
    )
    assert response.status_code == 422
    assert "private_sentinel" not in response.text


@pytest.mark.parametrize(
    "change",
    [
        "version",
        "token",
        "turn",
        "interaction",
        "appointment",
        "nonce",
        "workspace",
        "query",
        "encoding",
        "content_type",
        "oversized",
        "duplicate_key",
        "duplicate_header",
        "malformed",
    ],
)
def test_malformed_or_ambiguous_transport_is_rejected_without_reflection(
    monkeypatch, change
):
    monkeypatch.setattr(
        scope, "resolve_capture_binding", lambda **_: pytest.fail("scope reached")
    )
    payload = body()
    request_headers = headers()
    path = PATH
    if change in {"appointment", "nonce", "workspace"}:
        payload[
            {
                "appointment": "appointment_ref",
                "nonce": "request_nonce",
                "workspace": "workspace_id",
            }[change]
        ] = "private_sentinel"
    if change in {"version", "token", "turn", "interaction"}:
        name = {
            "version": "Version",
            "token": "Mount-Context",
            "turn": "Client-Turn-ID",
            "interaction": "Interaction-Ref",
        }[change]
        request_headers[
            "X-Cosmaneura-" + ("Capture-Version" if name == "Version" else name)
        ] = "bad"
    if change == "query":
        path += "?patient_id=private_sentinel"
    if change == "encoding":
        request_headers["Content-Encoding"] = "gzip"
    request_headers["Content-Type"] = (
        "text/plain" if change == "content_type" else "application/json"
    )
    content = json.dumps(payload)
    if change == "oversized":
        content = "private_sentinel" * 300
    elif change == "duplicate_key":
        content = content[:-1] + ',"workspace_id":"private_sentinel"}'
    elif change == "malformed":
        content = '{"private_sentinel":'
    request_headers = list(request_headers.items())
    if change == "duplicate_header":
        request_headers.append(("X-Cosmaneura-Capture-Version", "1"))
    response = client().post(path, headers=request_headers, content=content)
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert "private_sentinel" not in response.text


def test_success_carries_only_current_scope_and_echoed_nonce(monkeypatch):
    calls = []

    def resolve(**kwargs):
        calls.append(kwargs)
        return scope.CaptureBindingSnapshot(
            binding_ref="scribe_capture_binding_01j00000000000000000000000",
            workspace_id=kwargs["workspace_id"],
            appointment_ref=kwargs["appointment_ref"],
            request_nonce=kwargs["request_nonce"],
            actor_ref="authority_actor_01j00000000000000000000000",
            tenant_ref="authority_tenant_01j00000000000000000000000",
            checked_at=datetime.now(UTC),
        )

    monkeypatch.setattr(scope, "resolve_capture_binding", resolve)
    payload = body()
    response = client().post(PATH, headers=headers(), json=payload)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-cosmaneura-capture-version"] == "1"
    assert set(response.json()) == {
        "contract_version",
        "binding_ref",
        "workspace_id",
        "appointment_ref",
        "actor_ref",
        "tenant_ref",
        "request_nonce",
        "checked_at",
    }
    assert response.json()["request_nonce"] == payload["request_nonce"]
    assert calls[0]["workload_client_id"] == "11111111-1111-4111-8111-111111111111"
    assert "synthetic_secret" not in response.text


@pytest.mark.parametrize(
    "error",
    [
        authority.ContextTokenExpiredError,
        authority.ContextTokenInvalidError,
        authority.ClientTurnConflictError,
        scope.CaptureScopeDenied,
    ],
)
def test_denial_does_not_reflect_exception_or_scope(monkeypatch, error):
    def reject(**_):
        raise error("private_sentinel")

    monkeypatch.setattr(scope, "resolve_capture_binding", reject)
    response = client().post(PATH, headers=headers(), json=body())
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert "private_sentinel" not in response.text
