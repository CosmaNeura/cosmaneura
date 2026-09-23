"""Real HTTP auth decisions keep Scribe and Sovereign feature calls separate."""

from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.configuration.service_auth import AGENT_API_ACCESS_ROLE, EntraAgentAuthSettings
from app.routes.agent import agent_api_app
from app.routes.dependencies.entra_service import (
    authenticate_agent_api_request,
    authenticate_scribe_capture_request,
    authenticate_sovereign_tool_request,
)

from activemodel.session_manager import aglobal_session

SOVEREIGN = str(uuid4())
SCRIBE = str(uuid4())
FOREIGN = str(uuid4())
TENANT = str(uuid4())
CAPTURE_PATHS = (
    "/service/v1/agent/scribe/capture-bindings/resolve",
    "/service/v1/agent/scribe/capture-admissions",
    "/service/v1/agent/scribe/capture-notes",
)


@pytest.fixture
def client(monkeypatch):
    base = EntraAgentAuthSettings(
        enabled=True,
        tenant_id=TENANT,
        audience=str(uuid4()),
        sovereign_client_ids=frozenset({SOVEREIGN}),
    )
    for authenticator, allowed in (
        (authenticate_sovereign_tool_request, frozenset({SOVEREIGN})),
        (authenticate_scribe_capture_request, frozenset({SCRIBE})),
        (authenticate_agent_api_request, frozenset({SOVEREIGN, SCRIBE})),
    ):
        monkeypatch.setattr(
            authenticator,
            "settings",
            replace(
                base, sovereign_client_ids=allowed, required_client_count=len(allowed)
            ),
        )
        monkeypatch.setattr(
            authenticator,
            "decoder",
            lambda token: {
                "tid": TENANT,
                "azp": token,
                "idtyp": "app",
                "roles": [AGENT_API_ACCESS_ROLE],
            },
        )
    app = FastAPI()
    app.include_router(agent_api_app)
    app.dependency_overrides[aglobal_session] = lambda: None
    return TestClient(app)


@pytest.mark.parametrize("caller", [SCRIBE, FOREIGN])
def test_scribe_cannot_read_session_prep_even_with_agent_role(client, caller):
    response = client.post(
        "/service/v1/agent/tools/invoke",
        headers={"Authorization": f"Bearer {caller}"},
        content=b"private sentinel malformed body",
    )
    assert response.status_code == 403
    assert "private sentinel" not in response.text


@pytest.mark.parametrize("caller", [SOVEREIGN, FOREIGN])
@pytest.mark.parametrize("path", CAPTURE_PATHS)
def test_sovereign_cannot_drive_capture_even_with_agent_role(client, caller, path):
    response = client.post(
        path, headers={"Authorization": f"Bearer {caller}"}, content=b"private sentinel"
    )
    assert response.status_code == 403
    assert "private sentinel" not in response.text


@pytest.mark.parametrize("caller", [SOVEREIGN, SCRIBE])
def test_both_workloads_retain_context_protocol_access(client, caller):
    response = client.post(
        "/service/v1/agent/mount-context/bootstrap",
        headers={"Authorization": f"Bearer {caller}"},
    )
    # With no mount-context header, reaching protocol validation proves the
    # workload gate passed. Real record checks remain in the existing suites.
    assert response.status_code in (400, 422)


def test_missing_capture_receiver_cannot_fall_back_to_sovereign(client, monkeypatch):
    monkeypatch.setattr(
        authenticate_scribe_capture_request,
        "settings",
        replace(
            authenticate_scribe_capture_request.settings,
            sovereign_client_ids=frozenset(),
        ),
    )
    response = client.post(
        CAPTURE_PATHS[0], headers={"Authorization": f"Bearer {SOVEREIGN}"}
    )
    assert response.status_code == 503
