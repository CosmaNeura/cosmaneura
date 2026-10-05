"""Companion capture admission does not grant Sovereign tool access."""

import runpy
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.configuration import service_auth
from app.routes.dependencies.entra_service import (
    authenticate_agent_api_request,
    authenticate_scribe_capture_request,
)

from tests.authority_kernel.workload_feature_boundaries_test import (
    CAPTURE_PATHS,
    SCRIBE,
    SOVEREIGN,
)
from tests.authority_kernel.workload_feature_boundaries_test import (
    client as workload_client,  # noqa: F401 -- reuse the existing HTTP auth fixture
)

COMPANION = str(uuid4())


def configured(monkeypatch, *, scribe=SCRIBE, companion=COMPANION):
    monkeypatch.setenv("AGENT_API_ENABLED", "true")
    monkeypatch.setenv("ENTRA_TENANT_ID", str(uuid4()))
    monkeypatch.setenv("AGENT_API_AUDIENCE", str(uuid4()))
    monkeypatch.setenv("AGENT_API_SOVEREIGN_CLIENT_IDS", SOVEREIGN)
    monkeypatch.setenv("AGENT_API_SCRIBE_RECEIVER_CLIENT_ID", scribe)
    monkeypatch.setenv("AGENT_API_COMPANION_CLIENT_ID", companion)
    return runpy.run_path(str(Path(service_auth.__file__)))


@pytest.mark.parametrize("scribe", ["", SCRIBE])
def test_companion_configuration_preserves_separate_feature_callers(
    monkeypatch, scribe
):
    values = configured(monkeypatch, scribe=scribe)
    expected_capture = frozenset({COMPANION, *([scribe] if scribe else [])})
    capture = values["SCRIBE_CAPTURE_AUTH_SETTINGS"]
    shared = values["AGENT_API_AUTH_SETTINGS"]
    sovereign = values["SOVEREIGN_TOOL_AUTH_SETTINGS"]
    assert capture.is_complete() and shared.is_complete() and sovereign.is_complete()
    assert capture.allowed_client_ids == expected_capture
    assert shared.allowed_client_ids == expected_capture | {SOVEREIGN}
    assert sovereign.allowed_client_ids == frozenset({SOVEREIGN})


def test_unconfigured_companion_preserves_existing_scribe_policy(monkeypatch):
    values = configured(monkeypatch, companion="")
    assert values["SCRIBE_CAPTURE_AUTH_SETTINGS"].allowed_client_ids == frozenset(
        {SCRIBE}
    )
    assert values["AGENT_API_AUTH_SETTINGS"].required_client_count == 2


@pytest.mark.parametrize(
    "companion",
    [SOVEREIGN, SCRIBE, "bad", str(uuid4()) + "," + SCRIBE, str(UUID(int=0))],
)
def test_companion_cannot_alias_an_existing_caller_or_broaden_its_allowlist(
    monkeypatch, companion
):
    with pytest.raises(ValueError, match="agent_api_companion_configuration_invalid"):
        configured(monkeypatch, companion=companion)


@pytest.mark.parametrize("path", CAPTURE_PATHS)
def test_companion_requires_explicit_capture_admission(
    workload_client, monkeypatch, path
):  # noqa: F811 -- imported pytest fixture
    headers = {"Authorization": f"Bearer {COMPANION}"}
    response = workload_client.post(path, headers=headers, content=b"private sentinel")
    assert response.status_code == 403
    for authenticator in (
        authenticate_scribe_capture_request,
        authenticate_agent_api_request,
    ):
        old = authenticator.settings
        allowed = old.allowed_client_ids | {COMPANION}
        monkeypatch.setattr(
            authenticator,
            "settings",
            replace(
                old, sovereign_client_ids=allowed, required_client_count=len(allowed)
            ),
        )
    # Disabled capture reaches its own 503 gate only after workload admission.
    accepted = workload_client.post(path, headers=headers, content=b"private sentinel")
    assert accepted.status_code == 503
    denied = workload_client.post(
        "/service/v1/agent/tools/invoke", headers=headers, content=b"private sentinel"
    )
    assert denied.status_code == 403
    assert "private sentinel" not in accepted.text + denied.text
