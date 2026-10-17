"""Synthetic SDK ownership tests; no Azure endpoint or real credential is used."""

import asyncio
import time
from contextvars import ContextVar
from dataclasses import replace
from threading import Event, get_ident

import pytest
from azure.core.credentials import AccessToken
from azure.core.exceptions import AzureError
from fastapi import FastAPI

from app.configuration import scribe_service_identity as identity
from app.configuration.scribe_workflow import ScribeWorkflowSettings

SETTINGS = ScribeWorkflowSettings(
    enabled=True,
    base_url="https://scribe.internal.example",
    scope="synthetic/.default",
    managed_identity_client_id="11111111-1111-4111-8111-111111111111",
    timeout_seconds=5,
    identity_reuse_enabled=True,
)


class Expired(Exception):
    pass


class Budget:
    def __init__(self):
        self.owner_thread: int | None = get_ident()
        self.cancelled = Event()

    def check(self):
        if self.cancelled.is_set():
            raise Expired("synthetic deadline")
        return 10


class Credential:
    def __init__(self, handler=None):
        self.calls, self.closed = 0, 0
        self.loop, self.thread = asyncio.get_running_loop(), get_ident()
        self.handler = handler

    async def get_token(self, *scopes, **kwargs):
        assert asyncio.get_running_loop() is self.loop
        assert get_ident() == self.thread
        assert scopes == (SETTINGS.scope,) and not kwargs
        self.calls += 1
        if self.handler:
            return await self.handler()
        return AccessToken("synthetic-" + str(self.calls), int(time.time()) + 60)

    async def close(self):
        assert asyncio.get_running_loop() is self.loop
        self.closed += 1


def in_worker(owner, operation):
    async def run():
        budget = Budget()
        with identity.capture_scribe_service_identity(owner, budget):
            return await operation(budget)

    return asyncio.run(run())


async def until(predicate):
    expires = time.monotonic() + 2
    while not predicate():
        assert time.monotonic() < expires
        await asyncio.sleep(0.001)


@pytest.mark.asyncio
async def test_distinct_worker_loops_borrow_one_sdk_but_ask_each_operation():
    private = ContextVar[str | None]("synthetic_patient_and_sql_context", default=None)
    created = []

    async def token():
        assert private.get() is None
        return AccessToken("synthetic-official-result", int(time.time()) + 60)

    def factory(client_id):
        assert client_id == SETTINGS.managed_identity_client_id
        credential = Credential(token)
        created.append(credential)
        return credential

    owner = identity.ScribeServiceIdentityOwner(SETTINGS, credential_factory=factory)
    assert len(created) == 1 and created[0].calls == 0  # No app-opening mint.
    loops = []

    async def request(_budget):
        loops.append(asyncio.get_running_loop())
        private.set("synthetic-private-patient-and-transaction")
        borrower = identity.current_scribe_service_identity(SETTINGS)
        assert borrower is not None
        try:
            for _ in range(2):
                token = await borrower.get_token(SETTINGS.scope)
                assert token.token == "synthetic-official-result"
        finally:
            await borrower.close()
        assert created[0].closed == 0

    private.set("parent-request-context")
    await asyncio.gather(
        *[asyncio.to_thread(in_worker, owner, request) for _ in range(3)]
    )
    assert len(set(loops)) == 3 and owner.loop not in loops
    assert created[0].calls == 6 and not owner._operations
    await owner.close()
    await owner.close()
    assert created[0].closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scopes", "kwargs"),
    [
        (("other/.default",), {}),
        ((SETTINGS.scope, "other/.default"), {}),
        ((), {}),
        ((SETTINGS.scope,), {"claims": "synthetic-claim"}),
        ((SETTINGS.scope,), {"tenant_id": "synthetic-tenant"}),
    ],
)
async def test_borrower_rejects_scope_or_request_claim_override(scopes, kwargs):
    credential = Credential()
    owner = identity.ScribeServiceIdentityOwner(
        SETTINGS, credential_factory=lambda _: credential
    )

    async def request(_budget):
        borrower = identity.current_scribe_service_identity(SETTINGS)
        assert borrower is not None
        with pytest.raises(AzureError, match="^scribe_identity_unavailable$"):
            await borrower.get_token(*scopes, **kwargs)
        await borrower.close()

    await asyncio.to_thread(in_worker, owner, request)
    assert credential.calls == 0
    await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed",
    [
        replace(SETTINGS, scope="other/.default"),
        replace(SETTINGS, managed_identity_client_id="other-client"),
        replace(SETTINGS, enabled=False),
    ],
)
async def test_changed_deployment_recipe_does_not_fall_back(changed):
    credential = Credential()
    owner = identity.ScribeServiceIdentityOwner(
        SETTINGS, credential_factory=lambda _: credential
    )

    async def request(_budget):
        with pytest.raises(AzureError):
            identity.current_scribe_service_identity(changed)

    await asyncio.to_thread(in_worker, owner, request)
    assert not credential.calls
    await owner.close()


@pytest.mark.asyncio
async def test_same_loop_or_foreign_thread_and_closed_borrower_refuse():
    credential = Credential()
    owner = identity.ScribeServiceIdentityOwner(
        SETTINGS, credential_factory=lambda _: credential
    )
    with pytest.raises(AzureError):
        owner.borrow(SETTINGS, Budget())

    async def request(_budget):
        borrower = identity.current_scribe_service_identity(SETTINGS)
        assert borrower is not None

        def wrong_thread():
            with pytest.raises(AzureError):
                asyncio.run(borrower.get_token(SETTINGS.scope))

        await asyncio.to_thread(wrong_thread)
        await borrower.close()
        with pytest.raises(AzureError):
            await borrower.get_token(SETTINGS.scope)

    await asyncio.to_thread(in_worker, owner, request)
    assert credential.calls == 0
    await owner.close()


@pytest.mark.asyncio
async def test_expired_budget_before_or_during_acquisition_never_returns_token():
    release, entered = asyncio.Event(), asyncio.Event()

    async def token():
        entered.set()
        await release.wait()
        return AccessToken("synthetic", int(time.time()) + 60)

    credential = Credential(token)
    owner = identity.ScribeServiceIdentityOwner(
        SETTINGS, credential_factory=lambda _: credential
    )
    budgets = []

    async def expired(budget):
        budget.cancelled.set()
        with pytest.raises(Expired):
            identity.current_scribe_service_identity(SETTINGS)

    async def expires_later(budget):
        budgets.append(budget)
        borrower = identity.current_scribe_service_identity(SETTINGS)
        assert borrower is not None
        try:
            with pytest.raises(AzureError):
                await borrower.get_token(SETTINGS.scope)
        finally:
            await borrower.close()

    await asyncio.to_thread(in_worker, owner, expired)
    assert credential.calls == 0
    worker = asyncio.create_task(asyncio.to_thread(in_worker, owner, expires_later))
    await entered.wait()
    budgets[0].cancelled.set()
    release.set()
    await worker
    assert credential.calls == 1 and not owner._operations
    await owner.close()


@pytest.mark.asyncio
async def test_cancel_queued_borrower_does_not_cancel_other_sdk_call():
    entered, release = asyncio.Event(), asyncio.Event()

    async def token():
        entered.set()
        await release.wait()
        return AccessToken("synthetic", int(time.time()) + 60)

    credential = Credential(token)
    owner = identity.ScribeServiceIdentityOwner(
        SETTINGS, credential_factory=lambda _: credential
    )
    handles = []

    async def request(_budget):
        borrower = identity.current_scribe_service_identity(SETTINGS)
        assert borrower is not None
        task = asyncio.create_task(borrower.get_token(SETTINGS.scope))
        handles.append((asyncio.get_running_loop(), task))
        try:
            return await task
        except asyncio.CancelledError:
            return None
        finally:
            await borrower.close()

    first = asyncio.create_task(asyncio.to_thread(in_worker, owner, request))
    await entered.wait()
    second = asyncio.create_task(asyncio.to_thread(in_worker, owner, request))
    await until(lambda: len(owner._operations) == 2)
    handles[1][0].call_soon_threadsafe(handles[1][1].cancel)
    assert await second is None
    assert credential.calls == 1 and not first.done()
    release.set()
    assert (await first).token == "synthetic"
    await owner.close()


@pytest.mark.asyncio
async def test_cancellation_and_shutdown_join_sdk_cleanup_before_owner_close():
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def token():
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            cleanup.set()
            await release.wait()  # Deliberately resistant synthetic cleanup.

    credential = Credential(token)
    owner = identity.ScribeServiceIdentityOwner(
        SETTINGS, credential_factory=lambda _: credential
    )
    handles = []

    async def request(_budget):
        borrower = identity.current_scribe_service_identity(SETTINGS)
        assert borrower is not None
        task = asyncio.create_task(borrower.get_token(SETTINGS.scope))
        handles.append((asyncio.get_running_loop(), task))
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            await borrower.close()

    worker = asyncio.create_task(asyncio.to_thread(in_worker, owner, request))
    await entered.wait()
    handles[0][0].call_soon_threadsafe(handles[0][1].cancel)
    await cleanup.wait()
    closing = asyncio.create_task(owner.close())
    await asyncio.sleep(0.01)
    closing.cancel()
    handles[0][0].call_soon_threadsafe(handles[0][1].cancel)
    await asyncio.sleep(0.01)
    assert not worker.done() and not closing.done() and credential.closed == 0
    assert len(owner._operations) == 1
    release.set()
    await worker
    with pytest.raises(asyncio.CancelledError):
        await closing
    await owner.close()
    assert credential.closed == 1 and not owner._operations


@pytest.mark.asyncio
async def test_cancel_before_start_and_before_first_task_instruction_join():
    credential = Credential()
    owner = identity.ScribeServiceIdentityOwner(
        SETTINGS, credential_factory=lambda _: credential
    )
    for before_start in (True, False):
        operation = identity._Operation(Budget())
        if before_start:
            operation.cancelled.set()
        owner._start(operation)
        if not before_start:
            assert operation.task is not None
            operation.task.cancel()
        await identity._join(operation.joined)
        with pytest.raises(AzureError):
            operation.result.result()
    assert credential.calls == 0 and not owner._operations
    await owner.close()


@pytest.mark.asyncio
async def test_borrower_close_joins_its_operation_without_closing_owner():
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def token():
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            cleanup.set()
            await release.wait()

    credential = Credential(token)
    owner = identity.ScribeServiceIdentityOwner(
        SETTINGS, credential_factory=lambda _: credential
    )
    handles = []

    async def request(_budget):
        borrower = identity.current_scribe_service_identity(SETTINGS)
        assert borrower is not None
        handles.append((asyncio.get_running_loop(), borrower))
        with pytest.raises(AzureError):
            await borrower.get_token(SETTINGS.scope)
        with pytest.raises(AzureError):
            await borrower.get_token(SETTINGS.scope)

    worker = asyncio.create_task(asyncio.to_thread(in_worker, owner, request))
    await entered.wait()
    loop, borrower = handles[0]
    closing = asyncio.run_coroutine_threadsafe(borrower.close(), loop)
    await cleanup.wait()
    assert not closing.done() and not worker.done() and credential.closed == 0
    release.set()
    await asyncio.wrap_future(closing)
    await worker
    assert not owner._closed and not owner._operations and credential.closed == 0
    await owner.close()


@pytest.mark.asyncio
async def test_sdk_error_and_close_error_are_fixed_and_close_is_owner_only():
    class Broken(Credential):
        async def get_token(self, *scopes, **kwargs):
            raise ValueError("synthetic-private-credential-error")

        async def close(self):
            self.closed += 1
            raise ValueError("synthetic-private-close-error")

    credential = Broken()
    owner = identity.ScribeServiceIdentityOwner(
        SETTINGS, credential_factory=lambda _: credential
    )

    async def request(_budget):
        borrower = identity.current_scribe_service_identity(SETTINGS)
        assert borrower is not None
        with pytest.raises(AzureError, match="^scribe_identity_unavailable$"):
            await borrower.get_token(SETTINGS.scope)
        await borrower.close()
        with pytest.raises(RuntimeError, match="^scribe_identity_wrong_loop$"):
            await owner.close()

    await asyncio.to_thread(in_worker, owner, request)
    for _ in range(2):
        with pytest.raises(AzureError, match="^scribe_identity_unavailable$"):
            await owner.close()
    assert credential.closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_lifespan_is_opt_in_constructs_without_mint_and_drains_once(
    monkeypatch, enabled
):
    original = identity.ScribeServiceIdentityOwner
    credentials = []

    def create(settings):
        credential = Credential()
        credentials.append(credential)
        return original(settings, credential_factory=lambda _: credential)

    monkeypatch.setattr(identity, "ScribeServiceIdentityOwner", create)
    app = FastAPI()
    identity.install_scribe_service_identity(
        app, replace(SETTINGS, identity_reuse_enabled=enabled)
    )
    async with app.router.lifespan_context(app):
        owner = app.state.scribe_service_identity
        assert (owner is not None) is enabled
        assert all(c.calls == 0 and c.closed == 0 for c in credentials)
    assert app.state.scribe_service_identity is owner
    assert owner is None or owner.closed
    assert len(credentials) == int(enabled)
    assert all(c.closed == 1 for c in credentials)
