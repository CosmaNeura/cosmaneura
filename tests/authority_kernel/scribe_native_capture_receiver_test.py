"""Real HTTP/cancellation boundary; invented credentials and service receipts."""

import asyncio
import json
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from starlette.routing import Route

from app.configuration.scribe_capture import ScribeCaptureSettings
from app.configuration.scribe_encounters import ScribeEncounterSettings
from app.routes import scribe_capture_deadline as boundary
from app.routes import scribe_native_capture as route
from app.routes.dependencies.entra_service import EntraServicePrincipal
from app.routes.embed_context import (
    VerifiedClerkIdentity,
    authenticate_embed_platform_session,
)
from app.services.scribe_workflow import DisabledScribeWorkflowClient

PATH = "/service/v1/agent" + route.PATH
ACTOR = "authority_actor_00000000000000000000000001"
TENANT = "authority_tenant_00000000000000000000000002"
WORKLOAD = "11111111-1111-4111-8111-111111111111"


def body(**changes) -> dict[str, object]:
    values: dict[str, object] = dict(
        action="mount",
        workspace_id=str(uuid4()),
        request_nonce=str(uuid4()),
        expected_actor_ref=ACTOR,
        expected_tenant_ref=TENANT,
    )
    return values | changes


def headers():
    return {
        "Authorization": "Bearer invented-clerk-token",
        "X-Cosmaneura-Workload-Authorization": "Bearer invented-workload-token",
        "Content-Type": "application/json",
        "X-Cosmaneura-Native-Capture-Version": "1",
        "Idempotency-Key": str(uuid4()),
    }


def client(monkeypatch, *, enabled=True):
    app = FastAPI()
    app.include_router(route.native_capture_api_app)
    app.dependency_overrides[authenticate_embed_platform_session] = (
        lambda: VerifiedClerkIdentity(
            issuer="https://test.clerk.invalid",
            subject="user_test_provider",
            clerk_session_id="session_test",
        )
    )
    app.dependency_overrides[route.get_capture_settings] = (
        lambda: ScribeCaptureSettings(enabled=enabled)
    )
    app.dependency_overrides[route.encounter_settings] = (
        lambda: ScribeEncounterSettings(enabled=enabled)
    )
    app.dependency_overrides[route.get_admission_client_factory] = (
        lambda: DisabledScribeWorkflowClient
    )
    calls = []

    def authenticate(label):
        def checked(credentials):
            assert credentials.credentials == "invented-workload-token"
            calls.append(label)
            return EntraServicePrincipal(WORKLOAD)

        return checked

    monkeypatch.setattr(route, "authenticate_agent_api_request", authenticate("agent"))
    monkeypatch.setattr(
        route, "authenticate_scribe_capture_request", authenticate("capture")
    )

    async def execute(**kwargs):
        calls.append(kwargs)
        return {"version": "1", "action": kwargs["data"].action}

    monkeypatch.setattr(route, "execute_native_capture", execute)
    return TestClient(app), calls


def test_both_existing_policies_and_verified_clerk_identity_reach_service(monkeypatch):
    connection, calls = client(monkeypatch)
    result = connection.post(PATH, json=body(), headers=headers())
    assert result.status_code == 200
    assert calls[:2] == ["agent", "capture"]
    assert calls[2]["subject"] == "user_test_provider"
    assert calls[2]["clerk_session_id"] == "session_test"
    assert calls[2]["workload_client_id"] == WORKLOAD
    assert result.headers["cache-control"] == "no-store"
    assert "token" not in result.text


@pytest.mark.parametrize("denied", ["agent", "capture", "mismatch"])
def test_workload_policies_deny_before_body_or_service(monkeypatch, denied):
    connection, calls = client(monkeypatch)

    def reject(_credentials):
        if denied == "mismatch":
            return EntraServicePrincipal(str(uuid4()))
        raise HTTPException(403, "denied")

    monkeypatch.setattr(
        route,
        "authenticate_"
        + ("agent_api_request" if denied == "agent" else "scribe_capture_request"),
        reject,
    )
    result = connection.post(PATH, content=b'{"private_sentinel":', headers=headers())
    assert result.status_code == 403
    assert all(isinstance(call, str) for call in calls)
    assert "private_sentinel" not in result.text


@pytest.mark.parametrize(
    "change",
    ["duplicate_workload", "missing_workload", "malformed_workload", "duplicate_human"],
)
def test_closed_credential_headers(monkeypatch, change):
    connection, calls = client(monkeypatch)
    h = headers()
    if change == "missing_workload":
        del h["X-Cosmaneura-Workload-Authorization"]
    if change == "malformed_workload":
        h["X-Cosmaneura-Workload-Authorization"] = "Basic private_sentinel"
    h = list(h.items())
    if change == "duplicate_workload":
        h.append(
            ("X-Cosmaneura-Workload-Authorization", "Bearer invented-workload-token")
        )
    if change == "duplicate_human":
        h.append(("Authorization", "Bearer different-human"))
    assert connection.post(PATH, json=body(), headers=h).status_code == 401
    assert calls == []


@pytest.mark.parametrize(
    "change",
    [
        "version",
        "query",
        "cookie",
        "context",
        "type",
        "oversize",
        "duplicate_key",
        "bad_json",
        "authority",
        "operation",
        "missing_binding",
        "duplicate_length",
    ],
)
def test_closed_transport_and_body(monkeypatch, change):
    connection, calls = client(monkeypatch)
    data, h, path = body(), headers(), PATH
    if change == "version":
        h["X-Cosmaneura-Native-Capture-Version"] = "2"
    if change == "query":
        path += "?private_sentinel=true"
    if change == "cookie":
        h["Cookie"] = "session=private_sentinel"
    if change == "context":
        h["X-Cosmaneura-Mount-Context"] = "private_sentinel"
    if change == "type":
        h["Content-Type"] = "text/plain"
    if change == "authority":
        data["creator_actor_id"] = "private_sentinel"
    if change == "operation":
        data["operation"] = {"kind": "capabilities"}
    if change == "missing_binding":
        data.update(action="operation", operation={"kind": "capabilities"})
    content = json.dumps(data)
    if change == "oversize":
        content = "x" * (route.MAX_BODY + 1)
    if change == "duplicate_key":
        content = content[:-1] + ',"action":"operation"}'
    if change == "bad_json":
        content = '{"private_sentinel":'
    h = list(h.items())
    if change == "duplicate_length":
        h.extend(
            [
                ("Content-Length", str(len(content))),
                ("Content-Length", str(len(content))),
            ]
        )
    result = connection.post(path, content=content, headers=h)
    assert result.status_code == 422
    assert calls == ["agent", "capture"]
    assert "private_sentinel" not in result.text


def test_view_as_and_disabled_cohort_cannot_reach_service(monkeypatch):
    connection, calls = client(monkeypatch, enabled=False)
    assert (
        connection.post(
            PATH, content=b"private_sentinel", headers=headers()
        ).status_code
        == 503
    )
    assert calls == ["agent", "capture"]
    connection, calls = client(monkeypatch)
    monkeypatch.setattr(route, "view_as_browser_session_present", lambda _: True)
    assert connection.post(PATH, json=body(), headers=headers()).status_code == 403
    assert calls == ["agent", "capture"]


def test_new_route_is_inside_existing_owned_deadline(monkeypatch):
    connection, _ = client(monkeypatch)
    assert PATH in boundary.CAPTURE_PATHS
    monkeypatch.setattr(boundary, "CAPTURE_REQUEST_SECONDS", 0.08)
    finished = []

    async def stalled(**kwargs):
        try:
            await asyncio.sleep(30)
        finally:
            finished.append(True)

    monkeypatch.setattr(route, "execute_native_capture", stalled)
    result = connection.post(PATH, json=body(), headers=headers())
    assert result.status_code == 504
    assert finished == [True]


def test_clerk_authenticator_keeps_primary_header(monkeypatch):
    # Regression for the SDK's use of request.headers rather than the separately
    # parsed credentials argument. Workload parsing must not alter the Request.
    raw = [(k.lower().encode(), v.encode()) for k, v in headers().items()]
    request = Request({"type": "http", "headers": raw})
    monkeypatch.setattr(
        route,
        "authenticate_agent_api_request",
        lambda _: EntraServicePrincipal(WORKLOAD),
    )
    monkeypatch.setattr(
        route,
        "authenticate_scribe_capture_request",
        lambda _: EntraServicePrincipal(WORKLOAD),
    )
    assert route.authenticate_native_workload(request).client_id == WORKLOAD
    assert request.headers["authorization"] == "Bearer invented-clerk-token"


@pytest.mark.parametrize(
    "operation",
    [
        {"kind": "issue_lease", "payload": {}},
        {"kind": "encryption_key"},
        {"kind": "complete", "payload": {}},
    ],
)
def test_mutation_and_key_operations_are_not_composed(monkeypatch, operation):
    connection, calls = client(monkeypatch)
    result = connection.post(
        PATH,
        json=body(
            action="operation",
            binding_ref="scribe_capture_binding_00000000000000000000000003",
            operation=operation,
        ),
        headers=headers(),
    )
    assert result.status_code == 422
    assert calls == ["agent", "capture"]


@pytest.mark.parametrize(
    "alter, expected",
    [
        ({}, 200),
        ({"tid": str(uuid4())}, 401),
        ({"azp": str(uuid4())}, 403),
        ({"idtyp": "user"}, 403),
        ({"roles": ["Agent.API.Access"]}, 403),
        ({"roles": ["Scribe.Capture.Access"]}, 403),
    ],
)
def test_composition_enforces_both_real_workload_policies(monkeypatch, alter, expected):
    from app.configuration.service_auth import (
        EntraServiceAuthPolicy,
        EntraServiceAuthSettings,
    )
    from app.routes.dependencies.entra_service import EntraServiceAuthenticator

    connection, calls = client(monkeypatch)
    tenant, audience = str(uuid4()), str(uuid4())
    claims = {
        "tid": tenant,
        "azp": WORKLOAD,
        "idtyp": "app",
        "roles": ["Agent.API.Access", "Scribe.Capture.Access"],
    } | alter
    for name, role in (
        ("authenticate_agent_api_request", "Agent.API.Access"),
        ("authenticate_scribe_capture_request", "Scribe.Capture.Access"),
    ):
        policy = EntraServiceAuthPolicy(
            settings=EntraServiceAuthSettings(
                enabled=True, tenant_id=tenant, audience=audience
            ),
            allowed_client_ids=frozenset({WORKLOAD}),
            required_role=role,
        )
        monkeypatch.setattr(
            route, name, EntraServiceAuthenticator(policy, decoder=lambda _: claims)
        )
    assert connection.post(PATH, json=body(), headers=headers()).status_code == expected
    assert len(calls) == (1 if expected == 200 else 0)


def test_actual_server_registers_facade_without_request_wide_session():
    from fastapi.routing import iter_route_contexts

    from app.server import api_app

    from activemodel.session_manager import aglobal_session

    routes = [
        context
        for context in iter_route_contexts(api_app.routes)
        if context.path == PATH
    ]
    assert len(routes) == 1
    assert isinstance(routes[0].route, Route)
    assert routes[0].route.endpoint is route.native_capture
    assert all(
        item.call is not aglobal_session for item in routes[0].dependant.dependencies
    )
