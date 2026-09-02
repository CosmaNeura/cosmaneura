"""Contract tests for the dark Session Prep Agent API receiver."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import app.services.authority
from app.routes.agent import agent_api_app
from app.routes.dependencies.entra_service import (
    EntraServicePrincipal,
    authenticate_agent_api_request,
)

from activemodel.session_manager import aglobal_session

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "contracts" / "fixtures" / "provider-session-prep-v1"


def _isolated_app() -> FastAPI:
    app = FastAPI()
    app.include_router(agent_api_app)
    app.dependency_overrides[aglobal_session] = lambda: None
    return app


def _headers() -> dict[str, str]:
    return {
        "X-Cosmaneura-Mount-Context": "mctx_synthetic_token_000000000000",
        "X-Cosmaneura-Interaction-Ref": "ix_synthetic_interaction_0001",
        "X-Cosmaneura-Client-Turn-ID": str(uuid4()),
        "Idempotency-Key": "idem-session-prep-read-0001",
        "X-Trace-ID": "trace_session_prep_receiver_0001",
    }


def _request_body(fixture_name: str = "request-brief.json") -> dict[str, object]:
    return json.loads((FIXTURE_DIR / fixture_name).read_text())


def _authorized_client() -> TestClient:
    app = _isolated_app()
    app.dependency_overrides[authenticate_agent_api_request] = lambda: (
        EntraServicePrincipal(client_id="11111111-1111-4111-8111-111111111111")
    )
    return TestClient(app)


def test_receiver_authenticates_before_reading_the_body() -> None:
    app = _isolated_app()

    def reject_workload() -> None:
        raise HTTPException(status_code=401)

    app.dependency_overrides[authenticate_agent_api_request] = reject_workload
    response = TestClient(app).post(
        "/service/v1/agent/tools/invoke",
        headers={**_headers(), "Content-Type": "application/json"},
        content=b'{"patient":"sentinel private value"',
    )

    assert response.status_code == 401
    assert "sentinel private value" not in response.text


@pytest.mark.parametrize(
    "fixture_name",
    ["request-brief.json", "request-supporting-context.json"],
)
def test_receiver_recognizes_the_frozen_requests_but_execution_stays_dark(
    monkeypatch: pytest.MonkeyPatch,
    fixture_name: str,
) -> None:
    def unexpected_authorization(**kwargs: object) -> None:
        pytest.fail(f"dark receiver reached authority or data access: {kwargs!r}")

    monkeypatch.setattr(
        app.services.authority,
        "authorize_interaction",
        unexpected_authorization,
    )
    response = _authorized_client().post(
        "/service/v1/agent/tools/invoke",
        headers=_headers(),
        json=_request_body(fixture_name),
    )

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-trace-id"] == "trace_session_prep_receiver_0001"
    assert response.json()["code"] == "PLATFORM_UNAVAILABLE"
    assert response.json()["retryable"] is False
    assert "context_synthetic_" not in response.text


@pytest.mark.parametrize(
    ("tool_name", "tool_version"),
    [
        ("unknown.context.read", "1.0.0"),
        ("session-prep.context.read", "1.0.1"),
    ],
)
def test_receiver_rejects_unknown_name_and_version_without_reflection(
    tool_name: str,
    tool_version: str,
) -> None:
    body = _request_body()
    body["tool_name"] = tool_name
    body["tool_version"] = tool_version

    response = _authorized_client().post(
        "/service/v1/agent/tools/invoke",
        headers=_headers(),
        json=body,
    )

    assert response.status_code == 404
    assert response.json()["code"] == "RESOURCE_NOT_FOUND"
    assert tool_name not in response.text
    assert tool_version not in response.text


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body.update({"patient_id": "sentinel_patient_value"}),
        lambda body: body["arguments"].update(  # type: ignore[union-attr]
            {"patient_id": "sentinel_patient_value"}
        ),
        lambda body: body.update({"subject_ref": "patient_ref_00000001"}),
        lambda body: body.update({"ehr_read_access": {"sentinel": "private"}}),
        lambda body: body["arguments"].update(  # type: ignore[union-attr]
            {"source_turn_ref": "short"}
        ),
        lambda body: body["arguments"].update(  # type: ignore[union-attr]
            {"purpose": "patient_search"}
        ),
        lambda body: body["arguments"].update(  # type: ignore[union-attr]
            {"detail": "full_chart"}
        ),
    ],
)
def test_receiver_rejects_unratified_fields_and_selectors(
    mutate,
) -> None:
    body = _request_body()
    mutate(body)

    response = _authorized_client().post(
        "/service/v1/agent/tools/invoke",
        headers=_headers(),
        json=body,
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_FAILED"
    assert "sentinel_patient_value" not in response.text
    assert "full_chart" not in response.text


@pytest.mark.parametrize(
    ("content_type", "content"),
    [
        ("application/json", b'{"tool_name":"sentinel"'),
        ("text/plain", b"sentinel private value"),
    ],
)
def test_receiver_rejects_invalid_content_without_reflection(
    content_type: str,
    content: bytes,
) -> None:
    response = _authorized_client().post(
        "/service/v1/agent/tools/invoke",
        headers={**_headers(), "Content-Type": content_type},
        content=content,
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_FAILED"
    assert "sentinel" not in response.text


def test_receiver_rejects_deep_json_recursion_without_500() -> None:
    content = (
        json.dumps(_request_body())
        .replace(
            '"detail": "brief"', '"detail": "brief", "nested": ' + "[" * 500 + "]" * 500
        )
        .encode()
    )

    response = _authorized_client().post(
        "/service/v1/agent/tools/invoke",
        headers={**_headers(), "Content-Type": "application/json"},
        content=content,
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_FAILED"


def test_receiver_rejects_duplicate_json_keys_and_oversized_bodies() -> None:
    duplicate = json.dumps(_request_body()).replace(
        '"tool_name": "session-prep.context.read",',
        '"tool_name": "session-prep.context.read", "tool_name": "sentinel",',
    )
    client = _authorized_client()

    duplicate_response = client.post(
        "/service/v1/agent/tools/invoke",
        headers={**_headers(), "Content-Type": "application/json"},
        content=duplicate,
    )
    oversized_response = client.post(
        "/service/v1/agent/tools/invoke",
        headers={**_headers(), "Content-Type": "application/json"},
        content=b"{" + (b"x" * (8 * 1024)),
    )

    for response in (duplicate_response, oversized_response):
        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_FAILED"
    assert "sentinel" not in duplicate_response.text


@pytest.mark.parametrize(
    ("suffix", "extra_headers"),
    [
        ("?record_ref=sentinel", {}),
        ("", {"Content-Encoding": "gzip"}),
    ],
)
def test_receiver_rejects_transport_selectors(
    suffix: str,
    extra_headers: dict[str, str],
) -> None:
    response = _authorized_client().post(
        f"/service/v1/agent/tools/invoke{suffix}",
        headers={**_headers(), **extra_headers},
        json=_request_body(),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_FAILED"
    assert "sentinel" not in response.text
