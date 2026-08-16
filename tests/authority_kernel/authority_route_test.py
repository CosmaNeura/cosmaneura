from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.services.authority
from app.configuration.authority import AuthorityKernelSettings
from app.configuration.sentry import scrub_event
from app.routes.agent import agent_api_app, get_agent_authority_settings
from app.routes.dependencies.entra_service import (
    EntraServicePrincipal,
    authenticate_agent_api_request,
)
from app.routes.embed_context import (
    authenticate_embed_platform_session,
    embed_context_api_app,
    get_authority_kernel_settings,
)

from activemodel.session_manager import aglobal_session


def _isolated_app(router) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[aglobal_session] = lambda: None
    return app


def test_anonymous_issuance_is_safely_disabled() -> None:
    fastapi_app = _isolated_app(embed_context_api_app)
    client = TestClient(fastapi_app)

    response = client.post(
        "/embed/v1/context-tokens/anonymous",
        headers={"X-Cosmaneura-Embed-Version": "1.0"},
        json={"requested_surface": "public"},
    )

    assert response.status_code == 503
    assert response.headers["x-cosmaneura-embed-version"] == "1.0"
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["code"] == "SERVICE_UNAVAILABLE"
    assert "context_token" not in response.text


def test_exchange_feature_flag_fails_before_authority_lookup() -> None:
    fastapi_app = _isolated_app(embed_context_api_app)
    fastapi_app.dependency_overrides[authenticate_embed_platform_session] = (
        lambda: "user_test_provider"
    )
    fastapi_app.dependency_overrides[get_authority_kernel_settings] = lambda: (
        AuthorityKernelSettings(
            enabled=False,
            token_ttl_seconds=900,
            token_refresh_after_seconds=300,
        )
    )
    client = TestClient(fastapi_app)

    response = client.post(
        "/embed/v1/context-tokens/exchange",
        headers={"X-Cosmaneura-Embed-Version": "1.0"},
        json={
            "requested_surface": "provider_assistant",
            "record_ref": "record_provider_001",
        },
    )

    assert response.status_code == 503
    assert response.json()["code"] == "SERVICE_UNAVAILABLE"


def test_mount_resolution_returns_only_five_fields_and_lineage_headers(
    monkeypatch,
) -> None:
    fastapi_app = _isolated_app(agent_api_app)
    fastapi_app.dependency_overrides[authenticate_agent_api_request] = lambda: (
        EntraServicePrincipal(client_id="11111111-1111-4111-8111-111111111111")
    )
    fastapi_app.dependency_overrides[get_agent_authority_settings] = lambda: (
        AuthorityKernelSettings(
            enabled=True,
            token_ttl_seconds=900,
            token_refresh_after_seconds=300,
        )
    )
    interaction_ref = "ix_synthetic_interaction_0001"

    def synthetic_resolution(**kwargs):
        assert kwargs["mount_context_token"] == "mctx_synthetic_token_000000000000"
        assert isinstance(kwargs["client_turn_id"], UUID)
        return app.services.authority.MountResolutionResult(
            context=app.services.authority.ResolvedMountContext(
                surface="provider_assistant",
                auth_tier="provider",
                capabilities=("queue:draft", "trigger:file"),
                tenant_id="authority_tenant_0001",
                actor_id="authority_actor_0001",
                session_id="mount_session_0001",  # type: ignore[arg-type]
                person_id=None,
            ),
            interaction_ref=interaction_ref,
            reused=False,
        )

    monkeypatch.setattr(
        app.services.authority,
        "resolve_mount_context",
        synthetic_resolution,
    )
    client_turn_id = uuid4()
    client = TestClient(fastapi_app)

    response = client.post(
        "/service/v1/agent/mount-context/resolve",
        headers={
            "X-Cosmaneura-Mount-Context": "mctx_synthetic_token_000000000000",
            "X-Cosmaneura-Client-Turn-ID": str(client_turn_id),
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "surface": "provider_assistant",
        "auth_tier": "provider",
        "capabilities": ["queue:draft", "trigger:file"],
        "tenant_id": "authority_tenant_0001",
        "actor_id": "authority_actor_0001",
    }
    assert response.headers["x-cosmaneura-interaction-ref"] == interaction_ref
    assert response.headers["x-cosmaneura-client-turn-id"] == str(client_turn_id)
    assert response.headers["x-cosmaneura-interaction-reused"] == "false"
    assert response.headers["cache-control"] == "no-store"


def test_error_telemetry_scrubs_mount_context_tokens_case_insensitively() -> None:
    event = {
        "request": {
            "headers": {
                "authorization": "Bearer workload-secret",
                "X-Cosmaneura-Mount-Context": "mctx_plaintext-secret",
                "Content-Type": "application/json",
            }
        }
    }

    scrubbed = scrub_event(event, None)

    assert scrubbed["request"]["headers"] == {"Content-Type": "application/json"}
