"""Closed note receiver, workload authentication and private failure responses."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException

from app.configuration.scribe_capture import ScribeCaptureSettings
from app.routes.dependencies.entra_service import authenticate_scribe_capture_request
from app.routes.scribe_capture import get_admission_client_factory, get_capture_settings
from app.services import authority
from app.services import scribe_capture_note_edits as edits
from app.services import scribe_capture_notes as notes
from app.services.scribe_workflow import DisabledScribeWorkflowClient

from tests.authority_kernel.scribe_capture_receiver_test import client, headers

PATH = "/service/v1/agent/scribe/capture-notes"


def body():
    return {
        "binding_ref": "scribe_capture_binding_01j00000000000000000000000",
        "workspace_id": str(uuid4()),
        "request_nonce": str(uuid4()),
        "expected_session_id": str(uuid4()),
        "schema_ref": "doctor_note_schema_01j00000000000000000000000",
        "action": "prepare",
    }


def connection():
    result = client()
    assert isinstance(result.app, FastAPI)
    result.app.dependency_overrides[get_capture_settings] = lambda: (
        ScribeCaptureSettings(enabled=True, notes_enabled=True)
    )
    result.app.dependency_overrides[get_admission_client_factory] = lambda: (
        DisabledScribeWorkflowClient
    )
    return result


def test_explicit_resolution_reaches_receiver_without_preparing_or_saving(monkeypatch):
    payload={**body(),"action":"resolve","edit":{
        "client_key":str(uuid4()),"expected_note_ref":"visit_note_01j00000000000000000000000",
        "expected_note_sha256":"b"*64,"expected_source_sha256":"a"*64,"fields":{"Plan":"Invented retained edit"}}}
    called=[]
    async def resolve(**kwargs):
        called.append(kwargs)
        return edits.SaveResolution(session_id=payload['expected_session_id'],schema_ref=payload['schema_ref'],
            client_key=payload['edit']['client_key'],request_sha256='c'*64,status='closed')
    monkeypatch.setattr(edits,'resolve_capture_note_save',resolve)
    monkeypatch.setattr(edits,'save_capture_note',lambda **_:pytest.fail('save reached'))
    monkeypatch.setattr(notes,'capture_note',lambda **_:pytest.fail('prepare reached'))
    response=connection().post(PATH,json=payload,headers=headers())
    assert response.status_code==200 and response.json()['action']=='resolve'
    assert response.json()['receipt']['status']=='closed' and len(called)==1
    assert called[0]['edit'].model_dump(mode='json')==payload['edit']


def test_explicit_edit_reaches_the_current_scope_with_closed_payload(monkeypatch):
    payload = {**body(), "action": "save", "edit": {
        "client_key": str(uuid4()),
        "expected_note_ref": "visit_note_01j00000000000000000000000",
        "expected_note_sha256": "b" * 64, "expected_source_sha256": "a" * 64,
        "fields": {"CC": "Synthetic visit", "Plan": "x" * 2200},
    }}
    called = []
    async def save(**kwargs):
        called.append(kwargs)
        return notes.DraftReceipt(session_id=payload["expected_session_id"], scribe_source_id=uuid4(),
            source_sha256="a" * 64, schema_ref=payload["schema_ref"], transcript_ref="synthetic-transcript",
            note_ref="synthetic-note", status="draft", note_type="Visit", fields=payload["edit"]["fields"],
            field_order=("CC", "Plan"), note_sha256="b" * 64, created_at=datetime.now(UTC))
    monkeypatch.setattr(edits, "save_capture_note", save)
    response = connection().post(PATH, json=payload, headers=headers())
    assert response.status_code == 200 and response.json()["action"] == "save"
    assert called[0]["edit"].model_dump(mode="json") == payload["edit"]
    assert called[0]["caller"].capture_settings.notes_enabled


@pytest.mark.parametrize("fault", ["missing_edit", "edit_on_read", "authority", "large", "duplicate"])
def test_edit_receiver_rejects_ambiguous_or_unbounded_input(monkeypatch, fault):
    payload = {**body(), "action": "save", "edit": {
        "client_key": str(uuid4()), "expected_note_ref": "visit_note_01j00000000000000000000000",
        "expected_note_sha256": "b" * 64, "expected_source_sha256": "a" * 64,
        "fields": {"Plan": "Synthetic draft"},
    }}
    if fault == "missing_edit":
        del payload["edit"]
    elif fault == "edit_on_read":
        payload["action"] = "read"
    elif fault == "authority":
        payload["edit"]["doctor_id"] = "private sentinel"
    elif fault == "large":
        payload["edit"]["fields"]["Plan"] = "x" * 270337
    raw = json.dumps(payload)
    if fault == "duplicate":
        raw = raw.replace('"Plan":', '"Plan":"private sentinel","Plan":')
    monkeypatch.setattr(edits, "save_capture_note", lambda **_: pytest.fail("edit service reached"))
    response = connection().post(PATH, content=raw, headers=headers())
    assert response.status_code == 422 and "private sentinel" not in response.text


def test_workload_authentication_precedes_note_body(monkeypatch):
    result = connection()

    def deny():
        raise HTTPException(401)

    result.app.dependency_overrides[authenticate_scribe_capture_request] = deny
    monkeypatch.setattr(
        notes, "capture_note", lambda **_: pytest.fail("note service reached")
    )
    response = result.post(PATH, content="private sentinel")
    assert response.status_code == 401
    assert "private sentinel" not in response.text


def test_capture_enabled_does_not_implicitly_enable_notes():
    result = client()
    result.app.dependency_overrides[get_admission_client_factory] = lambda: (
        lambda: pytest.fail("client constructed")
    )
    response = result.post(PATH, content="private sentinel")
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "fault",
    [
        "patient_id",
        "doctor_id",
        "tenant_id",
        "content",
        "source",
        "schema",
        "action",
        "session",
        "duplicate",
        "oversized",
        "query",
    ],
)
def test_note_request_rejects_authority_content_and_ambiguous_input(monkeypatch, fault):
    payload = body()
    path = PATH
    if fault in {"patient_id", "doctor_id", "tenant_id", "content", "source"}:
        payload[fault] = "private sentinel"
    elif fault == "schema":
        payload["schema_ref"] = "../private sentinel"
    elif fault == "action":
        payload["action"] = "sign"
    elif fault == "session":
        payload["expected_session_id"] = "private sentinel"
    elif fault == "oversized":
        payload["extra"] = "private sentinel" * 200
    elif fault == "query":
        path += "?patient_id=private-sentinel"
    raw = json.dumps(payload)
    if fault == "duplicate":
        raw = raw[:-1] + ',"action":"read"}'
    monkeypatch.setattr(
        notes, "capture_note", lambda **_: pytest.fail("note service reached")
    )
    response = connection().post(
        path, headers={**headers(), "Content-Type": "application/json"}, content=raw
    )
    assert response.status_code == 422
    assert "private sentinel" not in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("action", ["read", "prepare"])
def test_note_response_correlates_request_and_releases_client(monkeypatch, action):
    payload = {**body(), "action": action}
    closed = []

    class LocalClient(DisabledScribeWorkflowClient):
        async def close(self):
            closed.append(True)

    async def execute(**kwargs):
        assert kwargs["prepare"] == (action == "prepare")
        assert kwargs["caller"].capture_settings.notes_enabled
        assert str(kwargs["expected_session_id"]) == payload["expected_session_id"]
        return notes.DraftReceipt(
            source_sha256="a" * 64,
            note_sha256="b" * 64,
            session_id=payload["expected_session_id"],
            scribe_source_id=uuid4(),
            schema_ref=payload["schema_ref"],
            transcript_ref="synthetic-transcript",
            note_ref="synthetic-note",
            status="draft",
            note_type="Synthetic visit",
            fields={"Plan": "Discuss sleep"},
            field_order=("Plan",),
            created_at=datetime.now(UTC),
        )

    monkeypatch.setattr(notes, "capture_note", execute)
    result = connection()
    result.app.dependency_overrides[get_admission_client_factory] = lambda: LocalClient
    response = result.post(PATH, json=payload, headers=headers())
    assert response.status_code == 200
    for field in ("binding_ref", "workspace_id", "request_nonce", "action"):
        assert response.json()[field] == payload[field]
    assert response.json()["receipt"]["review_required"] is True
    assert response.headers["cache-control"] == "no-store"
    assert closed == [True]


@pytest.mark.parametrize(
    "error,code",
    [
        (notes.CaptureNoteConflict, 409),
        (notes.CaptureNoteUnconfirmed, 503),
        (authority.AuthorityKernelError, 403),
    ],
)
def test_note_failure_hides_provider_exception_and_closes_client(
    monkeypatch, error, code
):
    closed = []

    class LocalClient(DisabledScribeWorkflowClient):
        async def close(self):
            closed.append(True)

    async def fail(**_):
        raise error("private sentinel")

    monkeypatch.setattr(notes, "capture_note", fail)
    result = connection()
    result.app.dependency_overrides[get_admission_client_factory] = lambda: LocalClient
    response = result.post(PATH, json=body(), headers=headers())
    assert response.status_code == code
    assert "private sentinel" not in response.text
    assert closed == [True]


def test_templates_action_requires_no_selected_schema(monkeypatch):
    payload = {**body(), "action": "templates"}
    assert connection().post(PATH, headers=headers(), json=payload).status_code == 422
    payload.pop("schema_ref")

    async def choices(**kwargs):
        return notes.TemplateReceipt(
            session_id=kwargs["expected_session_id"], templates=()
        )

    monkeypatch.setattr(notes, "capture_note_templates", choices)
    response = connection().post(PATH, headers=headers(), json=payload)
    assert response.status_code == 200
    assert response.json()["action"] == "templates"
    assert response.json()["receipt"]["templates"] == []


def test_request_deadline_returns_recoverable_error_and_cancels_pending_work(monkeypatch):
    import asyncio

    from app.routes import scribe_capture as route
    cancelled = []

    async def pending(**_):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(route, "NOTE_REQUEST_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(notes, "capture_note", pending)
    response = connection().post(PATH, headers=headers(), json=body())
    assert response.status_code == 503
    assert response.json()["code"] == "CAPTURE_NOTE_UNCONFIRMED"
    assert cancelled == [True]
