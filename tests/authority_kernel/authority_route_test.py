from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import app.services.authority
import app.services.authority_conversation
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


def test_conversation_write_returns_contract_receipt_and_forwards_no_browser_scope(
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
    client_turn_id = uuid4()
    recorded_at = datetime.now(UTC)

    def synthetic_write(**kwargs):
        assert kwargs == {
            "workload_client_id": "11111111-1111-4111-8111-111111111111",
            "mount_context_token": "mctx_synthetic_token_000000000000",
            "interaction_ref": "ix_synthetic_interaction_0001",
            "client_turn_id": client_turn_id,
            "idempotency_key": "idem-user-accepted-0001",
            "payload": {
                "phase": "user_accepted",
                "ingress_kind": "chat_text",
                "action_code": None,
                "message": {
                    "content_type": "text/plain",
                    "content": "Exact accepted text",
                },
            },
            "settings": fastapi_app.dependency_overrides[
                get_agent_authority_settings
            ](),
        }
        return app.services.authority_conversation.ConversationWriteResult(
            client_turn_id=client_turn_id,
            turn_ref="conversation_turn_0000000000000001",
            phase="user_accepted",
            state="accepted",
            recorded_at=recorded_at,
            replayed=False,
        )

    monkeypatch.setattr(
        app.services.authority_conversation,
        "write_conversation_turn",
        synthetic_write,
    )
    client = TestClient(fastapi_app)
    response = client.post(
        "/service/v1/agent/conversation-turns/write",
        headers={
            "X-Cosmaneura-Mount-Context": "mctx_synthetic_token_000000000000",
            "X-Cosmaneura-Interaction-Ref": "ix_synthetic_interaction_0001",
            "X-Cosmaneura-Client-Turn-ID": str(client_turn_id),
            "Idempotency-Key": "idem-user-accepted-0001",
        },
        json={
            "phase": "user_accepted",
            "ingress_kind": "chat_text",
            "action_code": None,
            "message": {
                "content_type": "text/plain",
                "content": "Exact accepted text",
            },
        },
    )

    assert response.status_code == 201
    assert response.json() == {
        "client_turn_id": str(client_turn_id),
        "turn_ref": "conversation_turn_0000000000000001",
        "phase": "user_accepted",
        "state": "accepted",
        "recorded_at": recorded_at.isoformat().replace("+00:00", "Z"),
    }
    assert response.headers["x-cosmaneura-client-turn-id"] == str(client_turn_id)
    assert response.headers["idempotency-replayed"] == "false"
    assert response.headers["cache-control"] == "no-store"


def test_conversation_write_authenticates_before_reading_invalid_body() -> None:
    fastapi_app = _isolated_app(agent_api_app)

    def reject_workload() -> None:
        raise HTTPException(status_code=401)

    fastapi_app.dependency_overrides[authenticate_agent_api_request] = reject_workload
    client = TestClient(fastapi_app)

    response = client.post(
        "/service/v1/agent/conversation-turns/write",
        headers={
            "Content-Type": "application/json",
            "X-Cosmaneura-Mount-Context": "mctx_synthetic_token_000000000000",
            "X-Cosmaneura-Interaction-Ref": "ix_synthetic_interaction_0001",
            "X-Cosmaneura-Client-Turn-ID": str(uuid4()),
            "Idempotency-Key": "idem-user-accepted-0001",
        },
        content=b'{"phase":',
    )

    assert response.status_code == 401

    missing_context = client.post(
        "/service/v1/agent/conversation-turns/write",
        headers={
            "Content-Type": "application/json",
            "X-Cosmaneura-Interaction-Ref": "ix_synthetic_interaction_0001",
            "X-Cosmaneura-Client-Turn-ID": str(uuid4()),
            "Idempotency-Key": "idem-user-accepted-0001",
        },
        content=b'{"phase":',
    )

    assert missing_context.status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {
            "ingress_kind": "chat_text",
            "action_code": None,
            "message": {"content_type": "text/plain", "content": "Missing phase"},
        },
        {
            "phase": "user_accepted",
            "ingress_kind": "chat_text",
            "action_code": None,
            "message": {"content_type": "text/plain", "content": "Closed"},
            "browser_history": [],
        },
        {
            "phase": "final_response",
            "outcome": "completed",
            "response": {"content_type": "text/plain", "content": "Safe text"},
            "presentation": {
                "mode": "sensitive_disclosure",
                "suppress": ["disclaimer"],
                "human_contact": None,
            },
        },
    ],
)
def test_conversation_write_rejects_missing_phase_browser_fields_and_unsafe_sensitive_shape(
    body: dict[str, object],
) -> None:
    fastapi_app = _isolated_app(agent_api_app)
    fastapi_app.dependency_overrides[authenticate_agent_api_request] = lambda: (
        EntraServicePrincipal(client_id="11111111-1111-4111-8111-111111111111")
    )
    client = TestClient(fastapi_app)

    response = client.post(
        "/service/v1/agent/conversation-turns/write",
        headers={
            "X-Cosmaneura-Mount-Context": "mctx_synthetic_token_000000000000",
            "X-Cosmaneura-Interaction-Ref": "ix_synthetic_interaction_0001",
            "X-Cosmaneura-Client-Turn-ID": str(uuid4()),
            "Idempotency-Key": "idem-user-accepted-0001",
        },
        json=body,
    )

    expected_code = (
        "TURN_PHASE_REQUIRED" if "phase" not in body else "VALIDATION_FAILED"
    )
    expected_status = 400 if "phase" not in body else 422
    assert response.status_code == expected_status
    assert response.json()["code"] == expected_code
    assert "browser_history" not in response.text


def test_error_telemetry_scrubs_mount_context_tokens_case_insensitively() -> None:
    event = {
        "request": {
            "headers": {
                "authorization": "Bearer workload-secret",
                "Idempotency-Key": "idem-sensitive-correlation",
                "X-Cosmaneura-Client-Turn-ID": str(uuid4()),
                "X-Cosmaneura-Interaction-Ref": "ix_sensitive-correlation",
                "X-Cosmaneura-Mount-Context": "mctx_plaintext-secret",
                "Content-Type": "application/json",
            }
        }
    }

    scrubbed = scrub_event(event, None)

    assert scrubbed["request"]["headers"] == {"Content-Type": "application/json"}
