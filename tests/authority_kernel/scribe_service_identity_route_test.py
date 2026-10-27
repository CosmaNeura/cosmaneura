"""Actual capture/factory boundaries with a synthetic lifespan SDK and HTTP."""

import ast
import asyncio
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from threading import BoundedSemaphore, get_ident
from typing import Any
from uuid import uuid4

import httpx
import pytest
from azure.core.credentials import AccessToken
from fastapi import APIRouter, FastAPI, Request
from pydantic import BaseModel

from app.configuration import scribe_service_identity as identity
from app.configuration.scribe_workflow import ScribeWorkflowSettings
from app.routes import scribe_capture_deadline as boundary
from app.services import scribe_http_pool as connections
from app.services import scribe_workflow as workflow

ORIGIN = "https://scribe.internal.example"
PATH = "/synthetic-capture"
SETTINGS = ScribeWorkflowSettings(
    enabled=True,
    base_url=ORIGIN,
    scope="synthetic/.default",
    managed_identity_client_id="11111111-1111-4111-8111-111111111111",
    timeout_seconds=5,
    identity_reuse_enabled=True,
)


class Receipt(BaseModel):
    ok: bool


class Credential:
    def __init__(self, handler: Callable[[], Awaitable[AccessToken]] | None = None):
        self.calls, self.closed = 0, 0
        self.handler = handler
        self.loop, self.thread = asyncio.get_running_loop(), get_ident()

    async def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        assert asyncio.get_running_loop() is self.loop
        assert get_ident() == self.thread
        assert scopes == (SETTINGS.scope,) and not kwargs
        self.calls += 1
        if self.handler:
            return await self.handler()
        return AccessToken("synthetic-service-identity", int(time.time()) + 60)

    async def close(self):
        assert asyncio.get_running_loop() is self.loop
        self.closed += 1


async def until(predicate):
    expires = time.monotonic() + 2
    while not predicate():
        assert time.monotonic() < expires
        await asyncio.sleep(0.001)


def install_from_server(app, settings):
    # Exercise the actual production registration expressions without importing
    # app.server's external service/bootstrap graph.
    source = Path(connections.__file__).parents[1] / "server.py"
    tree = ast.parse(source.read_text())
    names = {"install_scribe_connections", "install_scribe_service_identity"}
    calls: list[ast.stmt] = [
        node
        for node in tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id in names
    ]
    assert len(calls) == 2
    exec(
        compile(ast.Module(body=calls, type_ignores=[]), str(source), "exec"),
        {
            "api_app": app,
            "SCRIBE_WORKFLOW_SETTINGS": settings,
            "install_scribe_connections": connections.install_scribe_connections,
            "install_scribe_service_identity": identity.install_scribe_service_identity,
        },
    )


def application(
    monkeypatch, sdk, downstream, *, outer=False, seconds: float = 1, pool_enabled=True
):
    monkeypatch.setattr(boundary, "CAPTURE_REQUEST_SECONDS", seconds)
    original_pool = connections.ScribeHttpPool
    original_owner = identity.ScribeServiceIdentityOwner
    monkeypatch.setattr(
        connections,
        "ScribeHttpPool",
        lambda origin: original_pool(origin, transport=httpx.MockTransport(downstream)),
    )
    monkeypatch.setattr(
        identity,
        "ScribeServiceIdentityOwner",
        lambda settings: original_owner(settings, credential_factory=lambda _: sdk),
    )
    original_client = httpx.AsyncClient

    def client(*args, **kwargs):
        if kwargs.get("base_url") == ORIGIN and kwargs.get("transport") is None:
            # Replace only the worker-owned client's network with local HTTP.
            assert kwargs["trust_env"] is False
            assert kwargs["follow_redirects"] is False
            kwargs["transport"] = httpx.MockTransport(downstream)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    # Any missing borrower would attempt this legacy factory and fail the test.
    monkeypatch.setattr(
        workflow,
        "create_scribe_workload_credential",
        lambda _: pytest.fail("request created a second SDK credential"),
    )
    app = FastAPI()
    router = APIRouter(route_class=boundary.CaptureDeadlineRoute)
    contexts = ContextVar[str | None]("synthetic_patient_scope", default=None)
    parent_thread = get_ident()
    private_loops = []
    settings = replace(SETTINGS, http_pool_enabled=pool_enabled)

    @router.post(PATH)
    async def operation(request: Request):
        assert get_ident() != parent_thread
        assert contexts.get() is None
        contexts.set("synthetic-patient-claims-and-sql")
        private_loops.append(asyncio.get_running_loop())
        await request.body()
        client = workflow.build_scribe_workflow_client(settings)
        assert isinstance(client, workflow.HttpScribeWorkflowClient)
        assert client._uses_response_collection is pool_enabled
        tenant = uuid4()
        result = Receipt(ok=False)
        try:
            # Consent and session remain distinct serial operations, with a
            # get_token call for each rather than a cached domain decision.
            for path in ("/v1/consent", "/v1/session"):
                result = await client._post(
                    path, tenant, Receipt(ok=True), "synthetic-key", Receipt
                )
            return result.model_dump()
        finally:
            await client.close()

    app.include_router(router)
    if outer:
        app.add_middleware(boundary.CaptureBoundary, paths=frozenset({PATH}))
    install_from_server(app, settings)
    return app, contexts, private_loops


@pytest.mark.asyncio
@pytest.mark.parametrize("outer", [False, True])
@pytest.mark.parametrize("pool_enabled", [False, True])
async def test_actual_routes_borrow_sdk_but_keep_each_workflow_request_fresh(
    monkeypatch, outer, pool_enabled
):
    requests = []
    parent_loop = asyncio.get_running_loop()

    async def downstream(request: httpx.Request) -> httpx.Response:
        if pool_enabled:
            assert asyncio.get_running_loop() is parent_loop
            assert contexts.get() is None
        else:
            assert asyncio.get_running_loop() in loops
            assert contexts.get() == "synthetic-patient-claims-and-sql"
        requests.append(request)
        return httpx.Response(
            201, headers={"content-type": "application/json"}, content=b'{"ok":true}'
        )

    async def token() -> AccessToken:
        assert contexts.get() is None
        return AccessToken("synthetic-service-identity", int(time.time()) + 60)

    sdk = Credential(token)
    app, contexts, loops = application(
        monkeypatch, sdk, downstream, outer=outer, pool_enabled=pool_enabled
    )
    contexts.set("parent-private-context")
    async with app.router.lifespan_context(app):
        assert sdk.calls == 0
        owner = app.state.scribe_service_identity
        assert (app.state.scribe_http_pool is not None) is pool_enabled
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            results = await asyncio.gather(client.post(PATH), client.post(PATH))
            assert all(
                r.status_code == 200 and r.json() == {"ok": True} for r in results
            )
        assert sdk.calls == 4 and sdk.closed == 0
        assert len(set(loops)) == 2 and parent_loop not in loops
        assert len(requests) == 4
        assert {r.url.path for r in requests} == {"/v1/consent", "/v1/session"}
        assert len({r.headers["x-tenant-id"] for r in requests}) == 2
        assert all(
            r.headers["authorization"] == "Bearer synthetic-service-identity"
            for r in requests
        )
        assert not owner._operations and not owner._closed
    assert sdk.closed == 1 and owner._closed


@pytest.mark.asyncio
@pytest.mark.parametrize("outer", [False, True])
@pytest.mark.parametrize("pool_enabled", [False, True])
async def test_expired_route_waits_owned_token_cleanup_and_never_sends_http(
    monkeypatch, outer, pool_enabled
):
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def token() -> AccessToken:
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            cleanup.set()
            await release.wait()
        raise AssertionError("synthetic token unexpectedly continued")

    sdk = Credential(token)
    requests = []

    def downstream(request):
        requests.append(request)
        pytest.fail("expired SDK acquisition reached workflow HTTP")

    slots = BoundedSemaphore(1)
    monkeypatch.setattr(boundary, "_workers", slots)
    app, _, _ = application(
        monkeypatch,
        sdk,
        downstream,
        outer=outer,
        # Exercise in-flight token cancellation, not a race with SDK entry.
        seconds=1,
        pool_enabled=pool_enabled,
    )
    async with app.router.lifespan_context(app):
        owner = app.state.scribe_service_identity
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            try:
                first = await client.post(PATH)
                assert first.status_code == 504 and entered.is_set()
                await until(cleanup.is_set)
                assert sdk.closed == 0 and len(owner._operations) == 1
                second = await client.post(PATH)
                assert second.status_code == 503 and sdk.calls == 1
                assert not slots.acquire(blocking=False)
            finally:
                # A failed admission/cleanup assertion must not hang lifespan
                # shutdown on the deliberately resistant synthetic token.
                release.set()
            await until(lambda: not owner._operations)
            # The operation join precedes request-client/private-loop cleanup.
            expires = time.monotonic() + 2
            while not slots.acquire(blocking=False):
                assert time.monotonic() < expires
                await asyncio.sleep(0.001)
            slots.release()
        assert not requests
    assert sdk.closed == 1


@pytest.mark.asyncio
async def test_wrong_identity_lifespan_loop_fails_before_worker_and_releases_slot(
    monkeypatch,
):
    slots = BoundedSemaphore(1)
    monkeypatch.setattr(boundary, "_workers", slots)
    app = FastAPI()
    router = APIRouter(route_class=boundary.CaptureDeadlineRoute)

    @router.post(PATH)
    async def never_reached():
        pytest.fail("foreign identity owner reached capture worker")

    app.include_router(router)

    async def foreign():
        owner = identity.ScribeServiceIdentityOwner(
            SETTINGS, credential_factory=lambda _: Credential()
        )
        await owner.close()
        return owner

    app.state.scribe_service_identity = await asyncio.to_thread(
        lambda: asyncio.run(foreign())
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        for _ in range(2):
            result = await client.post(PATH)
            assert result.status_code == 503
            assert slots.acquire(blocking=False)
            slots.release()


@pytest.mark.asyncio
async def test_owned_sdk_failure_keeps_existing_workflow_error_and_no_http(monkeypatch):
    async def token():
        raise ValueError("synthetic-private-sdk-exception")

    sdk = Credential(token)
    owner = identity.ScribeServiceIdentityOwner(
        SETTINGS, credential_factory=lambda _: sdk
    )
    calls = []
    original = connections.ScribeHttpPool
    pool = original(
        ORIGIN,
        transport=httpx.MockTransport(
            lambda request: calls.append(request) or httpx.Response(500)
        ),
    )

    def worker():
        async def run():
            budget = boundary.CaptureBudget(5)
            with (
                boundary.capture_budget(budget),
                connections.capture_scribe_connections(pool, budget),
                identity.capture_scribe_service_identity(owner, budget),
            ):
                client = workflow.build_scribe_workflow_client(SETTINGS)
                assert isinstance(client, workflow.HttpScribeWorkflowClient)
                try:
                    with pytest.raises(
                        workflow.ScribeWorkflowRetryableError
                    ) as failure:
                        await client._post(
                            "/v1/fixed",
                            uuid4(),
                            Receipt(ok=True),
                            "synthetic-key",
                            Receipt,
                        )
                    assert failure.value.code == "scribe_identity_unavailable"
                    assert "synthetic-private" not in str(failure.value)
                finally:
                    await client.close()

        asyncio.run(run())

    await asyncio.to_thread(worker)
    assert sdk.calls == 1 and not calls and sdk.closed == 0
    await owner.close()
    await pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [False, True])
async def test_legacy_absent_owner_and_explicit_factory_keep_request_ownership(
    monkeypatch, override
):
    sdk = Credential()
    owner = identity.ScribeServiceIdentityOwner(
        SETTINGS, credential_factory=lambda _: sdk
    )

    def worker():
        async def run():
            budget = boundary.CaptureBudget(5)
            with (
                boundary.capture_budget(budget),
                identity.capture_scribe_service_identity(
                    owner if override else None, budget
                ),
            ):
                credential = Credential()
                monkeypatch.setattr(
                    workflow, "create_scribe_workload_credential", lambda _: credential
                )
                client = workflow.build_scribe_workflow_client(
                    replace(SETTINGS, identity_reuse_enabled=False),
                    credential_factory=(lambda _: credential) if override else None,
                )
                assert isinstance(client, workflow.HttpScribeWorkflowClient)
                assert client._credential is credential
                await credential.get_token(SETTINGS.scope)
                await client.close()
                assert credential.calls == 1 and credential.closed == 1

        asyncio.run(run())

    await asyncio.to_thread(worker)
    assert sdk.calls == 0 and sdk.closed == 0
    await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("outer", [False, True])
@pytest.mark.parametrize("pool_enabled", [False, True])
async def test_closing_lifespan_rejects_late_routes_until_fresh_lifespan(
    monkeypatch, outer, pool_enabled
):
    entered, cleanup = asyncio.Event(), asyncio.Event()
    release_cleanup = asyncio.Event()
    close_entered, release_close = asyncio.Event(), asyncio.Event()
    close_finished = asyncio.Event()

    async def token() -> AccessToken:
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            cleanup.set()
            await release_cleanup.wait()
        raise AssertionError("cancelled synthetic SDK continued")

    class DrainingCredential(Credential):
        async def close(self):
            self.closed += 1
            close_entered.set()
            await release_close.wait()
            close_finished.set()

    sdk = DrainingCredential(token)
    original = identity.ScribeServiceIdentityOwner
    calls, created = [], []

    def downstream(request):
        calls.append(request)
        return httpx.Response(
            201, headers={"content-type": "application/json"}, content=b'{"ok":true}'
        )

    app, _, loops = application(
        monkeypatch,
        sdk,
        downstream,
        outer=outer,
        seconds=5,
        pool_enabled=pool_enabled,
    )

    def create(settings):
        credential = sdk if not created else Credential()
        created.append(credential)
        return original(settings, credential_factory=lambda _: credential)

    monkeypatch.setattr(identity, "ScribeServiceIdentityOwner", create)
    lifespan = app.router.lifespan_context(app)
    await lifespan.__aenter__()
    first_owner = app.state.scribe_service_identity
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        pending = asyncio.create_task(client.post(PATH))
        await entered.wait()
        shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
        try:
            await cleanup.wait()
            # The closing owner must remain visible while cancellation joins.
            assert app.state.scribe_service_identity is first_owner
            late = await client.post(PATH)
            assert late.status_code == 503
            assert len(loops) == 1 and sdk.calls == 1 and not calls
            assert not pending.done() and not shutdown.done()
            release_cleanup.set()
            await close_entered.wait()
            assert (await pending).status_code == 503
            # SDK close itself may resist. No absent-owner legacy fallback.
            assert app.state.scribe_service_identity is first_owner
            late = await client.post(PATH)
            assert late.status_code == 503
            assert len(loops) == 1 and len(created) == 1 and not calls
            assert not shutdown.done() and not close_finished.is_set()
        finally:
            release_cleanup.set()
            release_close.set()
            await shutdown
            await pending
        assert close_finished.is_set() and sdk.closed == 1
        assert app.state.scribe_service_identity is first_owner
        after = await client.post(PATH)
        assert after.status_code == 503
        assert len(loops) == 1 and len(created) == 1 and not calls

        # A later actual lifespan owns a fresh SDK, not the retired instance.
        async with app.router.lifespan_context(app):
            next_owner = app.state.scribe_service_identity
            assert next_owner is not first_owner
            result = await client.post(PATH)
            assert result.status_code == 200 and result.json() == {"ok": True}
            assert len(loops) == 2 and len(created) == 2
            assert created[1].calls == 2 and created[1].closed == 0
            assert len(calls) == 2
        assert app.state.scribe_service_identity is next_owner
        assert sdk.closed == 1 and created[1].closed == 1
