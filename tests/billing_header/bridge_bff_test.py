"""Actual Core routes/PG with a bounded fake CAB wire; no clinical inference fixture."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.configuration import billing_header as source_config
from app.configuration import cab_bridge as config
from app.routes import cab_bff
from app.services import billing_header_operations as prepared
from app.services import cab_bridge_authority as authority
from app.services import cab_bridge_client as wire
from app.services.cab_bridge_contract import (
    CabAuthorizationRequest,
    canonical,
    digest,
    minimized_operation,
)

from activemodel.session_manager import aglobal_session, get_session
from app.models.ai_visit_note import AIVisitNote
from app.models.billing_header_action import BillingHeaderAction as Action
from app.models.billing_header_operation import (
    BillingHeaderPreparedOperation as Operation,
)
from sqlmodel import select

from tests.billing_header.operations_test import (  # noqa: F401
    ISSUER,
    NOTE,
    command_for,
)
from tests.billing_header.operations_test import (
    case as _source_case,
)

case = _source_case  # Reuse the same isolated, owned source fixture.


@pytest.fixture
def bridge(case, monkeypatch):
    command = command_for(case)
    prepared.prepare_operation(case.actor, case.note_id, command)
    op_id = UUID(command.operationId)
    with get_session() as session:
        op = minimized_operation(
            session.exec(select(Operation).where(Operation.operation_id == op_id)).one()
        )
    settings = config.CabBridgeSettings(
        True, "https://cab.invalid", str(uuid4()), str(uuid4())
    )
    policy = replace(
        config.CAB_AUTHORIZE_POLICY,
        settings=replace(
            config.CAB_AUTHORIZE_POLICY.settings,
            enabled=True,
            tenant_id=str(uuid4()),
            audience=str(uuid4()),
        ),
        allowed_client_ids=frozenset({str(uuid4())}),
    )
    monkeypatch.setattr(config, "CAB_BRIDGE_SETTINGS", settings)
    monkeypatch.setattr(config, "CAB_AUTHORIZE_POLICY", policy)
    state = SimpleNamespace(
        signed_in=True, view_as=None, calls=[], proofs=[], fail=False, alter=None
    )
    pin = {
        "encounterId": "source-encounter-local",
        "encounterVersion": 1,
        "ingestionSource": "provider_completed_note",
        "noteId": "source-note-local",
        "noteVersion": 1,
        "noteSha256": op["source"]["textSha256"],
        "supportingSourceDigests": [],
        "bundleSha256": "a" * 64,
    }
    source = {
        "encounterId": pin["encounterId"],
        "encounterVersion": 1,
        "headerSource": pin,
        "noteText": op["noteText"],
        "intake": "",
        "transcript": "",
        "visitContext": op["visit"],
        "completion": op["completion"],
        "projection": {
            "version": op["source"]["projectionVersion"],
            **{
                k: op["source"][k]
                for k in ("excludedRecordingTime", "contentSha256", "textSha256")
            },
        },
        "inferencePolicy": {
            "allowed": False,
            "reason": "operational_source_model_not_approved",
        },
    }

    def respond(request):
        assert request.url.host == "cab.invalid" and request.method == "POST"
        assert request.headers["authorization"] == "Bearer local-opaque-token"
        payload = json.loads(request.content)
        state.calls.append(payload)
        req = CabAuthorizationRequest(
            schemaVersion="core-cab-authorization-request.v1",
            purpose=payload["purpose"],
            actor={"issuer": case.actor.issuer, "subject": case.actor.subject},
            requestSha256=digest(payload),
            sourceContentSha256=op["source"]["contentSha256"],
            sourceTextSha256=op["source"]["textSha256"],
        )
        # A fresh thread has no inherited request session; this proves locks were released
        # for CAB's real callback pattern, rather than reusing Core's request transaction.
        with ThreadPoolExecutor(max_workers=1) as executor:
            state.proofs.append(
                executor.submit(authority.authorize_action, op_id, req).result(
                    timeout=5
                )
            )
        if state.fail:
            state.fail = False
            raise httpx.ReadTimeout("synthetic ambiguous response")
        result = {"view": None}
        if payload["purpose"] == "completed-note.accept":
            result = {
                "schemaVersion": "cab.completed-note-handoff.v1",
                "receipt": {
                    "operationId": str(op_id),
                    "receiptId": str(uuid4()),
                    "acceptedAt": op["completion"]["completedAt"],
                    "encounterId": pin["encounterId"],
                    "encounterVersion": 1,
                    "sourceRevision": op["source"]["revision"],
                    "sourceManifestSha256": "b" * 64,
                    "headerSource": pin,
                },
                "replayed": False,
                "current": True,
                "launchPath": None,
                "generation": "not_started",
            }
        elif payload["purpose"] != "header.read":
            result = {"view": {"syntheticWireFixture": True}, "created": True}
        body = {
            "schemaVersion": "core-cab-result.v1",
            "purpose": payload["purpose"],
            "sourceOperationId": str(op_id),
            "requestSha256": digest(payload),
            "result": result,
            "source": source,
        }
        if state.alter:
            state.alter(body)
        return httpx.Response(200, json=body)

    client_type = wire.CabClient
    monkeypatch.setattr(
        wire,
        "CabClient",
        lambda: client_type(
            settings,
            transport=httpx.MockTransport(respond),
            token_provider=lambda: "local-opaque-token",
        ),
    )
    app = FastAPI()
    app.include_router(
        cab_bff.router, prefix="/internal/v1", dependencies=[Depends(aglobal_session)]
    )

    @app.middleware("http")
    async def verified_local_fixture(request, call_next):
        request.state.auth_state = SimpleNamespace(
            is_signed_in=state.signed_in,
            payload={
                "iss": ISSUER,
                "sub": case.actor.subject,
                "sid": "synthetic-session",
            },
        )
        request.state.doctor = SimpleNamespace(clerk_id=case.actor.subject)
        request.state.view_as = state.view_as
        return await call_next(request)

    return SimpleNamespace(
        client=TestClient(app),
        path=f"/internal/v1/billing-header-operations/{op_id}/cab",
        state=state,
        op_id=op_id,
        operation=op,
        pin=pin,
        case=case,
    )


def call(bridge, action="accept", **changes):
    command = (
        {"expectedSource": None}
        if action == "accept"
        else {"encounterId": bridge.pin["encounterId"], "expectedSource": bridge.pin}
    )
    if action in ("generate", "save"):
        command["operationId"] = str(uuid4())
    if action == "save":
        command |= {
            "headerId": str(uuid4()),
            "expectedRevision": 0,
            "draft": {},
            "reason": "Authored synthetic review",
        }
    return bridge.client.post(
        bridge.path, json={"action": action, "command": command | changes}
    )


def test_actual_current_provider_routes_all_purposes_release_locks_and_finish_grants(
    bridge,
):
    for action in ("accept", "read", "generate", "save"):
        response = call(bridge, action)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["source"]["noteText"] == bridge.operation["noteText"]
        assert response.json()["requestSha256"] == digest(bridge.state.calls[-1])
    assert len(bridge.state.calls) == 4 and len(bridge.state.proofs) == 4
    with get_session() as s:
        actions = s.exec(
            select(Action).where(Action.operation_id == bridge.op_id)
        ).all()
        assert len(actions) == 4 and all(a.finished_at is not None for a in actions)
        assert s.get(AIVisitNote, bridge.case.note_id).note == NOTE
        row = s.exec(
            select(Operation).where(Operation.operation_id == bridge.op_id)
        ).one()
        assert minimized_operation(row) == bridge.operation


def test_ambiguous_response_is_not_retried_automatically_and_exact_retry_gets_new_grant(
    bridge,
):
    bridge.state.fail = True
    assert call(bridge).status_code == 503 and len(bridge.state.calls) == 1
    assert call(bridge).status_code == 200 and len(bridge.state.calls) == 2
    assert canonical(bridge.state.calls[0]) == canonical(bridge.state.calls[1])
    with get_session() as s:
        actions = s.exec(
            select(Action).where(Action.operation_id == bridge.op_id)
        ).all()
        assert len(actions) == 2 and all(a.finished_at is not None for a in actions)
        assert actions[0].request_sha256 == actions[1].request_sha256


def test_default_configuration_unsigned_and_view_as_are_not_action_authority(
    bridge, monkeypatch
):
    bridge.state.signed_in = False
    assert call(bridge).status_code == 401
    bridge.state.signed_in = True
    bridge.state.view_as = SimpleNamespace()
    assert call(bridge).status_code == 403
    bridge.state.view_as = None
    monkeypatch.setattr(config, "CAB_BRIDGE_SETTINGS", config.CabBridgeSettings())
    assert call(bridge).status_code == 503 and not bridge.state.calls


@pytest.mark.parametrize(
    "change",
    [
        lambda b: b.update(requestSha256="f" * 64),
        lambda b: b.update(sourceOperationId=str(uuid4())),
        lambda b: b["source"].update(noteText="Unrelated fictional note"),
        lambda b: b["source"].update(transcript="Not authorized for disclosure"),
        lambda b: b["source"]["projection"].update(textSha256="f" * 64),
    ],
)
def test_wrong_response_or_source_is_not_returned_to_browser(bridge, change):
    bridge.state.alter = change
    response = call(bridge)
    assert response.status_code == 503 and response.json() == {
        "detail": "cab_action_not_confirmed"
    }


def test_local_admission_loss_during_cab_response_prevents_browser_disclosure(
    bridge, monkeypatch
):
    bridge.state.alter = lambda body: monkeypatch.setattr(
        source_config, "BILLING_SOURCE_POLICY", source_config.BillingSourcePolicy()
    )
    assert call(bridge).status_code == 409
    assert len(bridge.state.calls) == 1


def test_new_confirmed_context_invalidates_previous_operation_before_any_wire_call(
    bridge,
):
    prepared.prepare_operation(
        bridge.case.actor,
        bridge.case.note_id,
        command_for(bridge.case, visit=bridge.operation["visit"] | {"patientAge": 32}),
    )
    assert call(bridge).status_code == 409 and not bridge.state.calls


@pytest.mark.parametrize(
    "body",
    [
        {
            "action": "accept",
            "command": {"expectedSource": None, "actor": {"subject": "other"}},
        },
        {
            "action": "accept",
            "command": {"expectedSource": None},
            "practiceId": "other",
        },
        {
            "action": "save",
            "command": {
                "encounterId": "e",
                "expectedSource": {},
                "operationId": str(uuid4()),
                "headerId": str(uuid4()),
                "expectedRevision": True,
                "draft": {},
                "reason": "x",
            },
        },
    ],
)
def test_browser_cannot_expand_source_authority_or_coerce_revision(bridge, body):
    assert bridge.client.post(bridge.path, json=body).status_code == 422
    assert not bridge.state.calls


def test_header_response_must_preserve_the_requested_source_pin(bridge):
    response = call(
        bridge, "read", expectedSource=bridge.pin | {"bundleSha256": "f" * 64}
    )
    assert response.status_code == 503
    assert len(bridge.state.calls) == 1
