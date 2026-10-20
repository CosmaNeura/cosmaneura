"""Explicit continuation against owned PostgreSQL; no external authentication or inference."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.configuration import billing_header as source_config
from app.configuration import cab_bridge as config
from app.configuration.clerk import verified_provider_session
from app.services import billing_header_operations as prepared
from app.services import cab_bridge_authority as authority
from app.services.cab_bridge_contract import (
    CabActor,
    CabAuthorizationRequest,
    CabGenerationCompletion,
    digest,
)

from activemodel.session_manager import get_session
from app.models.ai_visit_note import AIVisitNote
from app.models.billing_header_action import BillingHeaderAction as Action
from app.models.billing_header_generation_continuation import (
    BillingHeaderGenerationContinuation as Evidence,
)
from app.models.practice import PracticeMember
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from tests.billing_header.bridge_bff_test import bridge as _bridge
from tests.billing_header.operations_test import case as _case
from tests.billing_header.operations_test import command_for

case = _case
bridge = _bridge


def intent(case):
    command = command_for(case)
    prepared.prepare_operation(case.actor, case.note_id, command)
    operation = UUID(command.operationId)
    actor = replace(case.actor, expires_at=datetime.now(UTC) + timedelta(seconds=60))
    body = {
        "encounterId": "synthetic-local",
        "expectedSource": {},
        "operationId": str(uuid4()),
        "responseMode": "async",
        "continuationConsent": True,
    }
    payload, action_id, replay = authority.begin_async_action(actor, operation, body)
    request = CabAuthorizationRequest(
        schemaVersion="core-cab-authorization-request.v1",
        purpose="header.generate",
        actor=CabActor(issuer=actor.issuer, subject=actor.subject),
        requestSha256=digest(payload),
        sourceContentSha256=command.expectedContentSha256,
        sourceTextSha256=command.expectedTextSha256,
    )
    continuation = {
        k: body[k] for k in ("encounterId", "expectedSource", "operationId")
    }
    continuation["requestSha256"] = digest(payload)
    return actor, operation, continuation, action_id, request


def test_explicit_evidence_allows_callback_after_original_60_seconds_without_new_intent(
    case, monkeypatch
):
    actor, operation, command, action_id, request = intent(case)
    original = authority.authorize_action(operation, request)
    assert datetime.fromisoformat(original["generationExpiresAt"]) > actor.expires_at
    assert (
        datetime.fromisoformat(original["generationAuthorizedUntil"])
        == actor.expires_at
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(
        {
            "iss": actor.issuer,
            "sub": actor.subject,
            "sid": "synthetic-session",
            "exp": int((actor.expires_at + timedelta(seconds=90)).timestamp()),
        },
        key,
        algorithm="RS256",
    )
    verified = jwt.decode(
        token, key.public_key(), algorithms=["RS256"], issuer=actor.issuer
    )
    fresh = verified_provider_session(
        SimpleNamespace(is_signed_in=True, payload=verified)
    )
    receipt = authority.continue_generation(fresh, operation, command)
    assert receipt["generationExpiresAt"] == original["generationExpiresAt"]
    # Cached JWT and repeated action add no evidence and do not extend authority.
    assert authority.continue_generation(fresh, operation, command) == receipt
    monkeypatch.setattr(
        authority, "_now", lambda _: actor.expires_at + timedelta(seconds=10)
    )
    proof = authority.authorize_action(operation, request)
    assert proof["allowed"] and proof["generationContinuationRequired"] is True
    assert fresh.expires_at is not None
    assert datetime.fromisoformat(proof["expiresAt"]) <= fresh.expires_at
    with get_session() as session:
        rows = session.exec(
            select(Evidence).where(Evidence.action_id == action_id)
        ).all()
        assert len(rows) == 1
        action = session.get(Action, action_id)
        assert action is not None
        assert rows[0].actor == action.actor
        assert "token" not in str(rows[0].model_dump()).lower()


def test_disconnect_status_and_exact_retry_cannot_renew_lapsed_generation(
    case, monkeypatch
):
    actor, operation, command, _, request = intent(case)
    original_deadline = authority.authorize_action(operation, request)[
        "generationExpiresAt"
    ]
    monkeypatch.setattr(
        authority, "_now", lambda _: actor.expires_at + timedelta(seconds=1)
    )
    fresh = replace(actor, expires_at=actor.expires_at + timedelta(seconds=120))
    status_payload, status_id = authority.begin_action(
        fresh,
        operation,
        "header.generation-status",
        {k: command[k] for k in ("encounterId", "expectedSource", "operationId")},
    )
    status_request = request.model_copy(
        update={
            "purpose": "header.generation-status",
            "requestSha256": digest(status_payload),
        }
    )
    assert "generationExpiresAt" not in authority.authorize_action(
        operation, status_request
    )
    # Simulated time does not write a future finish timestamp into the real DB.
    retry = {
        k: command[k] for k in ("encounterId", "expectedSource", "operationId")
    } | {"responseMode": "async", "continuationConsent": True}
    assert authority.begin_async_action(fresh, operation, retry)[2] is True
    for operation_call in (
        lambda: authority.authorize_action(operation, request),
        lambda: authority.continue_generation(fresh, operation, command),
    ):
        with pytest.raises(prepared.BillingPreparationError):
            operation_call()
    assert original_deadline


@pytest.mark.parametrize("change", ["session", "digest", "generation", "source"])
def test_exact_original_provider_session_and_request_binding(case, change):
    actor, operation, command, _, _ = intent(case)
    fresh = replace(actor, expires_at=actor.expires_at + timedelta(seconds=60))
    if change == "session":
        fresh = replace(fresh, session_sha256="a" * 64)
    if change == "digest":
        command = command | {"requestSha256": "b" * 64}
    if change == "generation":
        command = command | {"operationId": str(uuid4())}
    if change == "source":
        command = command | {"expectedSource": {"changed": True}}
    with pytest.raises(prepared.BillingPreparationError):
        authority.continue_generation(fresh, operation, command)


def test_terminal_closure_serializes_with_continuation_and_evidence_cannot_be_changed(
    case,
):
    actor, operation, command, action_id, request = intent(case)
    fresh = replace(actor, expires_at=actor.expires_at + timedelta(seconds=60))
    receipt = CabGenerationCompletion(
        schemaVersion="core-cab-generation-completion.v1",
        **request.model_dump(exclude={"schemaVersion", "purpose"}),
        status="unknown",
    )

    def advance():
        try:
            return authority.continue_generation(fresh, operation, command)
        except prepared.BillingPreparationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        continuation = executor.submit(advance)
        terminal = executor.submit(authority.complete_generation, operation, receipt)
        continuation.result(timeout=5)
        assert terminal.result(timeout=5)["closed"]
    with pytest.raises(prepared.BillingPreparationError):
        authority.continue_generation(fresh, operation, command)
    with get_session() as session:
        rows = session.exec(
            select(Evidence).where(Evidence.action_id == action_id)
        ).all()
        if rows:
            with pytest.raises(IntegrityError):
                session.execute(
                    text(
                        "DELETE FROM billing_header_generation_continuation WHERE id=:id"
                    ),
                    params={"id": rows[0].id},
                )
                session.commit()
            session.rollback()
        action = session.get(Action, action_id)
        assert action is not None
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "UPDATE billing_header_action SET initial_authorized_until=initial_authorized_until+interval '1 second' WHERE id=:id"
                ),
                params={"id": action.id.uuid},
            )
            session.commit()
        session.rollback()


@pytest.mark.parametrize("change", ["admission", "membership", "note"])
def test_current_scope_is_checked_before_append(case, monkeypatch, change):
    actor, operation, command, _, _ = intent(case)
    if change == "admission":
        monkeypatch.setattr(
            source_config, "BILLING_SOURCE_POLICY", source_config.BillingSourcePolicy()
        )
    else:
        with get_session() as session:
            if change == "note":
                note = session.get(AIVisitNote, case.note_id)
                assert note is not None
                note.note = {"Assessment": "Changed synthetic note"}
                session.add(note)
            else:
                member = session.exec(
                    select(PracticeMember).where(
                        PracticeMember.doctor_id == case.doctor_id
                    )
                ).one()
                member.attested_at = None
                session.add(member)
            session.commit()
    with pytest.raises(prepared.BillingPreparationError):
        authority.continue_generation(
            replace(actor, expires_at=actor.expires_at + timedelta(seconds=60)),
            operation,
            command,
        )


def test_current_admission_and_immutable_hard_deadline_bound_advances(
    case, monkeypatch
):
    actor, operation, command, _, request = intent(case)
    original = authority.authorize_action(operation, request)["generationExpiresAt"]
    shortened = case.admission.model_copy(
        update={"expiresAt": (datetime.now(UTC) + timedelta(seconds=70)).isoformat()}
    )
    monkeypatch.setattr(
        source_config,
        "BILLING_SOURCE_POLICY",
        source_config.BILLING_SOURCE_POLICY.model_copy(
            update={"admissions": (shortened,)}
        ),
    )
    receipt = authority.continue_generation(
        replace(actor, expires_at=actor.expires_at + timedelta(hours=1)),
        operation,
        command,
    )
    assert receipt["generationExpiresAt"] == original
    assert datetime.fromisoformat(
        receipt["generationAuthorizedUntil"]
    ) <= datetime.fromisoformat(shortened.expiresAt)
    assert datetime.fromisoformat(
        authority.authorize_action(operation, request)["expiresAt"]
    ) <= datetime.fromisoformat(shortened.expiresAt)


def test_route_default_off_then_explicit_continue_has_no_cab_http(bridge, monkeypatch):
    now = datetime.now(UTC)
    bridge.state.async_exp = int((now + timedelta(seconds=60)).timestamp())
    monkeypatch.setattr(
        config,
        "CAB_BRIDGE_SETTINGS",
        replace(config.CAB_BRIDGE_SETTINGS, async_enabled=True),
    )
    generation_id = str(uuid4())
    body = {
        "action": "generate",
        "command": {
            "encounterId": bridge.pin["encounterId"],
            "expectedSource": bridge.pin,
            "operationId": generation_id,
            "responseMode": "async",
            "continuationConsent": True,
        },
    }
    assert bridge.client.post(bridge.path, json=body).status_code == 409
    assert bridge.state.calls == []
    monkeypatch.setattr(
        config,
        "CAB_BRIDGE_SETTINGS",
        replace(config.CAB_BRIDGE_SETTINGS, continuation_enabled=True),
    )
    result = bridge.client.post(bridge.path, json=body)
    assert result.status_code == 200, result.text
    receipt = result.json()["result"]["continuation"]
    command = {
        k: body["command"][k] for k in ("encounterId", "expectedSource", "operationId")
    } | {"requestSha256": receipt["requestSha256"]}
    bridge.state.async_exp += 60
    response = bridge.client.post(
        bridge.path.removesuffix("cab") + "generation-continue",
        json={"purpose": "header.generation-continue", "command": command},
    )
    assert response.status_code == 200, response.text
    assert len(bridge.state.calls) == 1
    assert response.json()["generationExpiresAt"] == receipt["generationExpiresAt"]
    assert (
        response.json()["generationAuthorizedUntil"]
        > receipt["generationAuthorizedUntil"]
    )
    assert (
        bridge.client.post(
            bridge.path.removesuffix("cab") + "generation-continue",
            json={"purpose": "header.generation-status", "command": command},
        ).status_code
        == 422
    )


def test_concurrent_continuation_requests_append_one_identical_expiry(case):
    actor, operation, command, action_id, _ = intent(case)
    fresh = replace(actor, expires_at=actor.expires_at + timedelta(seconds=60))
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: authority.continue_generation(fresh, operation, command),
                range(2),
            )
        )
    assert results[0] == results[1]
    with get_session() as session:
        rows = session.exec(
            select(Evidence).where(Evidence.action_id == action_id)
        ).all()
        assert len(rows) == 1


def test_lost_consented_generate_retry_reads_status_with_original_receipt_digest(
    bridge, monkeypatch
):
    now = datetime.now(UTC)
    bridge.state.async_exp = int((now + timedelta(seconds=60)).timestamp())
    monkeypatch.setattr(
        config,
        "CAB_BRIDGE_SETTINGS",
        replace(
            config.CAB_BRIDGE_SETTINGS, async_enabled=True, continuation_enabled=True
        ),
    )
    body = {
        "action": "generate",
        "command": {
            "encounterId": bridge.pin["encounterId"],
            "expectedSource": bridge.pin,
            "operationId": str(uuid4()),
            "responseMode": "async",
            "continuationConsent": True,
        },
    }
    bridge.state.fail = True
    assert bridge.client.post(bridge.path, json=body).status_code == 503
    original_digest = digest(bridge.state.calls[0])
    result = bridge.client.post(bridge.path, json=body)
    assert result.status_code == 200, result.text
    result = result.json()
    assert result["purpose"] == "header.generate"
    assert (
        result["requestSha256"]
        == original_digest
        == result["result"]["continuation"]["requestSha256"]
    )
    assert [request["purpose"] for request in bridge.state.calls] == [
        "header.generate",
        "header.generation-status",
    ]
    assert "continuationConsent" not in bridge.state.calls[-1]["command"]


def test_database_guard_rejects_direct_mismatch_late_insert_and_evidence_rewrite(case):
    actor, operation, command, action_id, _ = intent(case)
    with get_session() as session:
        action = session.get(Action, action_id)
        assert action is not None
        for actor_value, digest_value, expiry in (
            (
                action.actor | {"sessionSha256": "f" * 64},
                action.request_sha256,
                actor.expires_at + timedelta(seconds=1),
            ),
            (action.actor, "e" * 64, actor.expires_at + timedelta(seconds=1)),
            (
                action.actor,
                action.request_sha256,
                action.expires_at + timedelta(seconds=1),
            ),
        ):
            with pytest.raises(IntegrityError):
                session.add(
                    Evidence(
                        action_id=action.id,
                        actor=actor_value,
                        request_sha256=digest_value,
                        created_at=datetime.now(UTC),
                        authorized_until=expiry,
                    )
                )
                session.commit()
            session.rollback()
    # Construct a separately owned, already-lapsed intent with its immutable initial evidence.
    original_clock = authority._now
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            authority, "_now", lambda _: datetime.now(UTC) - timedelta(seconds=70)
        )
        stale_actor = replace(
            actor, expires_at=datetime.now(UTC) - timedelta(seconds=10)
        )
        original = {
            k: command[k] for k in ("encounterId", "expectedSource", "operationId")
        } | {
            "operationId": str(uuid4()),
            "responseMode": "async",
            "continuationConsent": True,
        }
        _, stale_id, _ = authority.begin_async_action(stale_actor, operation, original)
    with get_session() as session:
        stale = session.get(Action, stale_id)
        assert stale is not None
        with pytest.raises(IntegrityError):
            session.add(
                Evidence(
                    action_id=stale.id,
                    actor=dict(stale.actor),
                    request_sha256=stale.request_sha256,
                    created_at=original_clock(session),
                    authorized_until=datetime.now(UTC) + timedelta(seconds=30),
                )
            )
            session.commit()
        session.rollback()
