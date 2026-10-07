from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.configuration import billing_header as config
from app.configuration.cab_bridge import CAB_AUTHORIZE_POLICY, CAB_BRIDGE_SETTINGS
from app.configuration.clerk import VerifiedProviderSession
from app.routes.cab_authorization import authenticate_cab, router
from app.routes.dependencies.entra_service import EntraServiceAuthenticator
from app.services import billing_header_operations as prepared
from app.services import cab_bridge_authority as bridge
from app.services.cab_bridge_contract import CabAuthorizationRequest, digest

from activemodel.session_manager import get_session
from app.models.billing_header_action import BillingHeaderAction as Action
from sqlalchemy import text

from tests.billing_header.operations_test import ISSUER, command_for
from tests.billing_header.operations_test import case as _source_case

case = _source_case  # Reuse the same isolated, owned source fixture.


def setup_action(case, *, purpose="completed-note.accept", command=None):
    command_source = command_for(case)
    prepared.prepare_operation(case.actor, case.note_id, command_source)
    operation_id = UUID(command_source.operationId)
    payload, action_id = bridge.begin_action(
        case.actor,
        operation_id,
        purpose,
        {"expectedSource": None} if command is None else command,
    )
    request = CabAuthorizationRequest.model_validate(
        {
            "schemaVersion": "core-cab-authorization-request.v1",
            "purpose": purpose,
            "actor": {"issuer": case.actor.issuer, "subject": case.actor.subject},
            "requestSha256": digest(payload),
            "sourceContentSha256": command_source.expectedContentSha256,
            "sourceTextSha256": command_source.expectedTextSha256,
        }
    )
    return operation_id, payload, action_id, request


def test_default_workload_channel_is_closed():
    assert not CAB_AUTHORIZE_POLICY.enabled and not CAB_AUTHORIZE_POLICY.is_complete()
    assert not CAB_BRIDGE_SETTINGS.enabled and not CAB_BRIDGE_SETTINGS.is_complete()


def test_actual_grant_binds_original_projection_and_explicit_confirmation(case):
    operation_id, payload, action_id, request = setup_action(case)
    proof = bridge.authorize_action(operation_id, request)
    assert proof["operationSha256"] == digest(payload["operation"])
    assert proof["source"] == payload["operation"]["source"]
    assert payload["operation"]["completion"]["confirmedByProvider"] is True
    assert payload["operation"]["completion"]["status"] == "completed"
    assert (
        "ordered_content" not in payload["operation"]
        and "patientId" not in payload["operation"]
    )
    assert "transcript" not in payload["operation"]
    expiry = datetime.fromisoformat(proof["expiresAt"])
    issued = datetime.fromisoformat(proof["authorizedAt"])
    assert 0 < (expiry - issued).total_seconds() <= 30
    assert (
        bridge.authorize_action(operation_id, request)["requestSha256"]
        == proof["requestSha256"]
    )
    with get_session() as s:
        row = s.get(Action, action_id)
        assert row is not None
        assert row.actor["sessionSha256"] == case.actor.session_sha256
        assert (row.expires_at - row.created_at).total_seconds() <= 120
    bridge.finish_action(action_id)
    with pytest.raises(prepared.BillingPreparationError):
        bridge.authorize_action(operation_id, request)


@pytest.mark.parametrize(
    "change",
    [
        {"requestSha256": "f" * 64},
        {"sourceTextSha256": "f" * 64},
        {"sourceContentSha256": "f" * 64},
        {"purpose": "header.generate"},
        {"actor": {"issuer": ISSUER, "subject": "user_other"}},
    ],
)
def test_mismatched_request_purpose_source_actor_does_not_authorize(case, change):
    operation_id, _, _, request = setup_action(case)
    request = CabAuthorizationRequest.model_validate(request.model_dump() | change)
    with pytest.raises(prepared.BillingPreparationError):
        bridge.authorize_action(operation_id, request)


def test_a_stored_operation_and_workload_identity_are_not_an_action_grant(case):
    cmd = command_for(case)
    prepared.prepare_operation(case.actor, case.note_id, cmd)
    request = CabAuthorizationRequest.model_validate(
        {
            "schemaVersion": "core-cab-authorization-request.v1",
            "purpose": "completed-note.accept",
            "actor": {"issuer": case.actor.issuer, "subject": case.actor.subject},
            "requestSha256": "a" * 64,
            "sourceContentSha256": cmd.expectedContentSha256,
            "sourceTextSha256": cmd.expectedTextSha256,
        }
    )
    with pytest.raises(prepared.BillingPreparationError):
        bridge.authorize_action(UUID(cmd.operationId), request)


def test_current_source_admission_revocation_invalidates_active_action(
    case, monkeypatch
):
    operation_id, _, _, request = setup_action(case)
    monkeypatch.setattr(config, "BILLING_SOURCE_POLICY", config.BillingSourcePolicy())
    with pytest.raises(prepared.BillingPreparationError):
        bridge.authorize_action(operation_id, request)


def test_expired_action_has_no_grace(case, monkeypatch):
    operation_id, _, action_id, request = setup_action(case)
    with get_session() as s:
        stored_action = s.get(Action, action_id)
        assert stored_action is not None
        expires = stored_action.expires_at
    monkeypatch.setattr(bridge, "_now", lambda session: expires)
    with pytest.raises(prepared.BillingPreparationError):
        bridge.authorize_action(operation_id, request)


def test_finish_is_idempotent_and_bindings_cannot_be_rewritten(case, billing_database):
    _, _, action_id, _ = setup_action(case)
    with pytest.raises(Exception, match="immutable"):
        with billing_database.begin() as c:
            c.execute(
                text("UPDATE billing_header_action SET request_sha256 = repeat('f',64)")
            )
    bridge.finish_action(action_id)
    bridge.finish_action(action_id)
    with get_session() as s:
        stored_action_2 = s.get(Action, action_id)
        assert stored_action_2 is not None
        assert stored_action_2.finished_at is not None
    with pytest.raises(Exception, match="immutable"):
        with billing_database.begin() as c:
            c.execute(text("TRUNCATE billing_header_action"))


def test_exact_retry_needs_new_provider_action_but_preserves_remote_request(case):
    operation_id, payload, action_id, request = setup_action(case)
    bridge.finish_action(action_id)
    rotated = VerifiedProviderSession(case.actor.issuer, case.actor.subject, "a" * 64)
    retry, new_id = bridge.begin_action(
        rotated, operation_id, "completed-note.accept", {"expectedSource": None}
    )
    assert retry == payload and new_id != action_id
    assert bridge.authorize_action(operation_id, request)["requestSha256"] == digest(
        payload
    )
    with get_session() as s:
        stored_action_3 = s.get(Action, new_id)
        assert stored_action_3 is not None
        assert stored_action_3.actor["sessionSha256"] == "a" * 64


def test_real_app_only_route_requires_signed_token_role_and_active_provider_grant(case):
    operation_id, _, _, body = setup_action(case)
    tenant, audience, caller = (str(uuid4()) for _ in range(3))
    settings = replace(
        CAB_AUTHORIZE_POLICY,
        settings=replace(
            CAB_AUTHORIZE_POLICY.settings,
            enabled=True,
            tenant_id=tenant,
            audience=audience,
        ),
        allowed_client_ids=frozenset({caller}),
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def decode(token):
        return jwt.decode(
            token,
            key.public_key(),
            algorithms=["RS256"],
            audience=audience,
            issuer=settings.issuer,
            options={"require": ["aud", "exp", "iat", "nbf", "iss", "sub", "tid"]},
        )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[authenticate_cab] = EntraServiceAuthenticator(
        settings, decoder=decode
    )
    client = TestClient(app)
    now = datetime.now(UTC)
    claims = {
        "tid": tenant,
        "aud": audience,
        "iss": settings.issuer,
        "sub": caller,
        "azp": caller,
        "idtyp": "app",
        "roles": ["Core.CabOperation.Authorize"],
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=2),
    }
    path = f"/internal/cab/v1/operations/{operation_id}/authorize"

    def call(changes):
        token = jwt.encode(claims | changes, key, algorithm="RS256")
        return client.post(
            path, json=body.model_dump(), headers={"Authorization": "Bearer " + token}
        )

    ok = call({})
    assert ok.status_code == 200 and ok.headers["cache-control"] == "no-store"
    assert call({"aud": str(uuid4())}).status_code == 401
    assert call({"roles": ["AgentApi.Access"]}).status_code == 403
    assert call({"idtyp": "user"}).status_code == 403
    assert call({"azp": str(uuid4())}).status_code == 403
    assert client.post(path, json=body.model_dump()).status_code == 401
    assert "/internal/cab/v1" not in str(
        __import__(
            "app.utils.openapi", fromlist=["generate_openapi_schema"]
        ).generate_openapi_schema(
            __import__(
                "app.routes.internal", fromlist=["internal_api_app"]
            ).internal_api_app
        )
    )
