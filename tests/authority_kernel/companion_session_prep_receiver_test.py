"""Companion brief wire/auth boundary; runtime persistence is covered separately."""

import json
import runpy
from contextlib import contextmanager
from dataclasses import is_dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi import HTTPException as FastAPIHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException
from starlette.requests import Request

import app.configuration.session_prep as prep_configuration
import app.routes.agent as agent
import app.services.session_prep as prep
import app.services.session_prep_runtime as runtime
from app.configuration.service_auth import AGENT_API_ACCESS_ROLE
from app.routes.companion_session_prep import PATH, router
from app.routes.dependencies.entra_service import (
    authenticate_companion_session_prep_request,
)
from app.server import (
    api_app,
    safe_authority_http_exception_handler,
    safe_request_validation_handler,
)

from activemodel.session_manager import aglobal_session

from tests.authority_kernel.companion_capture_identity_test import (
    COMPANION,
    configured,
)
from tests.authority_kernel.session_prep_receiver_test import _headers, _request_body
from tests.authority_kernel.workload_feature_boundaries_test import SCRIBE, SOVEREIGN


@pytest.mark.parametrize("enabled", [False, True])
def test_receiver_permission_is_separate_and_default_off(monkeypatch, enabled):
    monkeypatch.delenv("COMPANION_SESSION_PREP_ENABLED", raising=False)
    if enabled:
        monkeypatch.setenv("COMPANION_SESSION_PREP_ENABLED", "true")
    values = configured(monkeypatch)
    policy = values["COMPANION_SESSION_PREP_AUTH_SETTINGS"]
    assert policy.is_complete() and policy.enabled is enabled
    assert policy.allowed_client_ids == {COMPANION}
    assert values["SOVEREIGN_TOOL_AUTH_SETTINGS"].allowed_client_ids == {SOVEREIGN}
    assert values["SCRIBE_CAPTURE_AUTH_SETTINGS"].allowed_client_ids == {
        SCRIBE,
        COMPANION,
    }
    monkeypatch.setenv("AGENT_API_ENABLED", "false")
    from app.configuration import service_auth

    disabled = runpy.run_path(service_auth.__file__)
    assert not disabled["COMPANION_SESSION_PREP_AUTH_SETTINGS"].enabled


def test_missing_companion_identity_never_falls_back(monkeypatch):
    monkeypatch.setenv("COMPANION_SESSION_PREP_ENABLED", "true")
    policy = configured(monkeypatch, companion="")[
        "COMPANION_SESSION_PREP_AUTH_SETTINGS"
    ]
    assert not policy.is_complete() and not policy.allowed_client_ids


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("COMPANION_SESSION_PREP_ENABLED", "true")
    values = configured(monkeypatch)
    policy = values["COMPANION_SESSION_PREP_AUTH_SETTINGS"]
    monkeypatch.setattr(authenticate_companion_session_prep_request, "settings", policy)
    monkeypatch.setattr(
        authenticate_companion_session_prep_request,
        "decoder",
        lambda token: {
            "tid": policy.tenant_id,
            "azp": token,
            "idtyp": "app",
            "roles": [AGENT_API_ACCESS_ROLE],
        },
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[aglobal_session] = lambda: None

    async def handle_http_error(request: Request, exc: Exception):
        assert isinstance(exc, HTTPException)
        normalized = FastAPIHTTPException(
            status_code=exc.status_code, detail=exc.detail, headers=exc.headers
        )
        return await safe_authority_http_exception_handler(request, normalized)

    async def handle_validation_error(request: Request, exc: Exception):
        assert isinstance(exc, RequestValidationError)
        return await safe_request_validation_handler(request, exc)

    app.add_exception_handler(HTTPException, handle_http_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    return TestClient(app)


def headers():
    return {**_headers(), "Authorization": f"Bearer {COMPANION}"}


def request_arguments() -> dict[str, object]:
    arguments = _request_body()["arguments"]
    assert isinstance(arguments, dict)
    return arguments


def test_platform_registers_receiver_hidden_from_browser_schema():
    from app.routes.middleware import allowed_hosts

    client = TestClient(api_app, base_url=f"https://{allowed_hosts()[0]}")
    response = client.post(PATH, content=b"private sentinel")
    assert response.status_code == 503
    assert "no-store" in response.headers["cache-control"].split(", ")
    assert PATH not in api_app.openapi()["paths"]


@pytest.mark.parametrize("caller", [SCRIBE, SOVEREIGN, str(uuid4())])
def test_other_workloads_rejected_before_body_parsing(client, caller):
    response = client.post(
        PATH, headers={"Authorization": f"Bearer {caller}"}, content=b"private sentinel"
    )
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert "private sentinel" not in response.text


@pytest.mark.parametrize(
    "claim,status",
    [
        ({"tid": str(uuid4())}, 401),
        ({"roles": ["Scribe.Transcript.Write"]}, 403),
        ({"idtyp": "user"}, 403),
    ],
)
def test_wrong_tenant_role_or_user_token_is_denied(client, monkeypatch, claim, status):
    old = authenticate_companion_session_prep_request.decoder
    assert callable(old)
    monkeypatch.setattr(
        authenticate_companion_session_prep_request,
        "decoder",
        lambda token: {**old(token), **claim},
    )
    response = client.post(PATH, headers=headers(), json=_request_body())
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"


def test_disabled_policy_cannot_read_even_with_valid_token(client, monkeypatch):
    old = authenticate_companion_session_prep_request.settings
    assert is_dataclass(old) and not isinstance(old, type)
    monkeypatch.setattr(
        authenticate_companion_session_prep_request,
        "settings",
        replace(old, enabled=False),
    )
    assert client.post(PATH, headers=headers(), json=_request_body()).status_code == 503


@pytest.mark.parametrize(
    "change,status",
    [
        ({"tool_name": "provider-question.context.read"}, 404),
        ({"tool_name": "conversation.context.read"}, 404),
        ({"tool_version": "1.0.1"}, 404),
        ({"subject_ref": "another_patient_ref"}, 422),
        ({"ehr_read_access": None}, 422),
        ({"patient_id": "private sentinel"}, 422),
        (
            {
                "arguments": {
                    **request_arguments(),
                    "detail": "supporting_context",
                }
            },
            422,
        ),
        ({"arguments": {**request_arguments(), "purpose": "other"}}, 422),
        (
            {
                "arguments": {
                    **request_arguments(),
                    "patient_id": "private sentinel",
                }
            },
            422,
        ),
    ],
)
def test_only_frozen_brief_contract_reaches_authority(
    client, monkeypatch, change, status
):
    def forbidden(**kwargs):
        pytest.fail("invalid request reached authority")

    monkeypatch.setattr(prep, "authorize_session_prep_read", forbidden)
    response = client.post(PATH, headers=headers(), json={**_request_body(), **change})
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert "private sentinel" not in response.text


@pytest.mark.parametrize(
    "fault",
    [
        "duplicate-json",
        "oversize",
        "query",
        "encoding",
        "duplicate-header",
        "invalid-header",
    ],
)
def test_transport_is_bounded_closed_and_value_free(client, fault):
    data = json.dumps(_request_body())
    h = {**headers(), "Content-Type": "application/json"}
    path = PATH
    if fault == "duplicate-json":
        data = data.replace(
            '"subject_ref": null', '"subject_ref": null, "subject_ref": null'
        )
    elif fault == "oversize":
        data += " " * 8192
    elif fault == "query":
        path += "?patient=private-sentinel"
    elif fault == "encoding":
        h["Content-Encoding"] = "gzip"
    elif fault == "duplicate-header":
        h = [*h.items(), ("Idempotency-Key", "second-private-sentinel")]
    else:
        h["X-Cosmaneura-Client-Turn-ID"] = "private-sentinel"
    response = client.post(path, headers=h, content=data)
    assert response.status_code in {400, 422}
    assert response.headers["cache-control"] == "no-store"
    assert "private-sentinel" not in response.text


def test_provider_disabled_never_falls_back_to_synthetic(client, monkeypatch):
    monkeypatch.setattr(
        prep_configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        prep_configuration.SessionPrepProviderSettings(),
    )
    synthetic = prep_configuration.SessionPrepSyntheticSettings(
        True,
        "context_synthetic_001",
        "user_test",
        "appointment_test",
        "synthetic_preview_fixture_test",
    )
    client.app.dependency_overrides[agent.get_session_prep_synthetic_settings] = (
        lambda: synthetic
    )
    assert synthetic.is_complete()
    response = client.post(PATH, headers=headers(), json=_request_body())
    assert response.status_code == 503


@pytest.fixture
def runtime_calls(monkeypatch):
    monkeypatch.setattr(
        prep_configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        prep_configuration.SessionPrepProviderSettings(True),
    )
    calls = []

    @contextmanager
    def session():
        yield SimpleNamespace(commit=lambda: calls.append(("commit", None)))

    monkeypatch.setattr(agent, "get_session", session)

    def authorize(**kwargs):
        calls.append(("authorize", kwargs))
        return SimpleNamespace(
            authorized=SimpleNamespace(
                context=SimpleNamespace(
                    session_prep_context_ref="context_synthetic_001"
                )
            )
        )

    monkeypatch.setattr(prep, "authorize_session_prep_read", authorize)
    result = json.loads(
        (
            Path(__file__).parents[2]
            / "contracts/fixtures/provider-session-prep-v1/result-ready-brief.json"
        ).read_text()
    )
    response = prep.SessionPrepToolResponse.model_validate(
        {
            "tool_call_ref": "tool_synthetic_receipt_001",
            "tool_name": "session-prep.context.read",
            "tool_version": "1.0.0",
            "status": "completed",
            "approval_ref": None,
            "access_intent_ref": None,
            "access_outcome_ref": None,
            "result_classification": "phi_possible",
            "result": result,
            "recorded_at": "2026-09-02T12:00:03Z",
        }
    )

    def invoke(**kwargs):
        calls.append(("read", kwargs))
        return prep.SessionPrepInvocation(
            response=response, replayed=len([c for c in calls if c[0] == "read"]) > 1
        )

    monkeypatch.setattr(runtime, "invoke_provider_session_prep", invoke)
    return calls


def test_explicit_provider_read_preserves_authority_receipt_and_commit(
    client, runtime_calls
):
    h = headers()
    first = client.post(PATH, headers=h, json=_request_body())
    second = client.post(PATH, headers=h, json=_request_body())
    assert first.status_code == second.status_code == 200
    assert [c[0] for c in runtime_calls] == ["authorize", "read", "commit"] * 2
    assert first.json() == second.json()
    assert first.json()["result"]["prepared_brief"]
    assert first.json()["result"]["supporting_context"] is None
    assert first.headers["idempotency-replayed"] == "false"
    assert second.headers["idempotency-replayed"] == "true"
    assert (
        first.headers["x-cosmaneura-client-turn-id"] == h["X-Cosmaneura-Client-Turn-ID"]
    )
    assert first.headers["cache-control"] == "no-store"
    assert len(first.content) < 262144
    auth, read = runtime_calls[0][1], runtime_calls[1][1]
    assert auth["workload_client_id"] == read["workload_client_id"] == COMPANION
    assert auth["mount_context_token"] == h["X-Cosmaneura-Mount-Context"]
    assert read["detail"] == "brief"
    assert (
        read["source_turn_ref"]
        == auth["source_turn_ref"]
        == request_arguments()["source_turn_ref"]
    )


@pytest.mark.parametrize(
    "error,status",
    [
        (prep.SessionPrepScopeDenied, 403),
        (prep.SessionPrepIdempotencyConflict, 409),
        (prep.SessionPrepInvariantError, 503),
    ],
)
def test_revoked_or_conflicting_reads_return_no_brief(
    client, runtime_calls, monkeypatch, error, status
):
    def rejected(**kwargs):
        raise error()

    monkeypatch.setattr(runtime, "invoke_provider_session_prep", rejected)
    response = client.post(PATH, headers=headers(), json=_request_body())
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert "prepared_brief" not in response.text
    assert "commit" not in [c[0] for c in runtime_calls]
