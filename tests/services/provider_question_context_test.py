"""Real receiver/final-writer regressions with fictional reviewed-note fixtures."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.configuration.session_prep as configuration
import app.services.authority as authority
import app.services.provider_question_context as context_service
from app.configuration.session_prep import SessionPrepProviderSettings
from app.routes.agent import agent_api_app, get_agent_authority_settings
from app.routes.dependencies.entra_service import (
    EntraServicePrincipal,
    authenticate_agent_api_request,
    authenticate_sovereign_tool_request,
)
from app.services.authority_conversation import write_conversation_turn
from app.services.provider_question_context import OPERATION, ProviderQuestionResponse
from app.services.session_prep import SessionPrepScopeDenied
from app.services.session_prep_note_sources import (
    finalize_note_for_session_prep,
    note_content_sha256,
)

from activemodel.session_manager import SessionManager, get_session
from app.models.ai_visit_note import AIVisitNote
from app.models.appointment import Appointment
from app.models.authority import AuthorityAuditEvent, AuthorityIdempotencyRecord
from app.models.authority_conversation import CanonicalConversationFinalPhase
from app.models.patient import Patient
from app.models.session_prep_runtime import SessionPrepArtifact
from sqlalchemy import text, update
from sqlmodel import Session, select

from tests.services.session_prep_note_sources_test import _note
from tests.services.session_prep_runtime_test import (
    AUTHORITY_SETTINGS,
    NOW,
    WORKLOAD,
    _action,
    _work,
)
from tests.services.session_prep_runtime_test import (
    _read as read_prep,
)
from tests.services.session_prep_runtime_test import (
    scope as _scope_fixture,
)

ROOT = Path(__file__).resolve().parents[2]
scope = _scope_fixture


def _chat(token):
    turn_id = uuid4()
    resolved = authority.resolve_mount_context(
        workload_client_id=WORKLOAD,
        mount_context_token=token,
        client_turn_id=turn_id,
        settings=AUTHORITY_SETTINGS,
    )
    receipt = write_conversation_turn(
        workload_client_id=WORKLOAD,
        mount_context_token=token,
        interaction_ref=resolved.interaction_ref,
        client_turn_id=turn_id,
        idempotency_key=f"chat-accepted-{turn_id}",
        settings=AUTHORITY_SETTINGS,
        payload={
            "phase": "user_accepted",
            "ingress_kind": "chat_text",
            "action_code": None,
            "message": {
                "content_type": "text/plain",
                "content": "What synthetic goals were reviewed last visit?",
            },
        },
    )
    return turn_id, resolved.interaction_ref, receipt.turn_ref


def _client(workload=WORKLOAD):
    app = FastAPI()
    app.include_router(agent_api_app)
    for dependency in (
        authenticate_agent_api_request,
        authenticate_sovereign_tool_request,
    ):
        app.dependency_overrides[dependency] = lambda: EntraServicePrincipal(
            client_id=workload
        )
    app.dependency_overrides[get_agent_authority_settings] = lambda: AUTHORITY_SETTINGS
    return TestClient(app)


def _headers(token, turn, operation="read"):
    return {
        "X-Cosmaneura-Mount-Context": token,
        "X-Cosmaneura-Interaction-Ref": turn[1],
        "X-Cosmaneura-Client-Turn-ID": str(turn[0]),
        "Idempotency-Key": f"question-{operation}-{turn[0]}",
    }


def _body(turn):
    return {
        "tool_name": OPERATION,
        "tool_version": "1.0.0",
        "subject_ref": None,
        "arguments": {
            "source_turn_ref": turn[2],
            "purpose": "provider_question",
            "detail": "prep_summary",
        },
    }


def _read(client, token, turn):
    return client.post(
        "/service/v1/agent/tools/invoke",
        headers=_headers(token, turn),
        json=_body(turn),
    )


def _final(client, token, turn, reference=None, outcome="completed"):
    body = {
        "phase": "final_response",
        "outcome": outcome,
        "response": {
            "content_type": "text/plain",
            "content": "Fictional bounded answer.",
        },
        "presentation": {"mode": "standard", "suppress": [], "human_contact": None},
    }
    if reference is not None:
        body["provider_context_ref"] = reference
    return client.post(
        "/service/v1/agent/conversation-turns/write",
        headers=_headers(token, turn, "final"),
        json=body,
    )


def _context_receipt():
    with get_session() as session:
        return session.exec(
            select(AuthorityIdempotencyRecord).where(
                AuthorityIdempotencyRecord.operation == OPERATION
            )
        ).one()


def test_ready_context_is_committed_scoped_and_finalized_through_actual_routes(scope):
    _, _, _, token, _ = scope
    assert _work() == "ready"
    turn, client = _chat(token), _client()
    first = _read(client, token, turn)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["result"]["state"] == "ready"
    assert "synthetic goals" in body["result"]["prepared_brief"]["recap"]["text"]
    assert body["result"]["prepared_brief"]["patient_reported"] is None
    assert all(
        p["included"] and p["author_class"] == "clinician"
        for p in body["result"]["provenance"]
    )
    assert first.headers["cache-control"] == "no-store"
    assert first.headers["idempotency-replayed"] == "false"
    receipt = _context_receipt()
    assert str(receipt.id) == body["tool_call_ref"]
    assert receipt.response_ref is not None
    assert receipt.response_ref.startswith("ready:prep_artifact_")
    assert (
        AuthorityAuditEvent.where(AuthorityAuditEvent.event_type == OPERATION).count()
        == 1
    )
    again = _read(client, token, turn)
    assert again.json() == body
    assert again.headers["idempotency-replayed"] == "true"
    final = _final(client, token, turn, body["tool_call_ref"])
    assert final.status_code == 201, final.text
    assert CanonicalConversationFinalPhase.count() == 1
    replay = _final(client, token, turn, body["tool_call_ref"])
    assert replay.status_code == 201
    assert replay.headers["idempotency-replayed"] == "true"


def test_ordinary_legacy_question_without_context_remains_compatible(scope):
    _, _, _, token, _ = scope
    client, turn = _client(), _chat(token)
    assert _final(client, token, turn).status_code == 201
    replay = _final(client, token, turn)
    assert replay.status_code == 201
    assert replay.headers["idempotency-replayed"] == "true"
    assert _read(client, token, turn).status_code == 403


@pytest.mark.parametrize(
    "reference", [None, "authority_idem_01j00000000000000000000000"]
)
def test_context_read_cannot_be_dropped_or_substituted_at_final_acceptance(
    scope, reference
):
    _, _, _, token, _ = scope
    assert _work() == "ready"
    client, turn = _client(), _chat(token)
    ready = _read(client, token, turn).json()
    rejected = _final(client, token, turn, reference)
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["code"] == "PROVIDER_CONTEXT_CHANGED"
    assert CanonicalConversationFinalPhase.count() == 0
    assert _final(client, token, turn, ready["tool_call_ref"]).status_code == 201


def test_unknown_context_ref_cannot_be_added_to_a_legacy_turn(scope):
    _, _, _, token, _ = scope
    response = _final(
        _client(), token, _chat(token), "authority_idem_01j00000000000000000000000"
    )
    assert response.status_code == 409
    assert CanonicalConversationFinalPhase.count() == 0


def test_pending_receipt_is_immutable_after_publication_and_only_accepts_unavailable(
    scope,
):
    _, _, _, token, _ = scope
    client, turn = _client(), _chat(token)
    pending = _read(client, token, turn).json()
    assert pending["result"]["state"] == "pending"
    assert pending["result"]["prepared_brief"] is None
    digest = _context_receipt().request_digest
    assert _work() == "ready"
    assert _read(client, token, turn).json() == pending
    assert _context_receipt().request_digest == digest
    assert _final(client, token, turn, pending["tool_call_ref"]).status_code == 409
    assert (
        _final(client, token, turn, pending["tool_call_ref"], "unavailable").status_code
        == 201
    )
    assert _read(client, token, _chat(token)).json()["result"]["state"] == "ready"


def test_no_reviewed_sources_returns_no_content_and_never_starts_generation(scope):
    _, _, note, token, _ = scope
    note.note = {"Plan": "Unreviewed fictional edit."}
    note.save()
    client, turn = _client(), _chat(token)
    response = _read(client, token, turn)
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["state"] == "unavailable"
    assert result["reason_code"] == "no_eligible_sources"
    assert result["prepared_brief"] is None and result["provenance"] == []
    assert "Unreviewed fictional edit" not in response.text
    assert SessionPrepArtifact.count() == 0
    assert (
        _final(
            client, token, turn, response.json()["tool_call_ref"], "unavailable"
        ).status_code
        == 201
    )


@pytest.mark.parametrize("already_final", [False, True])
def test_source_edit_while_model_is_running_rejects_final_and_final_replay(
    scope, already_final
):
    _, _, note, token, _ = scope
    assert _work() == "ready"
    client, turn = _client(), _chat(token)
    ready = _read(client, token, turn).json()
    if already_final:
        assert _final(client, token, turn, ready["tool_call_ref"]).status_code == 201
    # Independent committed source edit after the read, before final acceptance.
    note.note = {"Plan": "Corrected fictional goals require fresh review."}
    note.save()
    rejected = _final(client, token, turn, ready["tool_call_ref"])
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["code"] == "PROVIDER_CONTEXT_CHANGED"
    assert CanonicalConversationFinalPhase.count() == int(already_final)
    assert _read(client, token, turn).status_code == 403


def test_cohort_revocation_after_read_denies_final_acceptance(scope, monkeypatch):
    _, _, _, token, _ = scope
    assert _work() == "ready"
    client, turn = _client(), _chat(token)
    ref = _read(client, token, turn).json()["tool_call_ref"]
    monkeypatch.setattr(
        configuration,
        "SESSION_PREP_PROVIDER_SETTINGS",
        SessionPrepProviderSettings(False, frozenset()),
    )
    assert _final(client, token, turn, ref).status_code in {403, 409}
    assert CanonicalConversationFinalPhase.count() == 0


def test_other_turn_and_workload_cannot_read_this_turn(scope):
    _, _, _, token, _ = scope
    first, second, client = _chat(token), _chat(token), _client()
    request = _body(first)
    response = client.post(
        "/service/v1/agent/tools/invoke", headers=_headers(token, second), json=request
    )
    assert response.status_code == 403
    response = _read(_client("22222222-2222-4222-8222-222222222222"), token, first)
    assert response.status_code == 409


def test_prep_ui_action_and_chat_context_remain_distinct(scope):
    _, _, _, token, record = scope
    action, chat, client = _action(token), _chat(token), _client()
    assert _read(client, token, action).status_code == 403
    assert read_prep(token, record, action).response.result.state == "pending"
    with pytest.raises(SessionPrepScopeDenied):
        read_prep(token, record, chat)


@pytest.mark.parametrize(
    "change",
    [
        {"subject_ref": "patient_sentinel_selector"},
        {"ehr_read_access": {}},
        {
            "arguments": {
                "source_turn_ref": "conversation_turn_sentinel",
                "purpose": "full_chart",
                "detail": "prep_summary",
            }
        },
        {"tool_version": "1.0.1"},
    ],
)
def test_closed_receiver_rejects_unrecognized_context_requests(scope, change):
    _, _, _, token, _ = scope
    turn = _chat(token)
    body = {**_body(turn), **change}
    response = _client().post(
        "/service/v1/agent/tools/invoke", headers=_headers(token, turn), json=body
    )
    assert response.status_code in {404, 422}
    assert "sentinel" not in response.text


def test_changed_idempotency_key_cannot_create_another_context_read(scope):
    _, _, _, token, _ = scope
    client, turn = _client(), _chat(token)
    assert _read(client, token, turn).status_code == 200
    response = client.post(
        "/service/v1/agent/tools/invoke",
        headers=_headers(token, turn, "different"),
        json=_body(turn),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert (
        AuthorityAuditEvent.where(AuthorityAuditEvent.event_type == OPERATION).count()
        == 1
    )


@pytest.mark.parametrize("state", ["ready", "pending", "unavailable"])
def test_versioned_response_fixtures_match_closed_runtime_and_schema_fields(state):
    directory = ROOT / "contracts/schemas/provider-question-context-v1"
    response_schema = json.loads((directory / "response.schema.json").read_text())
    result_schema = json.loads((directory / "result.schema.json").read_text())
    body = json.loads(
        (
            ROOT
            / f"contracts/fixtures/provider-question-context-v1/response-{state}.json"
        ).read_text()
    )
    assert set(body) == set(response_schema["required"])
    assert set(body["result"]) == set(result_schema["required"])
    assert response_schema["additionalProperties"] is False
    assert result_schema["additionalProperties"] is False
    assert ProviderQuestionResponse.model_validate(body).result.state == state


@pytest.mark.parametrize(
    "writer_first,new_note,latent_source",
    [
        (True, False, False),
        (True, True, False),
        (False, False, False),
        (False, False, True),
    ],
)
def test_postgres_source_publication_and_final_acceptance_serialize(
    scope, monkeypatch, writer_first, new_note, latent_source
):
    doctor, target, note, token, _ = scope
    selected_note_ref = str(note.id)
    if latent_source:
        patient = Patient.get(id=target.patient_id)
        note = _note(
            doctor, patient, content={"Plan": "Previously reviewed fictional history."}
        )
        with get_session() as session:
            finalize_note_for_session_prep(
                session=session,
                doctor_id=doctor.id,
                note_id=note.id,
                expected_content_sha256=note_content_sha256(note.note),
                observed_at=NOW - timedelta(hours=8),
            )
            session.commit()
        note.superseded_at, note.supersession_reason = NOW, "regenerated"
        note.save()
        AIVisitNote(
            doctor_id=note.doctor_id,
            appointment_id=note.appointment_id,
            doctor_note_schema_id=note.doctor_note_schema_id,
            ai_transcript_id=note.ai_transcript_id,
            note={"Plan": "Unreviewed fictional replacement."},
        ).save()
    assert _work() == "ready"
    client, turn = _client(), _chat(token)
    ref = _read(client, token, turn).json()["tool_call_ref"]
    if latent_source:
        artifact = SessionPrepArtifact.first()
        assert artifact is not None
        assert [s["source_ref"] for s in artifact.source_manifest] == [
            selected_note_ref
        ]
    engine = SessionManager.get_instance().get_engine()
    assert engine.dialect.name == "postgresql"
    held, writer_entered, release = (threading.Event() for _ in range(3))
    pids = {}
    original_result = context_service._current_result

    def result_with_barrier(session, *args):
        result = original_result(session, *args)
        if not writer_first:
            pids["final"] = session.execute(
                text("SELECT pg_backend_pid()")
            ).scalar_one()
            held.set()
            assert release.wait(5)
        return result

    monkeypatch.setattr(context_service, "_current_result", result_with_barrier)

    def publish_note_change():
        # Actual Appointment -> existing Note update, as in restore. A new
        # note also checks the real Doctor FK, as ordinary summarize does.
        with Session(engine) as session:
            pids["writer"] = session.execute(
                text("SELECT pg_backend_pid()")
            ).scalar_one()
            writer_entered.set()
            session.exec(
                select(Appointment)
                .where(Appointment.id == note.appointment_id)
                .with_for_update()
            ).one()
            session.execute(
                update(AIVisitNote)
                .where(cast(Any, AIVisitNote.appointment_id) == note.appointment_id)
                .values(
                    superseded_at=datetime.now(UTC),
                    supersession_reason="regenerated",
                )
            )
            if latent_source:
                session.execute(
                    update(AIVisitNote)
                    .where(cast(Any, AIVisitNote.id) == note.id)
                    .values(
                        superseded_at=None,
                        supersession_reason=None,
                    )
                )
            if writer_first:
                held.set()
                assert release.wait(5)
            if new_note:
                session.add(
                    AIVisitNote(
                        doctor_id=note.doctor_id,
                        appointment_id=note.appointment_id,
                        doctor_note_schema_id=note.doctor_note_schema_id,
                        ai_transcript_id=note.ai_transcript_id,
                        note={"Plan": "New fictional generated version."},
                    )
                )
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        try:
            if writer_first:
                writer = pool.submit(publish_note_change)
                assert held.wait(5)
                final = pool.submit(_final, client, token, turn, ref)
                # Refuse and roll back before the writer's Doctor FK insert;
                # a waiting reader would deadlock or exhaust this timeout.
                response = final.result(timeout=3)
                assert response.status_code == 409, response.text
            else:
                final = pool.submit(_final, client, token, turn, ref)
                assert held.wait(5)
                writer = pool.submit(publish_note_change)
                assert writer_entered.wait(5)
                deadline = time.monotonic() + 3
                blocked = False
                with engine.connect() as connection:
                    while time.monotonic() < deadline:
                        blockers = connection.execute(
                            text("SELECT pg_blocking_pids(:pid)"),
                            {"pid": pids["writer"]},
                        ).scalar_one()
                        if pids["final"] in blockers:
                            blocked = True
                            break
                        time.sleep(0.01)
                assert blocked, "Source writer bypassed final acceptance"
                assert not writer.done()
            release.set()
            response = final.result(timeout=5)
            writer.result(timeout=5)
        finally:
            release.set()
    assert response.status_code == (409 if writer_first else 201), response.text
    assert CanonicalConversationFinalPhase.count() == (0 if writer_first else 1)
