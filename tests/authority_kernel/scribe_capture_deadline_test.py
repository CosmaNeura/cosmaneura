"""Actual capture ASGI routing under delays; no database or service is opened."""

import asyncio
import json
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from threading import BoundedSemaphore, Event, get_ident
from types import SimpleNamespace
from typing import Never
from uuid import uuid4

import httpx
import pytest
from azure.core.credentials import AccessToken
from fastapi import FastAPI

from app.configuration.scribe_capture import ScribeCaptureSettings
from app.routes import middleware
from app.routes import scribe_capture as route
from app.routes import scribe_capture_deadline as boundary
from app.routes.agent import agent_api_app
from app.routes.dependencies.entra_service import (
    EntraServicePrincipal,
    authenticate_scribe_capture_request,
)
from app.routes.scribe_native_capture import native_capture_api_app
from app.services import scribe_capture_admission as admission
from app.services import scribe_capture_notes as notes
from app.services import scribe_capture_scope as scope_service
from app.services.scribe_capture_deadline import (
    CaptureBudget,
    CaptureDeadlineExceeded,
    before_capture_statement,
    capture_budget,
    check_capture_deadline,
)
from app.services.scribe_workflow import (
    ConsentCreate,
    ConsentRecord,
    HttpScribeWorkflowClient,
)

from activemodel import session_manager
from activemodel.session_manager import aglobal_session
from sqlalchemy import Integer
from sqlalchemy.engine import URL, Engine
from sqlalchemy.engine.default import DefaultDialect
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool
from sqlmodel import Session as ActiveSession

PREFIX = "/service/v1/agent/scribe/"
DELAY = 0.08


def headers():
    return {
        "X-Cosmaneura-Mount-Context": "mctx_synthetic_secret_00000001",
        "X-Cosmaneura-Interaction-Ref": "ix_synthetic_interaction_00001",
        "X-Cosmaneura-Client-Turn-ID": str(uuid4()),
        "X-Cosmaneura-Capture-Version": "1",
        "Content-Type": "application/json",
        "Idempotency-Key": "synthetic-capture-key",
    }


def admission_body():
    return {
        "binding_ref": "scribe_capture_binding_01j00000000000000000000000",
        "workspace_id": str(uuid4()),
        "request_nonce": str(uuid4()),
    }


def note_body():
    return {
        **admission_body(),
        "expected_session_id": str(uuid4()),
        "schema_ref": "doctor_note_schema_01j00000000000000000000000",
        "action": "prepare",
    }


def app(monkeypatch, *, seconds=DELAY):
    monkeypatch.setattr(boundary, "CAPTURE_REQUEST_SECONDS", seconds)
    result = FastAPI()
    result.include_router(agent_api_app)
    result.include_router(native_capture_api_app)
    result.dependency_overrides[aglobal_session] = lambda: None
    result.dependency_overrides[authenticate_scribe_capture_request] = (
        lambda: EntraServicePrincipal(client_id="11111111-1111-4111-8111-111111111111")
    )
    result.dependency_overrides[route.get_capture_settings] = (
        lambda: ScribeCaptureSettings(
            enabled=True, notes_enabled=True, prep_enabled=True
        )
    )
    return result


async def until(event: Event):
    expires = time.monotonic() + 2
    while not event.is_set():
        assert time.monotonic() < expires, "owned worker did not settle"
        await asyncio.sleep(0.002)


async def resistant_wait(release: Event, cancelled: Event):
    while not release.is_set():
        try:
            await asyncio.sleep(0.002)
        except asyncio.CancelledError:
            cancelled.set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "capture-bindings/resolve",
        "capture-admissions",
        "capture-operations",
        "capture-notes",
        "encounter-context",
    ],
)
async def test_slow_body_times_out_before_any_service_or_client(monkeypatch, path):
    target = app(monkeypatch)
    target.dependency_overrides[route.get_admission_client_factory] = lambda: (
        lambda: pytest.fail("client constructed after slow body")
    )
    monkeypatch.setattr(
        scope_service,
        "resolve_capture_binding",
        lambda **_: pytest.fail("binding reached"),
    )
    consumed = Event()

    async def body():
        consumed.set()
        yield b'{"private_sentinel":'
        await asyncio.sleep(60)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=target), base_url="http://test"
    ) as client:
        started = time.monotonic()
        response = await client.post(PREFIX + path, headers=headers(), content=body())
    assert response.status_code == 504 and consumed.is_set()
    assert time.monotonic() - started < 0.6
    assert "private_sentinel" not in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize("outer_boundary", [False, True])
async def test_slow_sync_auth_is_private_and_never_reaches_body_or_scope(
    monkeypatch, outer_boundary
):
    target = app(monkeypatch)
    if outer_boundary:
        boundary.install_capture_boundaries(target)
        assert set(target.state.capture_deadline_paths) == boundary.CAPTURE_PATHS
    release, entered, settled = Event(), Event(), Event()
    inherited = ContextVar[str | None]("test_borrowed_session", default=None)
    token = inherited.set("main-request-owned-session")
    auth_threads = []

    def auth():
        auth_threads.append(get_ident())
        assert inherited.get() is None
        entered.set()
        release.wait(2)
        settled.set()
        return EntraServicePrincipal(client_id="11111111-1111-4111-8111-111111111111")

    target.dependency_overrides[authenticate_scribe_capture_request] = auth
    monkeypatch.setattr(
        scope_service, "resolve_capture_binding", lambda **_: pytest.fail("late scope")
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=target), base_url="http://test"
        ) as client:
            response = await client.post(
                PREFIX + "capture-bindings/resolve", headers=headers(), content=b"{}"
            )
        assert response.status_code == 504 and entered.is_set() and not settled.is_set()
        assert get_ident() not in auth_threads
    finally:
        release.set()
        inherited.reset(token)
    await until(settled)


@pytest.mark.asyncio
@pytest.mark.parametrize("outer_boundary", [False, True])
async def test_sync_auth_retains_one_slot_until_dependency_settles(
    monkeypatch, outer_boundary
):
    target = app(monkeypatch)
    slots = BoundedSemaphore(1)
    monkeypatch.setattr(boundary, "_workers", slots)
    if outer_boundary:
        monkeypatch.setattr(middleware, "ALLOWED_HOST_LIST", "testserver")
        middleware.add_middleware(target)
        boundary.install_capture_boundaries(target)
    release, entered, settled = Event(), [], []

    def auth():
        entered.append(get_ident())
        release.wait(2)
        settled.append(get_ident())
        return EntraServicePrincipal(client_id="11111111-1111-4111-8111-111111111111")

    target.dependency_overrides[authenticate_scribe_capture_request] = auth
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=target), base_url="http://testserver"
        ) as client:
            first = await client.post(PREFIX + "capture-bindings/resolve", json={})
            assert first.status_code == 504 and len(entered) == 1 and not settled
            await asyncio.sleep(0.04)  # Allow the cancelled private task to unwind.
            second = await client.post(PREFIX + "capture-bindings/resolve", json={})
            assert second.status_code == 503
            assert len(entered) == 1 and not settled
    finally:
        release.set()
        expires = time.monotonic() + 2
        while len(settled) != len(entered):
            assert time.monotonic() < expires, "sync authentication did not settle"
            await asyncio.sleep(0.002)
    # Returning the dependency result grants no late route work; capacity is
    # restored only after the dependency and its private request have settled.
    expires = time.monotonic() + 2
    while not slots.acquire(blocking=False):
        assert time.monotonic() < expires, "settled request retained its slot"
        await asyncio.sleep(0.002)
    slots.release()


@pytest.mark.asyncio
async def test_late_consent_never_records_receipt_or_dispatches_session(monkeypatch):
    target = app(monkeypatch)
    release, entered, cancelled, closed = Event(), Event(), Event(), Event()
    calls = []
    payload = ConsentCreate(
        appointment_id="appointment",
        practitioner_id="doctor",
        patient_id="patient",
        modality="in_person",
        participant_regions={"US-NY"},
        method="verbal_attestation",
        all_parties_consented=True,
        granted_at=datetime.now(UTC),
    )
    claim = SimpleNamespace(
        phase="consent",
        scribe_tenant_id=uuid4(),
        payload=payload,
        key="retained-consent-key",
    )
    monkeypatch.setattr(
        admission, "_prepare", lambda *args, **kwargs: "retained-admission"
    )
    monkeypatch.setattr(admission, "_claim", lambda *args: claim)
    monkeypatch.setattr(
        admission, "_accept_receipt", lambda *args: calls.append("published receipt")
    )
    monkeypatch.setattr(
        admission, "_record_failure", lambda *args: calls.append("failure journal")
    )

    class Client:
        async def create_consent(self, *args, **kwargs):
            calls.append(("consent dispatched", kwargs["idempotency_key"]))
            entered.set()
            await resistant_wait(release, cancelled)
            return ConsentRecord(
                **payload.model_dump(), id=uuid4(), policy_version="synthetic-v1"
            )

        async def create_session(self, *args, **kwargs):
            calls.append("session dispatched")

        async def close(self):
            closed.set()

    target.dependency_overrides[route.get_admission_client_factory] = lambda: Client
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=target), base_url="http://test"
        ) as client:
            response = await client.post(
                PREFIX + "capture-admissions", headers=headers(), json=admission_body()
            )
        assert response.status_code == 504 and entered.is_set()
        assert calls == [("consent dispatched", "retained-consent-key")]
    finally:
        release.set()
    await until(closed)
    # Failure journaling is an existing service attempt; production SQL fences
    # refuse it after expiry. No receipt is accepted and no next phase is sent.
    assert "published receipt" not in calls and "session dispatched" not in calls
    assert calls.count(("consent dispatched", "retained-consent-key")) == 1
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_late_note_commit_is_refused_and_cleanup_retains_capacity(monkeypatch):
    target = app(monkeypatch)
    monkeypatch.setattr(boundary, "_workers", BoundedSemaphore(1))
    release, entered, cancelled, attempted, cleanup_release, closed = (
        Event() for _ in range(6)
    )
    calls = []

    class Client:
        async def close(self):
            await resistant_wait(cleanup_release, cancelled)
            closed.set()

    async def late_note(**_):
        entered.set()
        await resistant_wait(release, cancelled)
        try:
            with Session() as session:
                # Actual SQLAlchemy commit event, before any connection/SQL.
                with pytest.raises(CaptureDeadlineExceeded):
                    session.commit()
                calls.append("commit refused")
        finally:
            attempted.set()
        raise notes.CaptureNoteUnconfirmed

    monkeypatch.setattr(notes, "capture_note", late_note)
    target.dependency_overrides[route.get_admission_client_factory] = lambda: Client
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=target), base_url="http://test"
        ) as client:
            response = await client.post(
                PREFIX + "capture-notes", headers=headers(), json=note_body()
            )
            assert response.status_code == 504 and entered.is_set()
            release.set()
            await until(attempted)
            saturated = await client.post(
                PREFIX + "capture-notes", headers=headers(), json=note_body()
            )
            assert saturated.status_code == 503 and not closed.is_set()
        assert calls == ["commit refused"]
    finally:
        release.set()
        cleanup_release.set()
    await until(closed)


@pytest.mark.asyncio
async def test_cancelled_request_never_releases_private_late_response(monkeypatch):
    target = app(monkeypatch, seconds=2)
    release, entered, cancelled, closed = Event(), Event(), Event(), Event()

    class Client:
        async def close(self):
            closed.set()

    async def work(**_):
        entered.set()
        await resistant_wait(release, cancelled)
        check_capture_deadline()
        pytest.fail("late work escaped request cancellation")

    monkeypatch.setattr(notes, "capture_note", work)
    target.dependency_overrides[route.get_admission_client_factory] = lambda: Client
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=target), base_url="http://test"
        ) as client:
            request = asyncio.create_task(
                client.post(
                    PREFIX + "capture-notes", headers=headers(), json=note_body()
                )
            )
            await until(entered)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
    finally:
        release.set()
    await until(closed)


def test_expired_sql_has_no_statement_or_commit_and_does_not_affect_other_context():
    sent = []
    connection = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    cursor = SimpleNamespace(execute=sent.append)
    budget = CaptureBudget(2)
    with capture_budget(budget):
        before_capture_statement(
            connection, cursor, "SELECT retained", None, None, False
        )
        assert len(sent) == 2 and all(
            command.startswith("SET LOCAL ") for command in sent
        )
        budget.cancel()
        with pytest.raises(CaptureDeadlineExceeded):
            before_capture_statement(
                connection, cursor, "UPDATE clinical", None, None, False
            )
        with Session() as session, pytest.raises(CaptureDeadlineExceeded):
            session.commit()
    assert len(sent) == 2
    with Session() as session:
        session.commit()  # No SQL; global hook is inert outside capture context.


@pytest.mark.asyncio
async def test_resistant_model_result_cannot_become_accepted_output():
    budget = CaptureBudget(DELAY)

    class Model:
        async def complete(self, *_args, **_kwargs):
            budget.cancel()
            return '{"Plan":"private_sentinel"}'

    with capture_budget(budget), pytest.raises(notes.CaptureNoteUnconfirmed):
        await notes._model_call(Model(), "synthetic", json_output=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_expired_token_acquisition_cannot_dispatch_http(method):
    dispatched = []
    token_calls = []
    budget = CaptureBudget(0.01)

    class Credential:
        async def get_token(self, *_args: str, **_kwargs):
            token_calls.append("token requested")
            await asyncio.sleep(0.02)
            return AccessToken("synthetic-secret", 9999999999)

        async def close(self):
            return None

    def received(request):
        dispatched.append(request)
        return httpx.Response(503)

    async with httpx.AsyncClient(
        base_url="https://scribe.example", transport=httpx.MockTransport(received)
    ) as transport:
        client = HttpScribeWorkflowClient(
            base_url="https://scribe.example",
            scope="synthetic/.default",
            timeout_seconds=1,
            credential=Credential(),
            client=transport,
        )
        with capture_budget(budget), pytest.raises(CaptureDeadlineExceeded):
            if method == "GET":
                await client.get_capture_status(uuid4(), uuid4())
            else:
                await client._post(
                    "/v1/sessions",
                    uuid4(),
                    ConsentCreate(
                        appointment_id="appointment",
                        practitioner_id="doctor",
                        patient_id="patient",
                        modality="in_person",
                        participant_regions={"US-NY"},
                        method="verbal_attestation",
                        all_parties_consented=True,
                        granted_at=datetime.now(UTC),
                    ),
                    "synthetic-key",
                    ConsentRecord,
                )
    assert token_calls == ["token requested"] and dispatched == []


@pytest.mark.asyncio
async def test_model_thread_queued_until_expiry_never_dispatches(monkeypatch):
    dispatched = []
    budget = CaptureBudget(0.01)

    async def queued(function):
        await asyncio.sleep(0.02)
        return function()

    monkeypatch.setattr(notes.asyncio, "to_thread", queued)
    monkeypatch.setattr(
        notes.openai, "with_options", lambda **_: dispatched.append("model client")
    )
    with capture_budget(budget), pytest.raises(CaptureDeadlineExceeded):
        await notes.ConfiguredNoteModel().complete("synthetic", json_output=True)
    assert dispatched == []


def test_expired_flush_is_refused_before_connection_acquisition():
    class Base(DeclarativeBase):
        pass

    class Draft(Base):
        __tablename__ = "synthetic_deadline_draft"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)

    budget = CaptureBudget(1)
    with capture_budget(budget), Session() as session:
        session.add(Draft(id=1))
        budget.cancel()
        with pytest.raises(CaptureDeadlineExceeded):
            session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize("error_output", [False, True])
async def test_stalled_output_is_bounded_and_never_admits_next_chunk(
    monkeypatch, error_output
):
    target = app(monkeypatch)
    slots = BoundedSemaphore(1)
    monkeypatch.setattr(boundary, "_workers", slots)
    monkeypatch.setattr(boundary, "ERROR_SEND_SECONDS", 0.03)
    if not error_output:
        target.dependency_overrides[route.get_capture_settings] = (
            lambda: ScribeCaptureSettings(enabled=False)
        )
    sent, cancelled = [], Event()
    release, finished = Event(), Event()

    async def receive():
        # Enabled route exhausts its body budget, then emits a static failure.
        await asyncio.sleep(60)
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)
        await resistant_wait(release, cancelled)
        finished.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": PREFIX + "capture-notes",
        "raw_path": (PREFIX + "capture-notes").encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [
            (key.lower().encode(), value.encode()) for key, value in headers().items()
        ],
        "server": ("test", 80),
        "client": ("synthetic", 1),
    }
    try:
        started = time.monotonic()
        await target(scope, receive, send)
        assert time.monotonic() - started < 0.6
        assert len(sent) == 1 and sent[0]["type"] == "http.response.start"
        assert not slots.acquire(blocking=False), (
            "slot released while sender still owns buffered output"
        )
    finally:
        release.set()
    await until(finished)
    await asyncio.sleep(0.01)
    assert len(sent) == 1 and cancelled.is_set()
    assert slots.acquire(blocking=False)
    slots.release()


@pytest.mark.asyncio
async def test_real_global_session_dependency_is_owned_until_worker_finishes(
    monkeypatch,
):
    target = app(monkeypatch)
    target.dependency_overrides.pop(aglobal_session)
    release, entered, cancelled, session_closed = Event(), Event(), Event(), Event()
    life = []
    owned = []

    class Manager:
        @contextmanager
        def get_session(self):
            with ActiveSession() as session:
                owned.append(session)
                life.append(("open", get_ident()))
                try:
                    yield session
                finally:
                    life.append(("close", get_ident()))
                    session_closed.set()

    manager = Manager()
    monkeypatch.setattr(
        session_manager.SessionManager, "get_instance", classmethod(lambda cls: manager)
    )

    class Client:
        async def close(self):
            return None

    async def work(**_):
        assert session_manager._session_context.get() is owned[0]
        entered.set()
        await resistant_wait(release, cancelled)
        check_capture_deadline()
        pytest.fail("expired work continued")

    target.dependency_overrides[route.get_admission_client_factory] = lambda: Client
    monkeypatch.setattr(notes, "capture_note", work)
    # A real request-scoped ActiveModel session must not be borrowed by the new worker.
    with ActiveSession() as parent_session:
        token = session_manager._session_context.set(parent_session)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=target), base_url="http://test"
            ) as client:
                response = await client.post(
                    PREFIX + "capture-notes", headers=headers(), json=note_body()
                )
            assert (
                response.status_code == 504
                and entered.is_set()
                and not session_closed.is_set()
            )
            assert session_manager._session_context.get() is parent_session
        finally:
            release.set()
            session_manager._session_context.reset(token)
    await until(session_closed)
    assert life[0][0] == "open" and life[1] == ("close", life[0][1])
    assert life[0][1] != get_ident()


@pytest.mark.asyncio
async def test_peer_disconnect_fences_remote_work_before_the_90_second_limit(
    monkeypatch,
):
    target = app(monkeypatch, seconds=2)
    entered, disconnect, release, cancelled, closed = (Event() for _ in range(5))
    sent = []
    first = True

    class Client:
        async def close(self):
            closed.set()

    async def work(**_):
        entered.set()
        await resistant_wait(release, cancelled)
        check_capture_deadline()
        pytest.fail("remote continuation after peer disconnect")

    monkeypatch.setattr(notes, "capture_note", work)
    target.dependency_overrides[route.get_admission_client_factory] = lambda: Client

    async def receive():
        nonlocal first
        if first:
            first = False
            return {
                "type": "http.request",
                "body": json.dumps(note_body()).encode(),
                "more_body": False,
            }
        await until(disconnect)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": PREFIX + "capture-notes",
        "raw_path": (PREFIX + "capture-notes").encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [
            (key.lower().encode(), value.encode()) for key, value in headers().items()
        ],
        "server": ("test", 80),
        "client": ("synthetic", 1),
    }
    request = asyncio.create_task(target(scope, receive, send))
    try:
        await until(entered)
        started = time.monotonic()
        disconnect.set()
        await asyncio.wait_for(request, timeout=0.5)
        assert time.monotonic() - started < 0.5 and sent == []
        assert not closed.is_set()
    finally:
        release.set()
    await until(closed)
    assert cancelled.is_set()


def test_connection_commit_is_rechecked_after_flush_or_other_prior_work():
    calls = []

    class DBAPI:
        autocommit: bool = False

        def cursor(self, *args, **kwargs) -> Never:
            raise AssertionError("commit-boundary test must not issue SQL")

        def rollback(self):
            calls.append("rollback")

        def commit(self):
            calls.append("commit")

        def close(self):
            calls.append("close")

    # Actual SQLAlchemy Connection commit dispatch with a local DBAPI double.
    # No network, SQL server, connection string or live transaction is involved.
    engine = Engine(
        StaticPool(creator=DBAPI), DefaultDialect(), URL.create("synthetic")
    )
    budget = CaptureBudget(2)
    try:
        with capture_budget(budget), engine.connect() as connection:
            connection.begin()
            budget.cancel()  # A preceding flush or lock wait has finished late.
            with pytest.raises(CaptureDeadlineExceeded):
                connection.commit()
    finally:
        engine.dispose()
    assert "commit" not in calls


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_public", [False, True])
async def test_saturated_error_senders_remain_bounded_until_actual_settlement(
    monkeypatch, cancel_public
):
    target = app(monkeypatch)
    workers, error_workers = BoundedSemaphore(1), BoundedSemaphore(2)
    assert workers.acquire(blocking=False)
    monkeypatch.setattr(boundary, "_workers", workers)
    # raising=False lets this same regression demonstrate the missing pool on R1.
    monkeypatch.setattr(boundary, "_error_workers", error_workers, raising=False)
    monkeypatch.setattr(boundary, "ERROR_SEND_SECONDS", 0.03)
    release, canceled, entered, all_settled = Event(), Event(), Event(), Event()
    sent, settled = [], []

    async def receive():
        await asyncio.sleep(60)
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)
        if len(sent) >= 2:
            entered.set()
        await resistant_wait(release, canceled)
        settled.append(message)
        if len(settled) == len(sent):
            all_settled.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": PREFIX + "capture-admissions",
        "raw_path": (PREFIX + "capture-admissions").encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1),
        "server": ("owned", 1),
    }
    requests = [
        asyncio.create_task(target(dict(scope), receive, send)) for _ in range(4)
    ]
    try:
        await until(entered)
        if cancel_public:
            for request in requests:
                request.cancel()
        await asyncio.gather(*requests, return_exceptions=True)
        assert len(sent) == 2, (
            "saturated failure senders exceeded their separate admission cap"
        )
        assert all(message["type"] == "http.response.start" for message in sent)
        assert all(message["status"] == 503 for message in sent)
        assert canceled.is_set() and settled == []
        assert not error_workers.acquire(blocking=False), (
            "public completion released live sender ownership"
        )
        # More saturated requests cannot accumulate additional private send tasks.
        await target(dict(scope), receive, send)
        assert len(sent) == 2
    finally:
        release.set()
        await asyncio.gather(*requests, return_exceptions=True)
        await until(all_settled)
    await asyncio.sleep(0)
    assert error_workers.acquire(blocking=False)
    assert error_workers.acquire(blocking=False)
    assert not error_workers.acquire(blocking=False)
