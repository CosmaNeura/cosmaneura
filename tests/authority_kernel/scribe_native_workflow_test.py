"""Closed native workflow authority fences; synthetic in-memory boundaries only."""

from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from app.configuration.scribe_capture import ScribeCaptureSettings
from app.services import authority
from app.services import scribe_native_capture as service
from app.services.scribe_capture_note_edits import NoteEdit, request_hash
from app.services.scribe_capture_note_request import NoteRequest
from app.services.scribe_capture_protocol import ReadChunk, ReadOperation

from tests.authority_kernel import scribe_native_capture_receiver_test as wire
from tests.authority_kernel import scribe_native_capture_service_test as boundaries


def operation(data, **changes):
    return service.NativeCaptureRequest.model_validate(
        {
            **data.model_dump(exclude_none=True),
            "action": "workflow_operation",
            "binding_ref": boundaries.BINDING,
            "opening_nonce": None,
            "operation": ReadChunk(
                kind="chunk_receipt", object_id=uuid4()
            ).model_dump(),
            **changes,
        }
    )


def test_original_operation_remains_read_only_and_new_scope_is_closed(monkeypatch):
    data, _, _, _ = boundaries.fixture(monkeypatch)
    value = operation(data)
    with pytest.raises(ValidationError):
        service.NativeCaptureRequest.model_validate(
            value.model_dump() | {"action": "operation"}
        )
    for kind in (
        "transcript",
        "working_transcript",
        "capture_recovery_state",
        "correct_transcript",
        "recover_capture",
    ):
        with pytest.raises(ValidationError):
            operation(data, operation={"kind": kind})


@pytest.mark.asyncio
async def test_dark_workflow_cannot_reach_lookup_exchange_or_dispatch(monkeypatch):
    data, calls, _, invoke = boundaries.fixture(monkeypatch)
    monkeypatch.setattr(
        service, "native_existing_patient_visits_enabled", lambda: False
    )
    with pytest.raises(authority.AuthorityKernelDisabledError):
        await invoke(operation(data))
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke", ["before", "during"])
async def test_current_provider_revocation_denies_before_or_after_existing_writer(
    monkeypatch, revoke
):
    data, calls, state, invoke = boundaries.fixture(monkeypatch)
    monkeypatch.setattr(service, "native_existing_patient_visits_enabled", lambda: True)
    state["revoked"] = revoke == "before"

    async def dispatch(**kwargs):
        calls.append(("writer", kwargs))
        state["revoked"] = True
        return ReadOperation(kind="creator_review")

    monkeypatch.setattr(service, "execute_capture_operation", dispatch)
    with pytest.raises(authority.AuthorityRequestDeniedError):
        await invoke(operation(data))
    assert sum(isinstance(call, tuple) and call[0] == "writer" for call in calls) == (
        revoke == "during"
    )


@pytest.mark.asyncio
async def test_operation_preserves_key_binding_current_caller_and_never_exports_context(
    monkeypatch,
):
    data, calls, _, invoke = boundaries.fixture(monkeypatch)
    monkeypatch.setattr(service, "native_existing_patient_visits_enabled", lambda: True)
    value = operation(data)
    result = await invoke(value)
    writer = [call[1] for call in calls if isinstance(call, tuple)][0]
    assert writer["client_request_key"] == "original-intent-key"
    assert writer["binding_ref"] == boundaries.BINDING
    assert writer["operation"] == value.operation
    assert writer["caller"].workload_client_id == "workload_test"
    assert writer["caller"].mount_context_token == "never-return-context-token"
    response_body = result["result"]
    assert isinstance(response_body, dict)
    assert response_body["operation"] == "chunk_receipt"
    assert "never-return" not in str(result)


@pytest.mark.asyncio
async def test_flag_removed_during_writer_with_global_settings_true_does_not_publish(
    monkeypatch,
):
    data, _, _, invoke = boundaries.fixture(monkeypatch)
    gate = {"enabled": True}
    monkeypatch.setattr(
        service, "native_existing_patient_visits_enabled", lambda: gate["enabled"]
    )

    async def dispatch(**kwargs):
        gate["enabled"] = False
        return ReadOperation(kind="creator_review")

    monkeypatch.setattr(service, "execute_capture_operation", dispatch)
    with pytest.raises(authority.AuthorityKernelDisabledError):
        await invoke(operation(data))


@pytest.mark.parametrize("enabled", [False, True])
def test_native_http_keeps_both_workload_policies_and_dark_gate(monkeypatch, enabled):
    connection, calls = wire.client(monkeypatch)
    monkeypatch.setattr(
        wire.route, "native_existing_patient_visits_enabled", lambda: enabled
    )
    value = wire.body(
        action="workflow_operation",
        binding_ref=boundaries.BINDING,
        operation={"kind": "chunk_receipt", "object_id": str(uuid4())},
    )
    response = connection.post(wire.PATH, json=value, headers=wire.headers())
    assert response.status_code == (200 if enabled else 503)
    assert calls[:2] == ["agent", "capture"]
    assert len(calls) == (3 if enabled else 2)


def test_recovery_admission_is_closed_and_can_check_original_consent(monkeypatch):
    connection, calls = wire.client(monkeypatch)
    monkeypatch.setattr(
        wire.route, "native_existing_patient_visits_enabled", lambda: True
    )
    body = wire.body(
        action="workflow_admission",
        binding_ref=boundaries.BINDING,
        attestation=None,
        configured_capture={"schema_version": "1.0", "language_mode": "en-US"},
    )
    response = connection.post(wire.PATH, json=body, headers=wire.headers())
    assert response.status_code == 200 and calls[2]["data"].attestation is None
    body["retain_preparation_context"] = True
    assert (
        connection.post(wire.PATH, json=body, headers=wire.headers()).status_code == 422
    )


def note(data, action="save", **changes):
    key = uuid4()
    value = dict(
        workspace_id=data.workspace_id,
        binding_ref=boundaries.BINDING,
        request_nonce=data.request_nonce,
        expected_session_id=uuid4(),
        action=action,
        schema_ref="doctor_note_schema_00000000000000000000000004",
        edit=dict(
            client_key=key,
            expected_note_ref="visit_note_00000000000000000000000005",
            expected_note_sha256="1" * 64,
            expected_source_sha256="2" * 64,
            fields={"test": "synthetic"},
        ),
    )
    return NoteRequest.model_validate(value | changes)


@pytest.mark.parametrize("field", ["workspace_id", "binding_ref", "request_nonce"])
def test_note_scope_must_match_current_native_envelope(monkeypatch, field):
    data, _, _, _ = boundaries.fixture(monkeypatch)
    n = note(data)
    changed = n.model_dump() | {
        field: uuid4()
        if field != "binding_ref"
        else "scribe_capture_binding_00000000000000000000000009"
    }
    with pytest.raises(ValidationError):
        service.NativeCaptureRequest.model_validate(
            {
                **data.model_dump(),
                "action": "workflow_notes",
                "binding_ref": boundaries.BINDING,
                "opening_nonce": None,
                "note_request": changed,
            }
        )


def test_note_edit_retains_existing_intent_hash_conflict_contract(monkeypatch):
    data, _, _, _ = boundaries.fixture(monkeypatch)
    n = note(data)
    assert n.edit is not None and n.schema_ref is not None
    changed = NoteEdit.model_validate(
        n.edit.model_dump() | {"fields": {"test": "changed synthetic"}}
    )
    assert changed.client_key == n.edit.client_key
    assert request_hash(
        n.edit, n.schema_ref, n.expected_session_id, n.workspace_id
    ) != request_hash(changed, n.schema_ref, n.expected_session_id, n.workspace_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,method",
    [
        ("save", "save_capture_note"),
        ("resolve", "resolve_capture_note_save"),
        ("save_status", "check_capture_note_save"),
    ],
)
async def test_native_note_uses_exact_existing_writer_and_current_authority(
    monkeypatch, action, method
):
    from app.configuration.scribe_encounters import ScribeEncounterSettings
    from app.services.scribe_capture_deadline import CaptureBudget, capture_budget
    from app.services.scribe_workflow import DisabledScribeWorkflowClient

    data, calls, state, _ = boundaries.fixture(monkeypatch)
    monkeypatch.setattr(service, "native_existing_patient_visits_enabled", lambda: True)
    n = note(data, action)
    assert n.edit is not None
    value = service.NativeCaptureRequest.model_validate(
        {
            **data.model_dump(),
            "action": "workflow_notes",
            "binding_ref": boundaries.BINDING,
            "opening_nonce": None,
            "note_request": n,
        }
    )
    seen = []

    class Receipt(BaseModel):
        session_id: str

    async def writer(**kwargs):
        seen.append(kwargs)
        return Receipt(session_id=str(n.expected_session_id))

    monkeypatch.setattr(service.note_edits, method, writer)
    with capture_budget(CaptureBudget(10)):
        result = await service.execute_native_capture(
            issuer="https://test.clerk.invalid",
            subject="user_test",
            clerk_session_id="session_test",
            workload_client_id="workload_test",
            data=value,
            key=str(n.edit.client_key),
            authority_settings=boundaries.SETTINGS,
            capture_settings=ScribeCaptureSettings(enabled=True, notes_enabled=True),
            encounter_settings=ScribeEncounterSettings(enabled=True),
            client=DisabledScribeWorkflowClient(),
        )
    assert (
        seen[0]["edit"] == n.edit
        and seen[0]["expected_session_id"] == n.expected_session_id
    )
    assert seen[0]["caller"].mount_context_token == "never-return-context-token"
    response_body = result["result"]
    assert isinstance(response_body, dict)
    assert response_body["action"] == action and "never-return" not in str(result)
    assert calls[-1] == "lookup"


def test_note_header_key_must_equal_original_edit_key_before_writer(monkeypatch):
    connection, calls = wire.client(monkeypatch)
    monkeypatch.setattr(
        wire.route, "native_existing_patient_visits_enabled", lambda: True
    )
    data, _, _, _ = boundaries.fixture(monkeypatch)
    n = note(data)
    body = wire.body(
        action="workflow_notes",
        binding_ref=boundaries.BINDING,
        workspace_id=str(n.workspace_id),
        request_nonce=str(n.request_nonce),
        note_request=n.model_dump(mode="json"),
    )
    # Different header/body intent keys cannot reach the exact existing writer.
    assert (
        connection.post(wire.PATH, json=body, headers=wire.headers()).status_code == 422
    )
    assert calls == ["agent", "capture"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["renew_lease", "complete"])
async def test_queue_mutation_keeps_typed_payload_and_original_intent_key(
    monkeypatch, kind
):
    data, calls, _, invoke = boundaries.fixture(monkeypatch)
    monkeypatch.setattr(service, "native_existing_patient_visits_enabled", lambda: True)
    lease = uuid4()
    payload = (
        {"expected_generation": 1}
        if kind == "renew_lease"
        else {
            "capture_lease_id": str(lease),
            "capture_lease_generation": 1,
            "channels": [{"channel": "microphone", "final_sequence_number": 0}],
        }
    )
    op = {"kind": kind, "payload": payload}
    if kind == "renew_lease":
        op["lease_id"] = str(lease)
    value = operation(data, operation=op)
    await invoke(value)
    writer = [call[1] for call in calls if isinstance(call, tuple)][0]
    assert writer["operation"] == value.operation
    assert writer["client_request_key"] == "original-intent-key"
    assert writer["binding_ref"] == boundaries.BINDING
    invalid = op | {"payload": payload | {"tenant_id": str(uuid4())}}
    with pytest.raises(ValidationError):
        operation(data, operation=invalid)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,seconds",
    [("capture-notes", 100), ("capture-admissions", 100), ("catalog", 60)],
)
async def test_existing_container_relay_does_not_cut_off_original_core_deadline(
    path, seconds
):
    from types import SimpleNamespace

    import httpx
    from fastapi import FastAPI

    from app.scribe_bundle import CoreBundleGateway

    seen = []

    def child(request):
        seen.append(request.extensions["timeout"]["read"])

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"synthetic"

        return httpx.Response(200, stream=Stream())

    gateway = CoreBundleGateway(FastAPI())
    from asyncio.subprocess import Process
    from typing import cast

    gateway.process = cast(Process, SimpleNamespace(returncode=None))
    async with httpx.AsyncClient(transport=httpx.MockTransport(child)) as loopback:
        gateway.client = loopback
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway),
            base_url="https://core.synthetic.invalid",
        ) as client:
            response = await client.post("/provider/v1/" + path, json={})
    assert response.status_code == 200 and seen == [seconds]


def test_native_gate_is_fresh_inside_original_locked_authorization_boundary(
    monkeypatch,
):
    from dataclasses import replace
    from typing import cast

    from app.services.scribe_capture_admission import CaptureCaller

    from sqlmodel import Session

    calls = []
    monkeypatch.setattr(
        CaptureCaller,
        "authorize",
        lambda *args: calls.append(args) or "synthetic-current-binding",
    )
    gate = {"enabled": True}
    monkeypatch.setattr(
        service, "native_existing_patient_visits_enabled", lambda: gate["enabled"]
    )
    caller = service.NativeWorkflowCaller(
        "synthetic-workload", "synthetic-context", "synthetic-interaction", uuid4()
    )
    session = cast(Session, object())
    assert (
        caller.authorize(session, boundaries.BINDING, uuid4())
        == "synthetic-current-binding"
    )
    gate["enabled"] = False
    caller = replace(caller, selected_session_id=uuid4())
    assert isinstance(caller, service.NativeWorkflowCaller)
    with pytest.raises(authority.AuthorityKernelDisabledError):
        caller.authorize(session, boundaries.BINDING, uuid4())
    assert len(calls) == 1
