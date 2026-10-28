"""Distributed admission on independently connected, unique synthetic PG rows."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from starlette.responses import JSONResponse

from app.routes.authority_errors import agent_problem_response
from app.routes.dependencies.entra_service import EntraServicePrincipal
from app.services import authority
from app.services import provider_chat_admission as admission
from app.services.authority_conversation import (
    IdempotencyConflictError,
    write_conversation_turn,
)

from app.models.authority import MountContextSession
from app.models.authority_conversation import (
    CanonicalConversationFinalPhase,
    CanonicalConversationTurn,
    CanonicalConversationUserPhase,
)
from sqlalchemy import text
from sqlmodel import Session, col, select

from tests.authority_kernel.authority_interaction_expiry_test import (
    WORKLOAD,
    capture_engine,
    owned_record,
)
from tests.authority_kernel.scribe_capture_scope_test import SETTINGS

# Import fixtures explicitly: the actual provider ownership/view-as adapters are
# exercised, not the authority-only SQLite stand-ins.
assert capture_engine and owned_record
OTHER_WORKLOADS = (
    "22222222-2222-4222-8222-222222222222",
    "33333333-3333-4333-8333-333333333333",
)
USER = {
    "phase": "user_accepted",
    "ingress_kind": "chat_text",
    "action_code": None,
    "message": {"content_type": "text/plain", "content": "Synthetic chart question."},
}
FINAL = {
    "phase": "final_response",
    "outcome": "unavailable",
    "response": {"content_type": "text/plain", "content": "Synthetic closed turn."},
    "presentation": {"mode": "standard", "suppress": [], "human_contact": None},
}


@pytest.fixture(autouse=True)
def dedicated_workload(monkeypatch):
    monkeypatch.setattr(
        admission,
        "SOVEREIGN_TOOL_AUTH_SETTINGS",
        SimpleNamespace(allowed_client_ids=frozenset({WORKLOAD})),
    )


def turn(f, workload=WORKLOAD):
    turn_id = uuid4()
    with Session(f.engine) as session:
        resolved = authority.resolve_mount_context(
            session=session,
            workload_client_id=workload,
            mount_context_token=f.token,
            client_turn_id=turn_id,
            settings=SETTINGS,
        )
    return SimpleNamespace(id=turn_id, ref=resolved.interaction_ref, key=uuid4().hex)


def write(f, t, payload=USER, *, workload=WORKLOAD, session=None, key=None):
    if session is None:
        with Session(f.engine) as managed:
            managed.execute(text("SET LOCAL lock_timeout = '6s'"))
            managed.execute(text("SET LOCAL statement_timeout = '8s'"))
            return write(f, t, payload, workload=workload, session=managed, key=key)
    return write_conversation_turn(
        session=session,
        workload_client_id=workload,
        mount_context_token=f.token,
        interaction_ref=t.ref,
        client_turn_id=t.id,
        idempotency_key=key
        or (t.key if payload["phase"] == "user_accepted" else t.key + "-final"),
        payload=payload,
        settings=SETTINGS,
    )


def phase_counts(f):
    with Session(f.engine) as session:
        mount = session.get(MountContextSession, f.mount_id)
        assert mount is not None
        turns = session.exec(
            select(CanonicalConversationTurn).where(
                CanonicalConversationTurn.actor_id == mount.actor_id,
                CanonicalConversationTurn.workload_client_id == WORKLOAD,
            )
        ).all()
        ids = [row.id for row in turns]
        users = session.exec(
            select(CanonicalConversationUserPhase).where(
                col(CanonicalConversationUserPhase.turn_id).in_(ids)
            )
        ).all()
        finals = session.exec(
            select(CanonicalConversationFinalPhase).where(
                col(CanonicalConversationFinalPhase.turn_id).in_(ids)
            )
        ).all()
        return len(users), len(finals)


def race(f, turns, monkeypatch):
    """Both independent connections attempt the same actor advisory lock first."""
    barrier = Barrier(2)
    begin = admission.begin_admission
    pids = []

    def simultaneous(*args, **kwargs):
        result = begin(*args, **kwargs)
        pids.append(args[0].execute(text("SELECT pg_backend_pid()")).scalar_one())
        barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(admission, "begin_admission", simultaneous)

    def attempt(t):
        try:
            return write(f, t).state
        except admission.ProviderChatAdmissionLimited:
            return "limited"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, turns))
    monkeypatch.setattr(admission, "begin_admission", begin)
    assert len(set(pids)) == 2
    assert sorted(outcomes) == ["accepted", "limited"]
    return outcomes


def test_inflight_limit_across_independent_connections_and_final_releases_slot(
    owned_record, monkeypatch
):
    f = owned_record
    initial = turn(f)
    write(f, initial)
    contenders = [turn(f), turn(f)]
    outcomes = race(f, contenders, monkeypatch)
    assert phase_counts(f) == (2, 0)
    rejected = contenders[outcomes.index("limited")]
    with pytest.raises(admission.ProviderChatAdmissionLimited) as limited:
        write(f, rejected)
    assert limited.value.retry_after_seconds == 5
    assert phase_counts(f) == (2, 0)
    write(f, initial, FINAL)
    assert write(f, rejected).state == "accepted"
    assert phase_counts(f) == (3, 1)


def test_burst_limit_race_exact_replay_and_no_rejected_user_phase(
    owned_record, monkeypatch
):
    f = owned_record
    completed = []
    for _ in range(9):
        t = turn(f)
        write(f, t)
        write(f, t, FINAL)
        completed.append(t)
    contenders = [turn(f), turn(f)]
    outcomes = race(f, contenders, monkeypatch)
    assert phase_counts(f) == (10, 9)
    rejected = contenders[outcomes.index("limited")]
    with pytest.raises(admission.ProviderChatAdmissionLimited) as limited:
        write(f, rejected)
    assert 1 <= limited.value.retry_after_seconds <= 60
    assert write(f, completed[0]).replayed
    assert write(f, completed[0], FINAL).replayed
    altered = {
        **USER,
        "message": {
            "content_type": "text/plain",
            "content": "Different synthetic question.",
        },
    }
    with pytest.raises(IdempotencyConflictError):
        write(f, completed[0], altered)
    assert phase_counts(f) == (10, 9)


def test_busy_advisory_cannot_replace_exact_replay_or_revocation_denial(owned_record):
    f = owned_record
    accepted, candidate = turn(f), turn(f)
    write(f, accepted)
    with Session(f.engine) as blocker:
        attempt = admission.begin_admission(
            blocker,
            workload_client_id=WORKLOAD,
            mount_context_token=f.token,
            payload=USER,
        )
        assert attempt is not None and attempt.obtained
        assert write(f, accepted).replayed
        with pytest.raises(admission.ProviderChatAdmissionLimited) as limited:
            write(f, candidate)
        assert limited.value.retry_after_seconds == 1
        with Session(f.engine) as revoker:
            mount = revoker.get(MountContextSession, f.mount_id)
            assert mount is not None
            mount.status = "revoked"
            revoker.add(mount)
            revoker.commit()
        with pytest.raises(authority.ContextTokenInvalidError):
            write(f, candidate)
        blocker.rollback()
    assert phase_counts(f) == (1, 0)


def test_reservation_expiry_and_late_final_fence_use_database_time(
    owned_record, monkeypatch
):
    f = owned_record
    first, second, third = turn(f), turn(f), turn(f)
    accepted_at = write(f, first).recorded_at
    write(f, second)
    clock = admission._database_now
    monkeypatch.setattr(
        admission, "_database_now", lambda session: accepted_at + timedelta(seconds=196)
    )
    assert write(f, third).state == "accepted"
    with pytest.raises(admission.ProviderChatWindowExpired):
        write(f, first, FINAL)
    assert phase_counts(f) == (3, 0)
    monkeypatch.setattr(admission, "_database_now", clock)


@pytest.mark.parametrize("workload", OTHER_WORKLOADS)
def test_other_configured_receivers_bypass_provider_quota(owned_record, workload):
    f = owned_record
    write(f, turn(f))
    write(f, turn(f))
    assert write(f, turn(f, workload), workload=workload).state == "accepted"
    assert phase_counts(f) == (2, 0)


def test_problem_retry_metadata_is_bounded_and_content_free():
    response = agent_problem_response(
        status_code=429,
        code="RATE_LIMITED",
        title="Rate limited",
        detail="Please wait briefly.",
        retryable=True,
        retry_after_seconds=5,
    )
    import json

    assert response.headers["Retry-After"] == "5"
    assert response.headers["Cache-Control"] == "no-store"
    assert json.loads(bytes(response.body))["retry_after_seconds"] == 5
    for invalid in (True, 0, 61, "5"):
        with pytest.raises(ValueError):
            agent_problem_response(
                status_code=429,
                code="RATE_LIMITED",
                title="Rate limited",
                detail="Please wait briefly.",
                retry_after_seconds=cast(Any, invalid),
            )


def test_public_chat_remains_outside_dedicated_provider_admission(
    authority_session, monkeypatch
):
    from tests.authority_kernel.conversation_kernel_test import (
        _public_context,
        _user_payload,
        _write,
    )

    def forbidden(_session):
        pytest.fail("Public chat must not use the provider database clock or quota")

    monkeypatch.setattr(admission, "_database_now", forbidden)
    token, turn_id, ref = _public_context(authority_session)
    arguments: dict[str, Any] = dict(
        context_token=token,
        client_turn_id=turn_id,
        interaction_ref=ref,
    )
    # The canonical receiver already refuses public contexts; admission must
    # preserve that authority denial, without a provider rate decision.
    with pytest.raises(authority.AuthorityRequestDeniedError):
        _write(
            authority_session,
            **arguments,
            idempotency_key=uuid4().hex,
            payload=_user_payload(),
        )


@pytest.mark.asyncio
async def test_receiver_returns_only_bounded_rate_metadata(monkeypatch):
    import json

    from starlette.requests import Request

    from app.routes import agent

    sent = False

    async def receive():
        nonlocal sent
        assert not sent
        sent = True
        return {
            "type": "http.request",
            "body": json.dumps(USER).encode(),
            "more_body": False,
        }

    async def limited(*args, **kwargs):
        raise admission.ProviderChatAdmissionLimited(5)

    monkeypatch.setattr(agent, "_run_agent_database", limited)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )
    result = await agent.write_conversation_turn(
        request=request,
        response=agent.Response(),
        principal=EntraServicePrincipal(client_id=WORKLOAD),
        mount_context_token="synthetic-context-token-only",
        interaction_ref="synthetic-interaction-only",
        client_turn_id=uuid4(),
        idempotency_key=uuid4().hex,
        settings=SETTINGS,
    )
    assert isinstance(result, JSONResponse)
    body = json.loads(bytes(result.body))
    assert result.status_code == 429
    assert (body["code"], body["retryable"], body["retry_after_seconds"]) == (
        "RATE_LIMITED",
        True,
        5,
    )
    assert result.headers["Retry-After"] == "5"
    assert "synthetic-context-token-only" not in bytes(result.body).decode()
