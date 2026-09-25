"""Real ASGI/request-session regression against an explicitly owned local PG.

Run with --confcutdir=tests/authority_kernel and AGENT_STALL_POSTGRES_URL. Only
the Doctor table in that synthetic database is used; no external auth runs.
"""

import asyncio
import os
import re
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import UTC, datetime
from threading import Event, Thread
from time import monotonic, sleep
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import anyio
import httpx
import pytest
from anyio.to_thread import run_sync
from fastapi import FastAPI

import app.configuration.session_prep as prep_configuration
import app.routes.agent as agent
from app.configuration.session_prep import SessionPrepProviderSettings
from app.routes.dependencies.entra_service import EntraServicePrincipal

from activemodel.session_manager import SessionManager, _session_context, get_session
from app.models.doctor import Doctor
from sqlalchemy import Table, event, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, SQLModel, create_engine, select


@pytest.fixture
def database(monkeypatch):
    url = os.environ.get("AGENT_STALL_POSTGRES_URL")
    if url is None:
        pytest.skip("AGENT_STALL_POSTGRES_URL must name the owned synthetic database")
    assert re.fullmatch(
        r"postgresql://synthetic:synthetic@127\.0\.0\.1:[0-9]+/agent_db_stall", url
    ), "This test may only use its explicitly owned loopback PostgreSQL database"
    engine = create_engine(
        url.replace("postgresql://", "postgresql+psycopg://", 1),
        connect_args={"options": "-c statement_timeout=5000 -c lock_timeout=4000"},
    )
    SQLModel.metadata.create_all(
        engine, tables=[cast(Table, inspect(Doctor).local_table)]
    )
    manager = SessionManager.get_instance()
    monkeypatch.setattr(manager, "_engine", engine)
    monkeypatch.setattr(manager, "session_connection", None)
    with Session(engine) as session:
        doctor = Doctor(clerk_id="user_test_stall_" + uuid4().hex, biography=None)
        session.add(doctor)
        session.commit()
        doctor_id = doctor.id
    try:
        yield engine, doctor_id
    finally:
        engine.dispose()


def request_case(route: str) -> tuple[str, dict[str, Any]]:
    now = datetime.now(UTC).isoformat()
    headers = {
        "X-Cosmaneura-Mount-Context": "mctx_synthetic_contention",
        "X-Cosmaneura-Interaction-Ref": "interaction_synthetic_0001",
        "X-Cosmaneura-Client-Turn-ID": str(uuid4()),
        "Idempotency-Key": "synthetic-contention-0001",
    }
    if route == "bootstrap":
        return "/mount-context/bootstrap", {
            "headers": {
                "X-Cosmaneura-Mount-Context": headers["X-Cosmaneura-Mount-Context"]
            }
        }
    if route == "tool":
        body = {
            "tool_name": "session-prep.context.read",
            "tool_version": "1.0.0",
            "subject_ref": None,
            "arguments": {
                "source_turn_ref": "turn_synthetic_0001",
                "context_snapshot_ref": "snapshot_synthetic_0001",
                "purpose": "session_prep",
                "detail": "brief",
            },
        }
        path = "/tools/invoke"
    elif route == "turn":
        body = {
            "phase": "user_accepted",
            "ingress_kind": "chat_text",
            "action_code": None,
            "message": {"content_type": "text/plain", "content": "Synthetic test"},
        }
        path = "/conversation-turns/write"
    elif route == "audit":
        body = {
            "client_event_id": str(uuid4()),
            "observed_at": now,
            "event_type": "sovereign.turn.completed",
            "observation_status": "completed",
            "route": None,
            "intent": None,
            "content_sha256": None,
            "platform_refs": [],
        }
        path = "/audit-events/write"
    else:
        body = {
            "client_event_id": str(uuid4()),
            "call_kind": "reasoning",
            "call_sequence": 1,
            "workstream": "provider.chat",
            "provider": "azure_openai",
            "provider_request_id": None,
            "model": "synthetic",
            "deployment": None,
            "purpose": "drafting",
            "started_at": now,
            "completed_at": now,
            "latency_ms": 1,
            "status": "succeeded",
            "usage_availability": "complete",
            "usage_source": "provider_response",
            "token_usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "cached_input_tokens": 0,
                "reasoning_tokens": None,
            },
        }
        path = "/model-usage-events/report"
    return path, {"headers": headers, "json": body}


@pytest.mark.parametrize("route", ["bootstrap", "tool", "turn", "audit", "usage"])
@pytest.mark.parametrize("denied", [False, True], ids=["database-error", "denied"])
def test_doctor_lock_wait_keeps_health_responsive_and_cleans_request(
    database, monkeypatch, route, denied
):
    engine, doctor_id = database
    state = SimpleNamespace(
        session=None,
        pid=None,
        health_before_release=False,
        lock_wait_seen=False,
        commits=0,
        holder_error=None,
    )
    locked, entered = Event(), Event()
    application = FastAPI()
    application.include_router(agent.agent_api_app)

    @application.get("/healthcheck")
    async def healthcheck():
        return {"status": "ok"}

    def authenticate():
        state.session = _session_context.get()
        assert state.session is not None
        return EntraServicePrincipal(client_id="synthetic-workload")

    application.dependency_overrides[agent.authenticate_agent_api_request] = (
        authenticate
    )
    application.dependency_overrides[agent.authenticate_sovereign_tool_request] = (
        authenticate
    )
    monkeypatch.setattr(
        prep_configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        SessionPrepProviderSettings(True, frozenset()),
    )

    def contend(**kwargs):
        with get_session() as session:
            assert session is state.session
            assert kwargs.get("session", session) is session
            event.listen(
                session,
                "after_commit",
                lambda _s: setattr(state, "commits", state.commits + 1),
            )
            state.pid = (
                session.connection()
                .execute(text("SELECT pg_backend_pid()"))
                .scalar_one()
            )
            entered.set()
            doctor = session.exec(
                select(Doctor).where(Doctor.id == doctor_id).with_for_update()
            ).one()
            doctor.biography = "Synthetic uncommitted write must roll back"
            session.flush()
            if denied:
                raise agent.app.services.authority.AuthorityRequestDeniedError
            raise SQLAlchemyError("synthetic database failure")

    services = {
        "bootstrap": (agent.app.services.authority, "bootstrap_mount_context"),
        "tool": (agent.app.services.session_prep, "authorize_session_prep_read"),
        "turn": (agent.app.services.authority_conversation, "write_conversation_turn"),
        "audit": (
            agent.app.services.authority_audit,
            "write_observational_audit_event",
        ),
        "usage": (agent.app.services.authority_usage, "report_model_usage_event"),
    }
    target, name = services[route]
    monkeypatch.setattr(target, name, contend)

    async def exercise():
        loop = asyncio.get_running_loop()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://testserver"
        ) as client:

            def holder():
                try:
                    with Session(engine) as session:
                        session.exec(
                            select(Doctor)
                            .where(Doctor.id == doctor_id)
                            .with_for_update()
                        ).one()
                        locked.set()
                        assert entered.wait(3)
                        # Prove a PostgreSQL lock wait, rather than relying on timing.
                        with engine.connect() as observer:
                            deadline = monotonic() + 2
                            while monotonic() < deadline:
                                blockers = observer.execute(
                                    text("SELECT cardinality(pg_blocking_pids(:pid))"),
                                    {"pid": state.pid},
                                ).scalar_one()
                                if blockers:
                                    state.lock_wait_seen = True
                                    break
                                sleep(0.01)
                        assert state.lock_wait_seen
                        health = asyncio.run_coroutine_threadsafe(
                            client.get("/healthcheck"), loop
                        )
                        try:
                            response = health.result(timeout=0.75)
                            state.health_before_release = response.status_code == 200
                        except FutureTimeout:
                            pass
                        # Always unblock the old implementation, so its regression
                        # fails normally instead of hanging the test runner.
                        session.rollback()
                except BaseException as error:
                    state.holder_error = error

            thread = Thread(target=holder, daemon=True)
            thread.start()
            assert await asyncio.to_thread(locked.wait, 3)
            path, kwargs = request_case(route)
            response = await client.post("/service/v1/agent" + path, **kwargs)
            await asyncio.to_thread(thread.join, 3)
            assert not thread.is_alive()
            assert state.holder_error is None
            assert response.status_code == (403 if denied else 503), response.text
        assert _session_context.get() is None

    asyncio.run(exercise())
    assert state.lock_wait_seen
    assert state.commits == 0, "Authorization must not commit to escape contention"
    assert not state.session.in_transaction(), "Request session must be closed"
    with Session(engine) as session:
        doctor = session.exec(
            select(Doctor).where(Doctor.id == doctor_id).with_for_update(nowait=True)
        ).one()
        assert doctor.biography is None, "Failure must roll back the pending write"
    assert state.health_before_release, "Doctor lock wait blocked the ASGI event loop"


def test_cancelled_request_waits_for_database_worker_before_session_cleanup(
    database, monkeypatch
):
    engine, doctor_id = database
    entered, release, worker_finished, request_finished = (Event() for _ in range(4))
    state = SimpleNamespace(session=None, scope=None)
    application = FastAPI()
    application.include_router(agent.agent_api_app)

    @application.get("/healthcheck")
    async def healthcheck():
        return {"status": "ok"}

    def authenticate():
        state.session = _session_context.get()
        return EntraServicePrincipal(client_id="synthetic-workload")

    application.dependency_overrides[agent.authenticate_agent_api_request] = (
        authenticate
    )

    def blocked_bootstrap(**_kwargs):
        with get_session() as session:
            assert session is state.session
            doctor = session.exec(
                select(Doctor).where(Doctor.id == doctor_id).with_for_update()
            ).one()
            doctor.biography = "Synthetic cancelled write must roll back"
            session.flush()
            entered.set()
            assert release.wait(3)
            assert session.in_transaction(), "Teardown closed an active worker session"
            worker_finished.set()
            raise SQLAlchemyError("synthetic database failure after cancellation")

    monkeypatch.setattr(
        agent.app.services.authority, "bootstrap_mount_context", blocked_bootstrap
    )

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://testserver"
        ) as client:

            async def request():
                try:
                    with anyio.CancelScope() as scope:
                        state.scope = scope
                        path, kwargs = request_case("bootstrap")
                        await client.post("/service/v1/agent" + path, **kwargs)
                finally:
                    request_finished.set()

            async with anyio.create_task_group() as group:
                group.start_soon(request)
                try:
                    assert await run_sync(entered.wait, 3)
                    state.scope.cancel()
                    await anyio.sleep(0.02)
                    assert not request_finished.is_set()
                    assert state.session.in_transaction()
                    assert (await client.get("/healthcheck")).status_code == 200
                finally:
                    release.set()
        assert _session_context.get() is None

    anyio.run(exercise)
    assert worker_finished.is_set() and request_finished.is_set()
    assert not state.session.in_transaction()
    with Session(engine) as session:
        doctor = session.exec(
            select(Doctor).where(Doctor.id == doctor_id).with_for_update(nowait=True)
        ).one()
        assert doctor.biography is None
