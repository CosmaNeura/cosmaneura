"""Real installed Azure SDK with an in-memory managed-identity transport.

No server, CLI credential, identity endpoint, network or external credential is
used. Only SDK wall-clock inputs advance; its public get_token API owns caching
and refresh. Product time, capture deadlines and token TTL policy are untouched.
"""

import asyncio
import json
from contextvars import ContextVar
from threading import get_ident
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from azure.core.pipeline.transport import (
    AsyncHttpResponse,
    AsyncHttpTransport,
    HttpRequest,
)
from azure.identity._internal import managed_identity_client as sync_client
from azure.identity.aio import DefaultAzureCredential
from azure.identity.aio._internal import get_token_mixin
from azure.identity.aio._internal import managed_identity_client as async_client

from app.configuration.scribe_service_identity import ScribeServiceIdentityOwner
from app.configuration.scribe_workflow import ScribeWorkflowSettings

from tests.authority_kernel.scribe_service_identity_test import Budget, until

SETTINGS = ScribeWorkflowSettings(
    enabled=True,
    base_url="https://scribe.internal.example",
    scope="synthetic/.default",
    managed_identity_client_id="11111111-1111-4111-8111-111111111111",
    timeout_seconds=5,
    identity_reuse_enabled=True,
)


class Response(AsyncHttpResponse):
    def __init__(self, request: HttpRequest, token: str, now: int):
        super().__init__(request, None)
        self.status_code = 200
        self.headers = {"Content-Type": "application/json"}
        self.content_type = "application/json"
        self._body = json.dumps(
            {
                "access_token": token,
                "expires_on": now + 3600,
                "expires_in": 3600,
                "resource": "synthetic",
                "token_type": "Bearer",
            }
        ).encode()

    def body(self) -> bytes:
        return self._body


class Transport(AsyncHttpTransport):
    def __init__(self, clock, private):
        self.clock, self.private = clock, private
        self.loop, self.thread = asyncio.get_running_loop(), get_ident()
        self.requests, self.closed = 0, 0

    async def open(self) -> None:
        assert asyncio.get_running_loop() is self.loop
        assert get_ident() == self.thread and self.closed == 0

    async def send(self, request: HttpRequest, **kwargs: Any) -> AsyncHttpResponse:
        await self.open()
        assert self.private.get() is None
        parsed = urlsplit(request.url)
        assert (
            parsed.scheme == "https" and parsed.hostname == "synthetic.identity.invalid"
        )
        assert parsed.path == "/token"
        query = parse_qs(parsed.query)
        assert query["resource"] == ["synthetic"]
        assert query["client_id"] == [SETTINGS.managed_identity_client_id]
        self.requests += 1
        return Response(
            request, "synthetic-sdk-token-" + str(self.requests), self.clock[0]
        )

    async def close(self) -> None:
        assert asyncio.get_running_loop() is self.loop
        self.closed += 1

    async def __aenter__(self):
        await self.open()
        return self

    async def __aexit__(self, *args):
        await self.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("warm_before_request", [False, True])
async def test_official_sdk_cache_then_refresh_across_private_worker_loops(
    monkeypatch, warm_before_request
):
    now = [2_000_000_000]
    clock = SimpleNamespace(time=lambda: now[0])
    # Replace module clock references, not global time or SDK cache structures.
    for module in (sync_client, async_client, get_token_mixin):
        monkeypatch.setattr(module, "time", clock)
    monkeypatch.setenv("IDENTITY_ENDPOINT", "https://synthetic.identity.invalid/token")
    monkeypatch.setenv("IDENTITY_HEADER", "synthetic-only-header")
    private = ContextVar[str | None]("sdk_synthetic_private_patient", default=None)
    transport = Transport(now, private)
    credentials = []

    def factory(client_id):
        # Public DAC options force this test to the fake managed-identity
        # transport. Production SDK chain options are not changed by the owner.
        credential = DefaultAzureCredential(
            managed_identity_client_id=client_id,
            transport=transport,
            retry_total=0,
            exclude_environment_credential=True,
            exclude_workload_identity_credential=True,
            exclude_shared_token_cache_credential=True,
            exclude_visual_studio_code_credential=True,
            exclude_cli_credential=True,
            exclude_powershell_credential=True,
            exclude_developer_cli_credential=True,
        )
        credentials.append(credential)
        return credential

    owner = ScribeServiceIdentityOwner(SETTINGS, credential_factory=factory)
    assert len(credentials) == 1 and transport.requests == 0
    loops = []

    def worker():
        async def request():
            loops.append(asyncio.get_running_loop())
            private.set("synthetic-request-only-patient-context")
            borrower = owner.borrow(SETTINGS, Budget())
            try:
                return await borrower.get_token(SETTINGS.scope)
            finally:
                await borrower.close()

        return asyncio.run(request())

    try:
        if warm_before_request:
            private.set("synthetic-catalog-result-and-provider-context")
            assert owner.request_warm() is None
            await until(lambda: owner._warm is None)
            assert transport.requests == 1 and len(credentials) == 1
        first = await asyncio.to_thread(worker)
        assert first.token == "synthetic-sdk-token-1" and transport.requests == 1
        second = await asyncio.to_thread(worker)
        assert second == first and transport.requests == 1
        assert (
            loops[0].is_closed() and loops[1].is_closed() and loops[0] is not loops[1]
        )
        now[0] += 3601
        refreshed = await asyncio.to_thread(worker)
        assert refreshed.token == "synthetic-sdk-token-2" and transport.requests == 2
        assert refreshed.expires_on > first.expires_on
        assert transport.closed == 0 and not owner._operations
    finally:
        await owner.close()
    assert transport.closed == 1
