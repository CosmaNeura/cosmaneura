"""A waiting capture DB call must not stall another request on the same replica."""

import asyncio
import concurrent.futures
import importlib
import time
from contextlib import contextmanager
from threading import Event, Thread, get_ident
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from app.configuration.scribe_capture import ScribeCaptureSettings
from app.routes import middleware
from app.routes import scribe_capture as route
from app.routes.agent import agent_api_app
from app.routes.dependencies.entra_service import (
    EntraServicePrincipal,
    authenticate_scribe_capture_request,
)
from app.services import scribe_capture_admission as admission

from activemodel.session_manager import aglobal_session
from sqlalchemy.exc import TimeoutError as SQLTimeout


@pytest.mark.asyncio
async def test_waiting_capture_keeps_other_requests_available(monkeypatch):
    """Exercise real route/gateway/transaction code, replacing only SQL effects."""
    target = FastAPI()
    target.include_router(agent_api_app)
    target.dependency_overrides[aglobal_session] = lambda: None
    target.dependency_overrides[authenticate_scribe_capture_request] = lambda: (
        EntraServicePrincipal(client_id="11111111-1111-4111-8111-111111111111")
    )
    target.dependency_overrides[route.get_capture_settings] = lambda: (
        ScribeCaptureSettings(enabled=True)
    )
    middleware.add_middleware(target)
    try:
        boundary = importlib.import_module("app.routes.scribe_capture_deadline")
    except ModuleNotFoundError as error:
        # Keeps the same regression runnable against the unmodified baseline.
        assert error.name == "app.routes.scribe_capture_deadline"
    else:
        monkeypatch.setattr(boundary, "CAPTURE_REQUEST_SECONDS", 0.4)
        boundary.install_capture_boundaries(target)

    @target.get("/unrelated-read")
    async def ordinary_read():
        return {"ok": True}

    entered, release, closed = Event(), Event(), Event()
    observed = {"sql_threads": [], "closed": 0}
    observer = {}
    main_thread = get_ident()

    class Database:
        def exec(self, statement):
            assert statement._for_update_arg is not None
            observed["sql_threads"].append(get_ident())
            if not entered.is_set():
                entered.set()
                assert release.wait(3), "independent release did not run"
            raise SQLTimeout("synthetic SQL wait")

        def rollback(self):
            pass

        def commit(self):
            pytest.fail("SQL failure must not commit")

    @contextmanager
    def database():
        yield Database()

    monkeypatch.setattr(admission, "get_session", database)
    monkeypatch.setattr(
        admission.CaptureCaller, "authorize",
        lambda *args: SimpleNamespace(id="scribe_capture_binding_01j00000000000000000000000"),
    )

    class Client:
        async def capture_operation(self, *args, **kwargs):
            pytest.fail("failed/expired SQL must not dispatch to Scribe")

        async def close(self):
            observed["closed"] += 1
            if observed["closed"] == 2:
                closed.set()

    target.dependency_overrides[route.get_admission_client_factory] = lambda: Client

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=target), base_url="http://testserver"
    ) as connection:
        async def capture(sequence):
            return await connection.post(
                "/service/v1/agent/scribe/capture-operations",
                headers={
                    "X-Cosmaneura-Mount-Context": "mctx_synthetic_secret_00000001",
                    "X-Cosmaneura-Interaction-Ref": "ix_synthetic_interaction_00001",
                    "X-Cosmaneura-Client-Turn-ID": str(UUID(int=sequence + 101)),
                    "X-Cosmaneura-Capture-Version": "1",
                    "Idempotency-Key": f"synthetic-receipt-{sequence}",
                },
                json={
                    "binding_ref": "scribe_capture_binding_01j00000000000000000000000",
                    "workspace_id": str(UUID(int=42)),
                    "request_nonce": str(UUID(int=sequence + 201)),
                    "operation": {
                        "kind": "chunk_receipt", "object_id": str(UUID(int=sequence + 301)),
                    },
                },
            )

        async def unrelated():
            return await connection.get("/unrelated-read")

        loop = asyncio.get_running_loop()

        def check_while_blocked():
            try:
                assert entered.wait(3)
                second = asyncio.run_coroutine_threadsafe(capture(8), loop)
                read = asyncio.run_coroutine_threadsafe(unrelated(), loop)
                done, _ = concurrent.futures.wait([read, second], timeout=0.2)
                observer["read_before_release"] = read in done and read.result().status_code
                observer["second_before_release"] = second in done and second.result().status_code
                release.wait(0.4)
                release.set()
                read.result(timeout=3)
                second.result(timeout=3)
            except BaseException as error:
                observer["error"] = error
                release.set()

        thread = Thread(target=check_while_blocked, daemon=True)
        thread.start()
        try:
            first = await capture(0)
            observer["first_before_release"] = not release.is_set()
            await asyncio.to_thread(thread.join, 3)
        finally:
            release.set()
        expires = time.monotonic() + 3
        while not closed.is_set() and time.monotonic() < expires:
            await asyncio.sleep(0.002)

    assert not thread.is_alive() and "error" not in observer, observer
    assert observer["read_before_release"] == 200, observer
    assert observer["second_before_release"] == 503, observer
    assert observer["first_before_release"] and first.status_code == 504
    assert main_thread not in observed["sql_threads"]
    assert observed["closed"] == 2
