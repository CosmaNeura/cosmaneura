"""Production registration with independent transport/identity capabilities."""

import asyncio
import runpy
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from app.configuration import scribe_service_identity as identity
from app.services import scribe_http_pool as connections

from tests.authority_kernel.scribe_service_identity_route_test import (
    SETTINGS,
    Credential,
    install_from_server,
    until,
)

CONFIG = Path(identity.__file__).with_name("scribe_workflow.py")


@pytest.mark.parametrize(
    "value,expected", [(None, False), ("false", False), ("true", True), ("1", True)]
)
def test_exact_pool_environment_is_default_off_and_independent(
    monkeypatch, value, expected
):
    # Run the real configuration module against only synthetic settings;
    # do not reload its process-global object used by other tests.
    for name, setting in {
        "SCRIBE_WORKFLOW_ENABLED": "true",
        "SCRIBE_SERVICE_BASE_URL": SETTINGS.base_url,
        "SCRIBE_SERVICE_SCOPE": SETTINGS.scope,
        "SCRIBE_MANAGED_IDENTITY_CLIENT_ID": SETTINGS.managed_identity_client_id,
        "SCRIBE_SERVICE_TIMEOUT_SECONDS": "5",
        "SCRIBE_WORKFLOW_IDENTITY_REUSE_ENABLED": "true",
    }.items():
        monkeypatch.setenv(name, setting)
    if value is None:
        monkeypatch.delenv("SCRIBE_WORKFLOW_HTTP_POOL_ENABLED", raising=False)
    else:
        monkeypatch.setenv("SCRIBE_WORKFLOW_HTTP_POOL_ENABLED", value)
    settings = runpy.run_path(str(CONFIG))["SCRIBE_WORKFLOW_SETTINGS"]
    assert settings.http_pool_enabled is expected
    assert settings.identity_reuse_enabled is True
    assert settings.is_complete() and settings.enabled


def test_malformed_pool_capability_fails_configuration(monkeypatch):
    monkeypatch.setenv("SCRIBE_WORKFLOW_HTTP_POOL_ENABLED", "not-a-boolean")
    with pytest.raises(ValueError):
        runpy.run_path(str(CONFIG))


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow_enabled", [False, True])
@pytest.mark.parametrize("pool_enabled", [False, True])
@pytest.mark.parametrize("identity_enabled", [False, True])
async def test_actual_registration_keeps_pool_identity_and_warmup_independent(
    monkeypatch, workflow_enabled, pool_enabled, identity_enabled
):
    settings = replace(
        SETTINGS,
        enabled=workflow_enabled,
        http_pool_enabled=pool_enabled,
        identity_reuse_enabled=identity_enabled,
    )
    pools, credentials = [], []
    original_pool = connections.ScribeHttpPool
    original_owner = identity.ScribeServiceIdentityOwner

    def create_pool(origin):
        transport = httpx.MockTransport(
            lambda _: pytest.fail("registration or service warmup reached HTTP")
        )
        pool = original_pool(origin, transport=transport)
        pools.append(pool)
        return pool

    class InstrumentedOwner(original_owner):
        def __init__(self, configuration):
            credential = Credential()
            credentials.append(credential)
            super().__init__(configuration, credential_factory=lambda _: credential)

    monkeypatch.setattr(connections, "ScribeHttpPool", create_pool)
    monkeypatch.setattr(identity, "ScribeServiceIdentityOwner", InstrumentedOwner)
    app = FastAPI()
    install_from_server(app, settings)
    async with app.router.lifespan_context(app):
        assert len(pools) == int(workflow_enabled and pool_enabled)
        assert len(credentials) == int(workflow_enabled and identity_enabled)
        assert (app.state.scribe_http_pool is not None) is bool(pools)
        assert (app.state.scribe_service_identity is not None) is bool(credentials)
        assert all(credential.calls == 0 for credential in credentials)
        # The existing service-only warm hook requires no HTTP pool and never
        # creates a fallback when the independent identity owner is absent.
        identity.request_scribe_service_identity_warm(app)
        if credentials:
            await until(lambda: app.state.scribe_service_identity._warm is None)
            assert credentials[0].calls == 1 and credentials[0].closed == 0
        else:
            await asyncio.sleep(0)
    assert all(pool._closed for pool in pools)
    assert all(credential.closed == 1 for credential in credentials)


@pytest.mark.asyncio
async def test_pool_opt_in_does_not_enable_incomplete_workflow(monkeypatch):
    monkeypatch.setattr(
        connections, "ScribeHttpPool", lambda _: pytest.fail("incomplete pool")
    )
    app = FastAPI()
    install_from_server(
        app,
        replace(
            SETTINGS, base_url="", http_pool_enabled=True, identity_reuse_enabled=False
        ),
    )
    async with app.router.lifespan_context(app):
        assert app.state.scribe_http_pool is None
        assert app.state.scribe_service_identity is None
