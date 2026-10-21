"""Dedicated receiver waits on real independently connected owned synthetic PG."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from queue import Queue
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services import authority
from app.services import provider_chat_admission as admission
from app.services import provider_database_deadline as deadline
from app.services.authority_conversation import write_conversation_turn

from app.models.authority import (
    AuthorityActorExternalIdentity,
    InteractionLineage,
    MountContextSession,
)
from app.models.doctor import Doctor
from sqlalchemy import text
from sqlmodel import Session, select

from tests.authority_kernel.authority_interaction_expiry_test import (
    WORKLOAD,
    _await_lineage_block,
    capture_engine,
    owned_record,
)
from tests.authority_kernel.provider_chat_admission_test import (
    FINAL,
    OTHER_WORKLOADS,
    USER,
    phase_counts,
)
from tests.authority_kernel.scribe_capture_scope_test import SETTINGS

assert capture_engine and owned_record


@pytest.fixture(autouse=True)
def dedicated_workload(monkeypatch):
    settings = SimpleNamespace(allowed_client_ids=frozenset({WORKLOAD}))
    monkeypatch.setattr(deadline, "SOVEREIGN_TOOL_AUTH_SETTINGS", settings)
    monkeypatch.setattr(admission, "SOVEREIGN_TOOL_AUTH_SETTINGS", settings)


def arguments(f, *, phase="user_accepted"):
    return dict(
        workload_client_id=WORKLOAD,
        mount_context_token=f.token,
        interaction_ref=f.interaction_ref,
        client_turn_id=f.turn_id,
        idempotency_key="deadline-" + uuid4().hex,
        payload=USER if phase == "user_accepted" else FINAL,
        settings=SETTINGS,
    )


def lock_row(session, f, kind):
    if kind == "lineage":
        return session.exec(
            select(InteractionLineage)
            .where(InteractionLineage.interaction_ref == f.interaction_ref)
            .with_for_update()
        ).one()
    mount = session.get(MountContextSession, f.mount_id)
    assert mount is not None
    identity = session.get(AuthorityActorExternalIdentity, mount.source_identity_id)
    assert identity is not None
    return session.exec(
        select(Doctor).where(Doctor.clerk_id == identity.subject).with_for_update()
    ).one()


def probe_advisory(f):
    with Session(f.engine) as session:
        result = admission.begin_admission(
            session,
            workload_client_id=WORKLOAD,
            mount_context_token=f.token,
            payload=USER,
        )
        assert result is not None
        obtained = result.obtained
        session.rollback()
        return obtained


@pytest.mark.parametrize("kind", ["doctor", "lineage"])
@pytest.mark.parametrize("phase", ["user_accepted", "final_response"])
def test_real_lock_wait_denies_boundedly_rolls_back_and_releases_admission(
    owned_record, kind, phase
):
    f = owned_record
    call = arguments(f, phase=phase)
    if phase == "final_response":
        with Session(f.engine) as setup:
            write_conversation_turn(session=setup, **arguments(f))
    pids = Queue()

    def writer():
        with Session(f.engine) as session:
            pids.put(session.execute(text("SELECT pg_backend_pid()")).scalar_one())
            started = monotonic()
            with pytest.raises(deadline.ProviderDatabaseDeadlineExceeded) as error:
                write_conversation_turn(session=session, **call)
            assert not session.in_transaction()
            assert not error.value.args
            return monotonic() - started

    with Session(f.engine) as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        blocker_pid = blocker.execute(text("SELECT pg_backend_pid()")).scalar_one()
        lock_row(blocker, f, kind)
        future = pool.submit(writer)
        _await_lineage_block(f.engine, pids.get(timeout=3), blocker_pid)
        if phase == "user_accepted":
            assert not probe_advisory(f)
        elapsed = future.result(timeout=4)
        assert 0.7 <= elapsed < 2.5
        assert probe_advisory(f)
        assert phase_counts(f) == ((1, 0) if phase == "final_response" else (0, 0))
        blocker.rollback()
    with Session(f.engine) as session:
        receipt = write_conversation_turn(session=session, **call)
    assert receipt.state == ("completed" if phase == "final_response" else "accepted")
    assert phase_counts(f) == ((1, 1) if phase == "final_response" else (1, 0))


@pytest.mark.parametrize(
    "operation", ["bootstrap_mount_context", "resolve_mount_context"]
)
def test_context_wait_times_out_without_new_lineage_and_valid_retry_still_resolves(
    owned_record,
    operation,
):
    f = owned_record
    candidate = uuid4()
    pids = Queue()
    call = dict(
        workload_client_id=WORKLOAD,
        mount_context_token=f.token,
        settings=SETTINGS,
    )
    if operation == "resolve_mount_context":
        call["client_turn_id"] = candidate
    resolve = getattr(authority, operation)

    def writer():
        with Session(f.engine) as session:
            pids.put(session.execute(text("SELECT pg_backend_pid()")).scalar_one())
            started = monotonic()
            with pytest.raises(deadline.ProviderDatabaseDeadlineExceeded):
                resolve(session=session, **call)
            assert not session.in_transaction()
            return monotonic() - started

    with Session(f.engine) as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        blocker_pid = blocker.execute(text("SELECT pg_backend_pid()")).scalar_one()
        lock_row(blocker, f, "doctor")
        future = pool.submit(writer)
        _await_lineage_block(f.engine, pids.get(timeout=3), blocker_pid)
        assert future.result(timeout=4) < 2.5
        with Session(f.engine) as check:
            assert (
                check.exec(
                    select(InteractionLineage).where(
                        InteractionLineage.client_turn_id == candidate
                    )
                ).first()
                is None
            )
        blocker.rollback()
    with Session(f.engine) as session:
        result = resolve(session=session, **call)
        if operation == "resolve_mount_context":
            assert not result.reused
        else:
            assert result.surface == "provider_assistant"
            assert result.session_id == f.mount_id


@pytest.mark.parametrize("kind", ["doctor", "lineage"])
def test_real_short_lock_wait_preserves_current_authority_and_succeeds(
    owned_record, kind
):
    f = owned_record
    pids = Queue()
    call = arguments(f)

    def writer():
        with Session(f.engine) as session:
            pids.put(session.execute(text("SELECT pg_backend_pid()")).scalar_one())
            return write_conversation_turn(session=session, **call)

    with Session(f.engine) as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        blocker_pid = blocker.execute(text("SELECT pg_backend_pid()")).scalar_one()
        lock_row(blocker, f, kind)
        future = pool.submit(writer)
        _await_lineage_block(f.engine, pids.get(timeout=3), blocker_pid)
        assert not future.done()
        blocker.rollback()
        assert future.result(timeout=3).state == "accepted"
    assert phase_counts(f) == (1, 0)


@deadline.provider_database_deadline
def slow_statement(*, session, workload_client_id, settings=SETTINGS):
    session.execute(text("SELECT pg_sleep(4)"))


def test_actual_statement_timeout_is_bounded_and_transaction_settings_reset(
    owned_record,
):
    with Session(owned_record.engine) as session:
        before = (
            session.execute(text("SHOW lock_timeout")).scalar_one(),
            session.execute(text("SHOW statement_timeout")).scalar_one(),
        )
        session.commit()
        started = monotonic()
        with pytest.raises(deadline.ProviderDatabaseDeadlineExceeded):
            slow_statement(
                session=session, workload_client_id=WORKLOAD, settings=SETTINGS
            )
        assert 2.5 <= monotonic() - started < 4.0
        assert not session.in_transaction()
        after = (
            session.execute(text("SHOW lock_timeout")).scalar_one(),
            session.execute(text("SHOW statement_timeout")).scalar_one(),
        )
        assert after == before


def test_tighter_existing_limits_are_not_increased_and_commit_restores_defaults(
    owned_record,
):
    with Session(owned_record.engine) as session:
        before = (
            session.execute(text("SHOW lock_timeout")).scalar_one(),
            session.execute(text("SHOW statement_timeout")).scalar_one(),
        )
        session.execute(text("SET LOCAL lock_timeout='25ms'"))
        session.execute(text("SET LOCAL statement_timeout='100ms'"))
        deadline.install_provider_database_limits(
            session=session, workload_client_id=WORKLOAD, settings=SETTINGS
        )
        assert session.execute(text("SHOW lock_timeout")).scalar_one() == "25ms"
        assert session.execute(text("SHOW statement_timeout")).scalar_one() == "100ms"
        session.commit()
        after = (
            session.execute(text("SHOW lock_timeout")).scalar_one(),
            session.execute(text("SHOW statement_timeout")).scalar_one(),
        )
        assert after == before


@pytest.mark.parametrize("workload", OTHER_WORKLOADS)
def test_other_receivers_do_not_change_transaction_timeouts(owned_record, workload):
    with Session(owned_record.engine) as session:
        before = (
            session.execute(text("SHOW lock_timeout")).scalar_one(),
            session.execute(text("SHOW statement_timeout")).scalar_one(),
        )
        deadline.install_provider_database_limits(
            session=session, workload_client_id=workload, settings=SETTINGS
        )
        assert (
            session.execute(text("SHOW lock_timeout")).scalar_one(),
            session.execute(text("SHOW statement_timeout")).scalar_one(),
        ) == before


def test_disabled_authority_does_not_install_limits_or_acquire_connection():
    class SessionMustNotConnect:
        def execute(self, *args, **kwargs):
            pytest.fail("Disabled authority attempted to acquire a connection")

    with pytest.raises(authority.AuthorityKernelDisabledError):
        authority.resolve_mount_context(
            session=SessionMustNotConnect(),
            workload_client_id=WORKLOAD,
            mount_context_token="synthetic-disabled-context",
            client_turn_id=uuid4(),
            settings=replace(SETTINGS, enabled=False),
        )


@pytest.mark.asyncio
async def test_canonical_database_timeout_is_safe_nonretryable_without_receipt(
    monkeypatch,
):
    import json
    from unittest.mock import AsyncMock

    from starlette.requests import Request

    from app.routes import agent

    async def receive():
        return {
            "type": "http.request",
            "body": json.dumps(USER).encode(),
            "more_body": False,
        }

    monkeypatch.setattr(
        agent,
        "_run_agent_database",
        AsyncMock(side_effect=deadline.ProviderDatabaseDeadlineExceeded()),
    )
    result = await agent.write_conversation_turn(
        request=Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/",
                "headers": [(b"content-type", b"application/json")],
            },
            receive,
        ),
        response=agent.Response(),
        principal=SimpleNamespace(client_id=WORKLOAD),
        mount_context_token="synthetic-timeout-context-only",
        interaction_ref="synthetic-timeout-interaction",
        client_turn_id=uuid4(),
        idempotency_key=uuid4().hex,
        settings=SETTINGS,
    )
    body = json.loads(result.body)
    assert result.status_code == 503 and body["code"] == "PLATFORM_UNAVAILABLE"
    assert body["retryable"] is False
    assert not {"phase", "state", "turn_ref"} & set(body)
    assert "synthetic-timeout-context-only" not in result.body.decode()
