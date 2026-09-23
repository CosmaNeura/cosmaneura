"""Exact session-bound longer-visit policy, with real Core authority and journals."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.services import authority
from app.services import scribe_capture_admission as admission
from app.services import scribe_capture_gateway as gateway
from app.services import scribe_capture_protocol as p
from app.services.scribe_workflow import (
    HttpScribeWorkflowClient,
    ScribeWorkflowConflictError,
)

from activemodel.session_manager import _session_context
from app.models.authority import AuthorityActorExternalIdentity
from sqlalchemy import text
from sqlmodel import Session

from tests.authority_kernel.scribe_capture_admission_test import call_arguments
from tests.authority_kernel.scribe_capture_gateway_test import (
    NativeScribe,
    arguments,
    pair,
)
from tests.authority_kernel.scribe_capture_gateway_test import (
    admission_case as _admission_case,
)
from tests.authority_kernel.scribe_capture_gateway_test import (
    capture_engine as _capture_engine,
)
from tests.authority_kernel.scribe_gateway_receiver_test import scope
from tests.services.scribe_workflow_test import StaticCredential

admission_case = _admission_case
capture_engine = _capture_engine


def selection(channels=("microphone", "system_audio")):
    return p.SelectCaptureProfile(
        kind="select_capture_profile",
        payload=p.CaptureProfileRequest(
            profile_id="visit-60m-v1",
            channels=channels,
        ),
    )


def profile(selected_scope, request=None):
    request = request or selection().payload
    return p.CaptureProfileReceipt(
        receipt_id=uuid4(),
        session_id=selected_scope.session_id,
        request=request,
        request_sha256=p.profile_request_sha256(request),
        created_at=datetime.now(UTC),
        maximum_aggregate_plaintext_bytes=268435456,
        maximum_chunks=720,
        maximum_chunks_per_channel=360,
        maximum_track_duration_ms=3600000,
    )


def capabilities(selected_scope):
    data = pair("capabilities", selected_scope)[1].model_dump()
    return p.LongVisitCapabilities.model_validate(
        {**data, "max_chunks": 720, "remaining_aggregate_plaintext_bytes": 268435456}
    )


class ProfileScribe(NativeScribe):
    profile = None

    async def capture_operation(
        self, tenant_id, session_id, operation, *, idempotency_key
    ):
        if operation.kind == "capture_profile":
            self.operation_calls.append(
                (tenant_id, session_id, operation, idempotency_key)
            )
            self.after_native("profile")
            assert self.profile is not None
            return self.profile
        if isinstance(operation, p.SelectCaptureProfile):
            if self.profile is not None and self.profile.request != operation.payload:
                raise ScribeWorkflowConflictError("private_sentinel")
            self.profile = self.profile or self.result
            self.result = self.profile
        return await super().capture_operation(
            tenant_id, session_id, operation, idempotency_key=idempotency_key
        )


async def ready(f, *, enabled=True):
    f.client = ProfileScribe()
    f.caller = replace(
        f.caller,
        capture_settings=replace(
            f.caller.capture_settings, long_visits_enabled=enabled
        ),
    )
    await admission.admit_capture(**call_arguments(f))
    selected_scope = admission.authorize_admitted_capture(
        f.caller, f.binding.binding_ref, f.binding.workspace_id
    )
    f.client.result = profile(selected_scope)
    return selected_scope


@pytest.mark.asyncio
async def test_select_recover_disable_and_use_exact_profile(admission_case):
    f = admission_case
    selected_scope = await ready(f)
    receipt = await gateway.execute_capture_operation(**arguments(f, selection()))
    assert receipt == f.client.profile
    f.caller = replace(
        f.caller,
        capture_settings=replace(f.caller.capture_settings, long_visits_enabled=False),
    )
    assert (
        await gateway.execute_capture_operation(
            **arguments(f, p.ReadOperation(kind="capture_profile"))
        )
        == receipt
    )
    with pytest.raises(gateway.CaptureOperationUnavailable):
        await gateway.execute_capture_operation(**arguments(f, selection()))
    f.client.result = capabilities(selected_scope)
    result = await gateway.execute_capture_operation(
        **arguments(
            f,
            p.ReadOperation(kind="capabilities", profile_receipt_id=receipt.receipt_id),
        )
    )
    assert result.max_chunks == 720
    assert [call[2].kind for call in f.client.operation_calls[-2:]] == [
        "capture_profile",
        "capabilities",
    ]
    assert all(
        call[:2] == (selected_scope.tenant_id, selected_scope.session_id)
        for call in f.client.operation_calls
    )


@pytest.mark.asyncio
async def test_default_off_selection_never_calls_scribe(admission_case):
    f = admission_case
    await ready(f, enabled=False)
    with pytest.raises(gateway.CaptureOperationUnavailable):
        await gateway.execute_capture_operation(**arguments(f, selection()))
    assert not f.client.operation_calls


@pytest.mark.asyncio
async def test_lost_profile_reply_recovers_same_receipt_after_core_reopen(
    admission_case,
):
    f = admission_case
    await ready(f)
    f.client.lose = True
    args = arguments(f, selection())
    with pytest.raises(gateway.CaptureOperationUnconfirmed):
        await gateway.execute_capture_operation(**args)
    with Session(f.engine) as fresh:
        token = _session_context.set(fresh)
        try:
            actual = await gateway.execute_capture_operation(**args)
        finally:
            _session_context.reset(token)
    assert actual == f.client.profile
    assert len(f.client.receipts) == 1
    assert f.client.operation_calls[0][3] == f.client.operation_calls[1][3]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["wrong_id", "session", "hash", "future", "unselected", "over_channel"]
)
async def test_invalid_profile_stops_before_capability_or_reservation_call(
    admission_case, fault
):
    f = admission_case
    selected_scope = await ready(f)
    receipt = await gateway.execute_capture_operation(**arguments(f, selection()))
    f.client.operation_calls.clear()
    operation = p.ReadOperation(
        kind="capabilities", profile_receipt_id=receipt.receipt_id
    )
    if fault == "wrong_id":
        operation = operation.model_copy(update={"profile_receipt_id": uuid4()})
    elif fault in {"session", "hash", "future"}:
        change = {
            "session": {"session_id": uuid4()},
            "hash": {"request_sha256": "0" * 64},
            "future": {"created_at": datetime.now(UTC) + timedelta(hours=1)},
        }[fault]
        f.client.profile = receipt.model_copy(update=change)
    else:
        operation = p.ReserveChunks(
            kind="reserve_chunks",
            profile_receipt_id=receipt.receipt_id,
            payload=p.ReservePayload(
                capture_lease_id=uuid4(),
                capture_lease_generation=1,
                channel="mixed" if fault == "unselected" else "microphone",
                first_sequence_number=0 if fault == "unselected" else 359,
                count=2,
            ),
        )
    f.client.result = capabilities(selected_scope)
    with pytest.raises(gateway.CaptureReceiptInvalid):
        await gateway.execute_capture_operation(**arguments(f, operation))
    assert [call[2].kind for call in f.client.operation_calls] == ["capture_profile"]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["status", "profile", "operation"])
async def test_revocation_during_profile_flow_denies_next_step(admission_case, phase):
    f = admission_case
    selected_scope = await ready(f)
    receipt = await gateway.execute_capture_operation(**arguments(f, selection()))
    f.client.operation_calls.clear()
    f.client.result = capabilities(selected_scope)

    def revoke(observed):
        if observed != phase:
            return
        with Session(f.engine) as db:
            db.execute(text("SET LOCAL lock_timeout='1s'"))
            identity = db.get(AuthorityActorExternalIdentity, f.case.identity.id)
            identity.revoked_at = datetime.now(UTC)
            db.add(identity)
            db.commit()

    f.client.after_native = revoke
    with pytest.raises(authority.AuthorityKernelError):
        await gateway.execute_capture_operation(
            **arguments(
                f,
                p.ReadOperation(
                    kind="capabilities", profile_receipt_id=receipt.receipt_id
                ),
            )
        )
    assert (
        len(f.client.operation_calls)
        == {"status": 0, "profile": 1, "operation": 2}[phase]
    )


@pytest.mark.parametrize(
    "fault", ["no_ack", "no_profile", "legacy_reply", "changed_selection"]
)
def test_receipt_validator_keeps_legacy_and_profile_contracts_separate(fault):
    selected_scope = scope()
    selected = profile(selected_scope)
    operation = p.ReadOperation(
        kind="capabilities", profile_receipt_id=selected.receipt_id
    )
    receipt = capabilities(selected_scope)
    if fault == "no_ack":
        operation = p.ReadOperation(kind="capabilities")
        selected = None
    elif fault == "no_profile":
        selected = None
    elif fault == "legacy_reply":
        receipt = pair("capabilities", selected_scope)[1]
    else:
        operation = selection(("microphone",))
        receipt = selected
        selected = None
    with pytest.raises(gateway.CaptureReceiptInvalid):
        gateway.validate_receipt(
            operation,
            receipt,
            selected_scope,
            upload_origins=(),
            capture_profile=selected,
        )


@pytest.mark.parametrize("kind", ["capture_profile", "transcript", "encryption_key"])
def test_profile_ack_is_not_accepted_on_unrelated_reads(kind):
    with pytest.raises(ValidationError):
        p.ReadOperation(kind=kind, profile_receipt_id=uuid4())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    ["capture_profile", "select_capture_profile", "capabilities", "reserve_chunks"],
)
async def test_closed_profile_routes_and_exact_ack_header(kind):
    selected_scope = scope()
    receipt = profile(selected_scope)
    if kind == "select_capture_profile":
        operation = selection()
        response = receipt
    elif kind == "capture_profile":
        operation = p.ReadOperation(kind=kind)
        response = receipt
    elif kind == "capabilities":
        operation = p.ReadOperation(kind=kind, profile_receipt_id=receipt.receipt_id)
        response = capabilities(selected_scope)
    else:
        legacy, response = pair(kind, selected_scope)
        operation = legacy.model_copy(update={"profile_receipt_id": receipt.receipt_id})
    seen = []

    def handler(request):
        seen.append(request)
        suffix = {
            "capture_profile": "capture-profile",
            "select_capture_profile": "capture-profile",
            "capabilities": "capture-capabilities",
            "reserve_chunks": "chunk-reservations",
        }[kind]
        assert request.url.path == f"/v1/sessions/{selected_scope.session_id}/{suffix}"
        assert request.headers["X-Tenant-ID"] == str(selected_scope.tenant_id)
        assert request.headers.get("X-Capture-Profile-Receipt") == (
            str(receipt.receipt_id)
            if kind in {"capabilities", "reserve_chunks"}
            else None
        )
        assert request.method == (
            "POST" if kind in {"select_capture_profile", "reserve_chunks"} else "GET"
        )
        return httpx.Response(200, json=response.model_dump(mode="json"))

    async with httpx.AsyncClient(
        base_url="https://scribe.internal.example",
        transport=httpx.MockTransport(handler),
    ) as wire:
        service = HttpScribeWorkflowClient(
            base_url="https://scribe.internal.example",
            scope="api://scribe/.default",
            timeout_seconds=5,
            credential=StaticCredential(),
            client=wire,
        )
        actual = await service.capture_operation(
            selected_scope.tenant_id,
            selected_scope.session_id,
            operation,
            idempotency_key="synthetic-profile-key",
        )
    assert actual == response and len(seen) == 1
