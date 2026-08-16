from fastapi.testclient import TestClient

from app.routes.dependencies.entra_service import (
    EntraServicePrincipal,
    authenticate_agent_api_request,
)
from app.routes.middleware import allowed_hosts
from app.server import api_app


def test_authority_fixture_does_not_shadow_real_versions_module() -> None:
    from app.configuration.versions import VERSIONS_FILE

    assert VERSIONS_FILE.name == ".service-versions"


def test_authority_routes_are_registered_on_the_platform_server() -> None:
    paths = {getattr(route, "path", "") for route in api_app.routes}

    assert {
        "/embed/v1/context-tokens/anonymous",
        "/embed/v1/context-tokens/exchange",
        "/embed/v1/context-tokens/rotate",
        "/service/v1/agent/mount-context/resolve",
        "/service/v1/agent/conversation-turns/write",
    } <= paths


def test_authority_validation_errors_use_safe_contract_shapes() -> None:
    client = TestClient(api_app, base_url=allowed_hosts(with_scheme=True)[0])
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
        missing_interaction = client.post(
            "/service/v1/agent/conversation-turns/write",
            headers={
                "X-Cosmaneura-Mount-Context": "mctx_synthetic_token_000000000000",
                "X-Cosmaneura-Client-Turn-ID": ("11111111-1111-4111-8111-111111111111"),
                "Idempotency-Key": "idem-user-accepted-0001",
            },
            json={
                "phase": "user_accepted",
                "ingress_kind": "chat_text",
                "action_code": None,
                "message": {
                    "content_type": "text/plain",
                    "content": "Synthetic safe text",
                },
            },
        )
    finally:
        api_app.dependency_overrides.pop(authenticate_agent_api_request, None)

    assert missing_interaction.status_code == 400
    assert missing_interaction.json()["code"] == "INTERACTION_REQUIRED"
    assert "mctx_synthetic" not in missing_interaction.text
