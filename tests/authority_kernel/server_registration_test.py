import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.routes.dependencies.entra_service import (
    EntraServicePrincipal,
    authenticate_agent_api_request,
)
from app.routes.middleware import allowed_hosts
from app.server import api_app


def test_authority_routes_are_registered_on_the_platform_server() -> None:
    paths = {getattr(route, "path", "") for route in api_app.routes}

    assert {
        "/embed/v1/context-tokens/anonymous",
        "/embed/v1/context-tokens/exchange",
        "/embed/v1/context-tokens/rotate",
        "/service/v1/agent/mount-context/bootstrap",
        "/service/v1/agent/mount-context/resolve",
        "/service/v1/agent/audit-events/write",
        "/service/v1/agent/conversation-turns/write",
        "/service/v1/agent/model-usage-events/report",
    } <= paths


def test_authority_validation_errors_use_safe_contract_shapes() -> None:
    client = TestClient(api_app, base_url=f"https://{allowed_hosts()[0]}")
    unsupported_version = client.post(
        "/embed/v1/context-tokens/anonymous",
        json={"requested_surface": "public"},
    )

    assert unsupported_version.status_code == 426
    assert unsupported_version.json()["code"] == "EMBED_VERSION_UNSUPPORTED"
    assert unsupported_version.headers["x-cosmaneura-embed-version"] == "1.0"

    api_app.dependency_overrides[authenticate_agent_api_request] = lambda: (
        EntraServicePrincipal(client_id="11111111-1111-4111-8111-111111111111")
    )
    try:
        missing_context = client.post(
            "/service/v1/agent/mount-context/resolve",
            headers={
                "X-Cosmaneura-Client-Turn-ID": ("11111111-1111-4111-8111-111111111111")
            },
        )
    finally:
        api_app.dependency_overrides.pop(authenticate_agent_api_request, None)

    assert missing_context.status_code == 400
    assert missing_context.json()["code"] == "CONTEXT_REQUIRED"
    assert "input" not in missing_context.text

    api_app.dependency_overrides[authenticate_agent_api_request] = lambda: (
        EntraServicePrincipal(client_id="11111111-1111-4111-8111-111111111111")
    )
    try:
        invalid_turn = client.post(
            "/service/v1/agent/mount-context/resolve",
            headers={
                "X-Cosmaneura-Mount-Context": "mctx_synthetic_token_000000000000",
                "X-Cosmaneura-Client-Turn-ID": "not-a-client-turn",
            },
        )
    finally:
        api_app.dependency_overrides.pop(authenticate_agent_api_request, None)

    assert invalid_turn.status_code == 400
    assert invalid_turn.json()["code"] == "CLIENT_TURN_INVALID"
    assert "not-a-client-turn" not in invalid_turn.text

    api_app.dependency_overrides[authenticate_agent_api_request] = lambda: (
        EntraServicePrincipal(client_id="11111111-1111-4111-8111-111111111111")
    )
    try:
        invalid_bootstrap_context = client.post(
            "/service/v1/agent/mount-context/bootstrap",
            headers={
                "X-Cosmaneura-Mount-Context": "secret",
                "X-Trace-ID": "trace_synthetic_bootstrap_0005",
            },
        )
    finally:
        api_app.dependency_overrides.pop(authenticate_agent_api_request, None)

    assert invalid_bootstrap_context.status_code == 400
    assert invalid_bootstrap_context.json()["code"] == "VALIDATION_FAILED"
    assert (
        invalid_bootstrap_context.headers["x-trace-id"]
        == "trace_synthetic_bootstrap_0005"
    )
    assert (
        invalid_bootstrap_context.json()["trace_id"] == "trace_synthetic_bootstrap_0005"
    )
    assert "secret" not in invalid_bootstrap_context.text

    api_app.dependency_overrides[authenticate_agent_api_request] = lambda: (
        EntraServicePrincipal(client_id="11111111-1111-4111-8111-111111111111")
    )
    try:
        rejected_content = client.post(
            "/service/v1/agent/audit-events/write",
            headers={
                "X-Cosmaneura-Interaction-Ref": "ix_synthetic_interaction_0001",
                "Idempotency-Key": "opaque-audit-key-closed-0003",
            },
            json={"prompt": "raw patient value"},
        )
    finally:
        api_app.dependency_overrides.pop(authenticate_agent_api_request, None)

    assert rejected_content.status_code == 422
    assert rejected_content.json()["code"] == "VALIDATION_FAILED"
    assert "raw patient value" not in rejected_content.text


def test_bootstrap_denied_workload_preserves_trace_and_precedes_header_validation() -> (
    None
):
    client = TestClient(api_app, base_url=f"https://{allowed_hosts()[0]}")
    trace_id = "trace_bootstrap_denied_workload_0001"

    def reject_workload() -> None:
        raise HTTPException(status_code=403, detail="sentinel upstream detail")

    api_app.dependency_overrides[authenticate_agent_api_request] = reject_workload
    try:
        response = client.post(
            "/service/v1/agent/mount-context/bootstrap",
            headers={"X-Trace-ID": trace_id},
            content=b"sentinel private body",
        )
    finally:
        api_app.dependency_overrides.pop(authenticate_agent_api_request, None)

    assert response.status_code == 403
    assert response.json()["code"] == "WORKLOAD_NOT_ALLOWED"
    assert response.headers["x-trace-id"] == trace_id
    assert response.json()["trace_id"] == trace_id
    assert "sentinel upstream detail" not in response.text
    assert "sentinel private body" not in response.text


@pytest.mark.parametrize(
    ("mount_context", "trace_id", "expected_code", "preserves_trace"),
    [
        (None, "trace_bootstrap_missing_context_0001", "CONTEXT_REQUIRED", True),
        ("secret", "trace_bootstrap_invalid_context_0001", "VALIDATION_FAILED", True),
        ("secret", "unsafe trace sentinel!", "VALIDATION_FAILED", False),
    ],
)
def test_bootstrap_header_validation_uses_safe_trace_contract(
    mount_context: str | None,
    trace_id: str,
    expected_code: str,
    preserves_trace: bool,
) -> None:
    client = TestClient(api_app, base_url=f"https://{allowed_hosts()[0]}")
    api_app.dependency_overrides[authenticate_agent_api_request] = lambda: (
        EntraServicePrincipal(client_id="11111111-1111-4111-8111-111111111111")
    )
    headers = {"X-Trace-ID": trace_id}
    if mount_context is not None:
        headers["X-Cosmaneura-Mount-Context"] = mount_context
    try:
        response = client.post(
            "/service/v1/agent/mount-context/bootstrap",
            headers=headers,
        )
    finally:
        api_app.dependency_overrides.pop(authenticate_agent_api_request, None)

    assert response.status_code == 400
    assert response.json()["code"] == expected_code
    returned_trace = response.headers["x-trace-id"]
    assert response.json()["trace_id"] == returned_trace
    if preserves_trace:
        assert returned_trace == trace_id
    else:
        assert returned_trace.startswith("trace_")
        assert returned_trace != trace_id
        assert trace_id not in response.text
