"""Service-only background ownership and foreground priority, with no network."""

import asyncio
import time
from contextvars import ContextVar

import pytest
from azure.core.credentials import AccessToken
from azure.core.exceptions import AzureError
from fastapi import FastAPI

from app.configuration import scribe_service_identity as identity

from tests.authority_kernel.scribe_service_identity_test import (
    SETTINGS,
    Credential,
    in_worker,
    until,
)


async def request(_budget):
    borrower = identity.current_scribe_service_identity(SETTINGS)
    assert borrower is not None
    try:
        return await borrower.get_token(SETTINGS.scope)
    finally:
        await borrower.close()


@pytest.mark.asyncio
async def test_coalesced_warm_is_fixed_scope_empty_context_and_exposes_no_token():
    entered, release = asyncio.Event(), asyncio.Event()
    private = ContextVar[str | None](
        "synthetic_catalog_patient_and_principal", default=None
    )

    async def token():
        assert private.get() is None
        entered.set()
        await release.wait()
        return AccessToken("synthetic-private-service-token", int(time.time()) + 60)

    sdk = Credential(token)
    owner = identity.ScribeServiceIdentityOwner(
        SETTINGS, credential_factory=lambda _: sdk
    )
    private.set("synthetic-provider-query-and-catalog-result")
    try:
        assert owner.request_warm() is None
        operation = owner._warm
        assert operation is not None and not hasattr(operation, "result")
        for _ in range(10):
            assert owner.request_warm() is None and owner._warm is operation
        await entered.wait()
        assert sdk.calls == 1 and sdk.closed == 0
        release.set()
        await until(lambda: owner._warm is None)
        assert operation.joined.result() is None
        assert operation.timer is not None and operation.timer.cancelled()
    finally:
        await owner.close()
    assert sdk.closed == 1


@pytest.mark.asyncio
async def test_foreground_cancels_and_joins_resistant_warm_before_sdk_acquisition():
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def token():
        if sdk.calls == 1:
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                cleanup.set()
                await release.wait()
        return AccessToken("synthetic-foreground", int(time.time()) + 60)

    sdk = Credential(token)
    owner = identity.ScribeServiceIdentityOwner(
        SETTINGS, credential_factory=lambda _: sdk
    )
    try:
        owner.request_warm()
        warm = owner._warm
        await entered.wait()
        worker = asyncio.create_task(asyncio.to_thread(in_worker, owner, request))
        await cleanup.wait()
        assert warm is not None and warm.cancelled and not warm.joined.done()
        assert sdk.calls == 1 and not worker.done()
        owner.request_warm()  # Cannot add another task while a request waits.
        assert owner._warm is warm
        release.set()
        assert (await worker).token == "synthetic-foreground"
        assert sdk.calls == 2 and warm.joined.done() and owner._warm is None
    finally:
        release.set()
        await owner.close()


@pytest.mark.asyncio
async def test_foreground_pending_skips_warm_and_foreign_loop_cannot_start_it():
    entered, release = asyncio.Event(), asyncio.Event()

    async def token():
        entered.set()
        await release.wait()
        return AccessToken("synthetic", int(time.time()) + 60)

    sdk = Credential(token)
    owner = identity.ScribeServiceIdentityOwner(
        SETTINGS, credential_factory=lambda _: sdk
    )
    try:
        await asyncio.to_thread(lambda: asyncio.run(foreign(owner)))
        assert sdk.calls == 0 and owner._warm is None
        worker = asyncio.create_task(asyncio.to_thread(in_worker, owner, request))
        await entered.wait()
        for _ in range(5):
            owner.request_warm()
        assert sdk.calls == 1 and owner._warm is None
        release.set()
        await worker
    finally:
        release.set()
        await owner.close()


async def foreign(owner):
    assert owner.request_warm() is None


@pytest.mark.asyncio
async def test_deadline_cancels_once_and_retains_resistant_cleanup(monkeypatch):
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(identity, "WARM_ACQUISITION_SECONDS", 0.02)
    cancellations = []

    async def token():
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            task = asyncio.current_task()
            assert task is not None
            cancellations.append(task.cancelling())
            cleanup.set()
            await release.wait()

    sdk = Credential(token)
    owner = identity.ScribeServiceIdentityOwner(
        SETTINGS, credential_factory=lambda _: sdk
    )
    try:
        owner.request_warm()
        warm = owner._warm
        await entered.wait()
        await cleanup.wait()
        assert warm is not None and warm.cancelled and not warm.joined.done()
        closing = asyncio.create_task(owner.close())
        await asyncio.sleep(0.01)
        closing.cancel()
        closing.cancel()
        await asyncio.sleep(0.01)
        assert sdk.closed == 0 and not closing.done() and cancellations == [1]
        assert owner.request_warm() is None and owner._warm is warm
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert owner._warm is None and warm.joined.done() and sdk.closed == 1
        await owner.close()
        assert sdk.closed == 1
    finally:
        release.set()
        await owner.close()


@pytest.mark.asyncio
async def test_cancel_before_warm_first_instruction_never_calls_sdk():
    sdk = Credential()
    owner = identity.ScribeServiceIdentityOwner(
        SETTINGS, credential_factory=lambda _: sdk
    )
    owner.request_warm()
    warm = owner._warm
    assert warm is not None
    owner._cancel_warm(warm)  # No event-loop yield: SDK coroutine has not begun.
    await until(lambda: owner._warm is None)
    assert warm.joined.done() and sdk.calls == 0
    await owner.close()
    assert sdk.closed == 1


@pytest.mark.asyncio
async def test_warm_sdk_error_is_consumed_and_next_foreground_still_asks_sdk():
    async def token():
        if sdk.calls == 1:
            raise ValueError("synthetic-private-sdk-diagnostic")
        return AccessToken("synthetic-next-request", int(time.time()) + 60)

    sdk = Credential(token)
    owner = identity.ScribeServiceIdentityOwner(
        SETTINGS, credential_factory=lambda _: sdk
    )
    try:
        owner.request_warm()
        await until(lambda: owner._warm is None)
        assert (
            await asyncio.to_thread(in_worker, owner, request)
        ).token == "synthetic-next-request"
        assert sdk.calls == 2
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_missing_or_closed_application_owner_never_creates_fallback(monkeypatch):
    app = FastAPI()
    monkeypatch.setattr(
        identity,
        "create_scribe_workload_credential",
        lambda _: pytest.fail("legacy factory"),
    )
    assert identity.request_scribe_service_identity_warm(app) is None
    sdk = Credential()
    owner = identity.ScribeServiceIdentityOwner(
        SETTINGS, credential_factory=lambda _: sdk
    )
    app.state.scribe_service_identity = owner
    await owner.close()
    assert identity.request_scribe_service_identity_warm(app) is None
    assert sdk.calls == 0 and sdk.closed == 1 and owner._warm is None
    with pytest.raises(AzureError):
        await asyncio.to_thread(in_worker, owner, request)


@pytest.mark.asyncio
async def test_actual_capture_deadline_joins_warm_cleanup_without_http(monkeypatch):
    from threading import BoundedSemaphore

    import httpx

    from app.routes import scribe_capture_deadline as boundary

    from tests.authority_kernel.scribe_service_identity_route_test import (
        PATH,
        application,
    )

    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def token():
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            cleanup.set()
            await release.wait()
        raise AssertionError("synthetic warm must not continue")

    sdk = Credential(token)
    sent = []
    slots = BoundedSemaphore(1)
    monkeypatch.setattr(boundary, "_workers", slots)
    app, _, loops = application(
        monkeypatch,
        sdk,
        lambda r: sent.append(r) or pytest.fail("expired request reached HTTP"),
        outer=True,
        seconds=0.04,
    )
    async with app.router.lifespan_context(app):
        owner = app.state.scribe_service_identity
        owner.request_warm()
        await entered.wait()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.post(PATH)
            assert response.status_code == 504
            await cleanup.wait()
            assert sdk.calls == 1 and sdk.closed == 0 and len(loops) == 1
            assert owner._warm is not None and not owner._warm.joined.done()
            assert len(owner._operations) == 1 and not slots.acquire(blocking=False)
            assert (await client.post(PATH)).status_code == 503
            assert sdk.calls == 1 and len(loops) == 1 and not sent
            release.set()
            await until(lambda: not owner._operations and owner._warm is None)
            await until(lambda: slots.acquire(blocking=False))
            slots.release()
    assert sdk.closed == 1 and not sent
