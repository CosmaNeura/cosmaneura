"""Real canonical routes, transitive source dependencies, and recovery without replay."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest

import app.services.authority as authority
import app.services.authority_conversation_read as history
from app.services.authority_conversation_read import OPERATION

from activemodel.session_manager import SessionManager, get_session
from app.models.ai_visit_note import AIVisitNote
from app.models.authority import (
    AuthorityIdempotencyRecord,
    MountContextSession,
    MountContextToken,
)
from app.models.authority_conversation import (
    CanonicalConversationFinalPhase,
)
from sqlalchemy import text, update
from sqlmodel import Session, select

from tests.factories import AppointmentFactory
from tests.services.provider_question_context_test import (
    _chat,
    _client,
    _headers,
    _read,
)
from tests.services.provider_question_context_test import scope as _scope_fixture
from tests.services.session_prep_authority_test import ISSUER
from tests.services.session_prep_runtime_test import (
    AUTHORITY_SETTINGS,
    _action,
    _work,
)

scope = _scope_fixture


def memory(client, token, turn):
    return client.post(
        "/service/v1/agent/tools/invoke",
        headers=_headers(token, turn, "memory"),
        json={
            "tool_name": OPERATION,
            "tool_version": "1.0.0",
            "subject_ref": None,
            "arguments": {"source_turn_ref": turn[2], "detail": "recent"},
        },
    )


def final(
    client,
    token,
    turn,
    *,
    mem=None,
    prep=None,
    content="Fictional accepted answer.",
    outcome="completed",
    mode="standard",
):
    body = {
        "phase": "final_response",
        "outcome": outcome,
        "response": {"content_type": "text/plain", "content": content},
        "presentation": {"mode": mode, "suppress": [], "human_contact": None},
    }
    if mem is not None:
        body["history_context_ref"] = mem
    if prep is not None:
        body["provider_context_ref"] = prep
    return client.post(
        "/service/v1/agent/conversation-turns/write",
        headers=_headers(token, turn, "final"),
        json=body,
    )


def read(client, token, turn=None):
    return client.post(
        "/service/v1/agent/conversation-turns/read",
        headers={"X-Cosmaneura-Mount-Context": token},
        json={
            "schema_version": "1.0.0",
            "mode": "recent" if turn is None else "turn",
            "client_turn_id": str(turn[0]) if turn else None,
        },
    )


def test_lost_final_response_and_pending_recover_without_writes(scope):
    token, client = scope[3], _client()
    first, pending = _chat(token), _chat(token)
    assert final(client, token, first).status_code == 201
    with get_session() as session:
        before = len(session.exec(select(AuthorityIdempotencyRecord)).all())
    recovered = read(client, token)
    assert recovered.status_code == 200, recovered.text
    result = recovered.json()
    assert [s["state"] for s in result["turns"]] == ["completed", "pending"]
    assert [m["role"] for m in result["history"]["messages"]] == [
        "user",
        "assistant",
        "user",
    ]
    assert (
        read(client, token, first).json()["history"]["messages"][1]["content"]
        == "Fictional accepted answer."
    )
    assert read(client, token, pending).json()["turns"][0]["state"] == "pending"
    with get_session() as session:
        assert len(session.exec(select(AuthorityIdempotencyRecord)).all()) == before
        assert len(session.exec(select(CanonicalConversationFinalPhase)).all()) == 1
    assert recovered.headers["cache-control"] == "no-store"


def test_empty_history_is_immutable_mandatory_and_excludes_late_final(scope):
    token, client = scope[3], _client()
    old, current = _chat(token), _chat(token)
    first = memory(client, token, current)
    assert first.status_code == 200, first.text
    ref = first.json()["tool_call_ref"]
    assert first.json()["result"]["messages"] == []
    assert final(client, token, old).status_code == 201
    assert memory(client, token, current).json() == first.json()
    for value in (None, "authority_idem_wrong_reference"):
        denied = final(client, token, current, mem=value)
        assert (
            denied.status_code == 409
            and denied.json()["code"] == "CONVERSATION_CONTEXT_CHANGED"
        )
    assert final(client, token, current, mem=ref).status_code == 201
    replay = final(client, token, current, mem=ref)
    assert (
        replay.status_code == 201 and replay.headers["idempotency-replayed"] == "true"
    )


def test_history_drives_real_followup_and_transitive_source_invalidates_chain(scope):
    token, client = scope[3], _client()
    assert _work() == "ready"
    a = _chat(token)
    source = _read(client, token, a).json()["tool_call_ref"]
    assert (
        final(
            client,
            token,
            a,
            prep=source,
            content="Reviewed fictional goal: walk daily.",
        ).status_code
        == 201
    )
    b = _chat(token)
    b_read = memory(client, token, b)
    assert b_read.status_code == 200, b_read.text
    b_result = b_read.json()
    assert (
        b_result["result"]["messages"][1]["content"]
        == "Reviewed fictional goal: walk daily."
    )
    assert b_result["result"]["source_version_digest"].startswith("sha256_")
    assert (
        final(
            client,
            token,
            b,
            mem=b_result["tool_call_ref"],
            content="Generic follow-up remembers that goal.",
        ).status_code
        == 201
    )
    c = _chat(token)
    c_read = memory(client, token, c).json()
    assert len(c_read["result"]["messages"]) == 4
    with get_session() as session:
        receipts = session.exec(
            select(AuthorityIdempotencyRecord).where(
                AuthorityIdempotencyRecord.operation == OPERATION
            )
        ).all()
        assert all(
            r.response_ref is not None and r.response_ref.endswith("|" + source)
            for r in receipts
        )
        direct = session.get(AuthorityIdempotencyRecord, source)
        assert direct is not None
        assert all(r.retain_until <= direct.retain_until for r in receipts)
        session.execute(
            update(AIVisitNote)
            .where(cast(Any, AIVisitNote.id) == scope[2].id)
            .values(note="Changed fictional note")
        )
        session.commit()
    refused = final(client, token, c, mem=c_read["tool_call_ref"])
    assert (
        refused.status_code == 409
        and refused.json()["code"] == "CONVERSATION_CONTEXT_CHANGED"
    )
    for turn in (a, b):
        projection = read(client, token, turn).json()
        assert projection["turns"][0]["state"] == "stale"
        assert all(m["role"] == "user" for m in projection["history"]["messages"])
    fresh = memory(client, token, _chat(token)).json()
    assert fresh["result"]["messages"] == []


@pytest.mark.parametrize("remove", [False, True])
def test_expired_or_missing_dependency_cannot_be_laundered(scope, remove):
    token, client = scope[3], _client()
    assert _work() == "ready"
    a = _chat(token)
    ref = _read(client, token, a).json()["tool_call_ref"]
    assert final(client, token, a, prep=ref).status_code == 201
    b = _chat(token)
    mem = memory(client, token, b).json()["tool_call_ref"]
    assert final(client, token, b, mem=mem).status_code == 201
    with get_session() as session:
        receipt = session.get(AuthorityIdempotencyRecord, ref)
        assert receipt is not None
        if remove:
            session.delete(receipt)
        else:
            receipt.retain_until = datetime.now(UTC) - timedelta(seconds=1)
            session.add(receipt)
        session.commit()
    assert [s["state"] for s in read(client, token).json()["turns"]] == [
        "stale",
        "stale",
    ]


def test_fresh_mount_recovers_expired_session_but_revocation_hides_it(scope):
    doctor, _, _, token, _ = scope
    client, turn = _client(), _chat(token)
    assert final(client, token, turn).status_code == 201
    # Match the authority clock fixed by scope; wall time can be much later
    # when this test executes near the end of the full suite.
    expired_at = authority.datetime.now(UTC) - timedelta(seconds=1)
    with get_session() as session:
        old = session.exec(select(MountContextSession)).one()
        old_id = old.id
        old.absolute_expires_at = expired_at
        session.add(old)
        session.execute(update(MountContextToken).values(expires_at=expired_at))
        session.commit()
    assert read(client, token).status_code == 401
    fresh = authority.exchange_context_token(
        clerk_issuer=ISSUER,
        clerk_subject=doctor.clerk_id,
        requested_surface="provider_assistant",
        record_ref=str(scope[1].id),
        settings=AUTHORITY_SETTINGS,
    ).context_token
    assert read(client, fresh, turn).json()["turns"][0]["state"] == "completed"
    with get_session() as session:
        session.execute(
            update(MountContextSession)
            .where(cast(Any, MountContextSession.id) == old_id)
            .values(status="revoked", revoked_at=datetime.now(UTC))
        )
        session.commit()
    assert read(client, fresh, turn).status_code == 404


def test_exact_encounter_workload_and_ui_action_scope(scope):
    doctor, target, _, token, _ = scope
    client, turn = _client(), _chat(token)
    assert final(client, token, turn).status_code == 201
    assert read(_client(str(uuid4())), token, turn).status_code == 404
    second = AppointmentFactory.build(
        doctor_id=doctor.id, patient_id=target.patient_id
    ).save()
    other = authority.exchange_context_token(
        clerk_issuer=ISSUER,
        clerk_subject=doctor.clerk_id,
        requested_surface="provider_assistant",
        record_ref=str(second.id),
        settings=AUTHORITY_SETTINGS,
    ).context_token
    assert read(client, other, turn).status_code == 404
    assert read(client, other).json()["history"]["messages"] == []
    assert read(client, token, _action(token)).status_code == 404
    assert read(client, token, (uuid4(),)).status_code == 404


@pytest.mark.parametrize(
    "outcome,mode",
    [("refused", "standard"), ("unavailable", "standard"), ("completed", "emergency")],
)
def test_nonordinary_final_displays_but_never_enters_model_memory(scope, outcome, mode):
    token, client = scope[3], _client()
    turn = _chat(token)
    assert final(client, token, turn, outcome=outcome, mode=mode).status_code == 201
    result = read(client, token, turn).json()
    assert result["history"]["messages"][1]["presentation"]["mode"] == mode
    assert result["turns"][0]["outcome"] == outcome
    assert memory(client, token, _chat(token)).json()["result"]["messages"] == []


def test_six_pair_and_utf8_bound_preserve_whole_answers(scope):
    token, client = scope[3], _client()
    for i in range(8):
        assert (
            final(
                client, token, _chat(token), content=str(i) + "😀" * 19999
            ).status_code
            == 201
        )
    result = read(client, token).json()
    assert 1 <= len(result["turns"]) <= 6
    assert (
        len(
            json.dumps(
                result["history"]["messages"], ensure_ascii=False, separators=(",", ":")
            ).encode()
        )
        <= history.MAX_MESSAGES_BYTES
    )
    assert all(
        len(m["content"]) == 20000
        for m in result["history"]["messages"]
        if m["role"] == "assistant"
    )
    mem = memory(client, token, _chat(token)).json()["result"]["messages"]
    assert len(mem) % 2 == 0 and len(mem) <= 12
    assert (
        len(json.dumps(mem, ensure_ascii=False, separators=(",", ":")).encode())
        <= history.MAX_MESSAGES_BYTES
    )


@pytest.mark.committed_database
def test_busy_source_refuses_final_and_rolls_back_without_deadlock(scope):
    token, client = scope[3], _client()
    assert _work() == "ready"
    a = _chat(token)
    ref = _read(client, token, a).json()["tool_call_ref"]
    assert final(client, token, a, prep=ref).status_code == 201
    b = _chat(token)
    mem = memory(client, token, b).json()["tool_call_ref"]
    engine = SessionManager.get_instance().get_engine()
    holding, release = threading.Event(), threading.Event()

    def writer():
        with Session(engine) as db:
            db.execute(
                text("SELECT id FROM appointment WHERE id = :id FOR UPDATE"),
                {"id": scope[2].appointment_id.uuid},
            )
            holding.set()
            assert release.wait(5)
            db.commit()

    with ThreadPoolExecutor() as pool:
        work = pool.submit(writer)
        assert holding.wait(5)
        response = final(client, token, b, mem=mem)
        release.set()
        work.result(timeout=5)
    assert (
        response.status_code == 409
        and response.json()["code"] == "CONVERSATION_CONTEXT_CHANGED"
    )
    with get_session() as session:
        assert (
            session.exec(
                select(CanonicalConversationFinalPhase).where(
                    CanonicalConversationFinalPhase.turn_id == b[2]
                )
            ).one_or_none()
            is None
        )
    assert final(client, token, b, mem=mem).status_code == 201


@pytest.mark.parametrize(
    "extra", [{"patient_id": "other"}, {"history": []}, {"thread_ref": "other"}]
)
def test_browser_selectors_rejected(scope, extra):
    body = {
        "schema_version": "1.0.0",
        "mode": "recent",
        "client_turn_id": None,
        **extra,
    }
    assert (
        _client()
        .post(
            "/service/v1/agent/conversation-turns/read",
            headers={"X-Cosmaneura-Mount-Context": scope[3]},
            json=body,
        )
        .status_code
        == 422
    )


def test_current_cohort_revocation_denies_hydration_and_memory(scope, monkeypatch):
    import app.configuration.session_prep as configuration
    from app.configuration.session_prep import SessionPrepProviderSettings

    token, client = scope[3], _client()
    turn = _chat(token)
    monkeypatch.setattr(
        configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        SessionPrepProviderSettings(False, frozenset()),
    )
    assert read(client, token).status_code == 403
    assert memory(client, token, turn).status_code in {403, 503}


def test_changed_canonical_final_is_never_displayed_as_verified(scope):
    token, client = scope[3], _client()
    turn = _chat(token)
    assert final(client, token, turn).status_code == 201
    with get_session() as session:
        stored = session.exec(select(CanonicalConversationFinalPhase)).one()
        # Simulate damaged retained data with the test-owner connection;
        # ordinary SQL updates are also protected by the append-only trigger.
        session.execute(
            text(
                "ALTER TABLE canonical_conversation_final_phase DISABLE TRIGGER "
                "canonical_conversation_final_phase_append_only"
            )
        )
        try:
            session.execute(
                update(CanonicalConversationFinalPhase)
                .where(cast(Any, CanonicalConversationFinalPhase.id) == stored.id)
                .values(content="Tampered fictional answer")
            )
        finally:
            session.execute(
                text(
                    "ALTER TABLE canonical_conversation_final_phase ENABLE TRIGGER "
                    "canonical_conversation_final_phase_append_only"
                )
            )
        session.commit()
    result = read(client, token, turn).json()
    assert result["turns"][0]["state"] == "stale"
    assert len(result["history"]["messages"]) == 1


def test_receiver_contract_models_equal_mirrored_schemas_and_fixtures():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    schema_root = root / "contracts/schemas/governed-conversation-v1"
    definitions = json.loads((schema_root / "schema.json").read_text())["$defs"]
    for name, model in (
        ("read-request", history.ConversationReadRequest),
        ("read-response", history.ConversationReadResponse),
        ("context-request", history.HistoryInvocation),
        ("context-response", history.HistoryResponse),
    ):
        generated = model.model_json_schema(ref_template="schema.json#/$defs/{model}")
        for key, value in generated.pop("$defs", {}).items():
            assert definitions[key] == value
        assert definitions[name] == generated
        fixture = json.loads(
            (
                root / "contracts/fixtures/governed-conversation-v1" / f"{name}.json"
            ).read_text()
        )
        model.model_validate(fixture)


@pytest.mark.parametrize(
    "query,content",
    [
        ("?patient_id=foreign", None),
        (
            "",
            '{"schema_version":"1.0.0","mode":"recent","mode":"turn","client_turn_id":null}',
        ),
        ("", " " * 8193),
    ],
)
def test_transport_selector_duplicate_and_oversize_are_rejected(scope, query, content):
    response = _client().post(
        "/service/v1/agent/conversation-turns/read" + query,
        headers={
            "X-Cosmaneura-Mount-Context": scope[3],
            "content-type": "application/json",
        },
        content=content
        or '{"schema_version":"1.0.0","mode":"recent","client_turn_id":null}',
    )
    assert response.status_code == 422


def test_identical_text_cannot_replace_revoked_snapshot_membership(scope):
    doctor, target, _, token, _ = scope
    client = _client()
    assert final(client, token, _chat(token)).status_code == 201
    alternate = authority.exchange_context_token(
        clerk_issuer=ISSUER,
        clerk_subject=doctor.clerk_id,
        requested_surface="provider_assistant",
        record_ref=str(target.id),
        settings=AUTHORITY_SETTINGS,
    ).context_token
    assert final(client, alternate, _chat(alternate)).status_code == 201
    for _ in range(5):
        assert final(client, token, _chat(token)).status_code == 201
    current = _chat(token)
    snapshot = memory(client, token, current).json()
    assert len(snapshot["result"]["messages"]) == 12
    with get_session() as session:
        alternate_token = session.exec(
            select(MountContextToken).where(
                MountContextToken.token_hash == authority._token_hash(alternate)
            )
        ).one()
        old = session.get(MountContextSession, alternate_token.session_id)
        assert old is not None
        old.status, old.revoked_at = "revoked", datetime.now(UTC)
        session.add(old)
        session.commit()
    # Six pairs of identical text remain, but one different prior turn has filled
    # the revoked member's place. Snapshot acceptance must bind identity too.
    assert len(read(client, token).json()["turns"]) == 6
    response = final(client, token, current, mem=snapshot["tool_call_ref"])
    assert (
        response.status_code == 409
        and response.json()["code"] == "CONVERSATION_CONTEXT_CHANGED"
    )


@pytest.mark.parametrize(
    "supplied", ["trace_fictional_history_20260928", "short", None]
)
def test_successful_history_read_echoes_one_normalized_request_trace(scope, supplied):
    headers = {"X-Cosmaneura-Mount-Context": scope[3]}
    if supplied is not None:
        headers["X-Trace-ID"] = supplied
    response = _client().post(
        "/service/v1/agent/conversation-turns/read",
        headers=headers,
        json={"schema_version": "1.0.0", "mode": "recent", "client_turn_id": None},
    )
    assert response.status_code == 200
    traces = response.headers.get_list("x-trace-id")
    assert len(traces) == 1
    if supplied is not None and len(supplied) >= 16:
        assert traces == [supplied]
    else:
        assert traces[0].startswith("trace_") and len(traces[0]) == 38
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"
