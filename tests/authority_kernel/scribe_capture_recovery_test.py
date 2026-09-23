"""Recovery wire agreement and current Core authority around receiver operations."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import RootModel, ValidationError

from app.services import authority
from app.services import scribe_capture_gateway as gateway
from app.services import scribe_capture_protocol as protocol
from app.services.scribe_capture_recovery_contract import (
    CaptureLeaseReceipt,
    CaptureRecoveryHistory,
    CaptureRecoveryReceipt,
    CaptureRecoveryRequest,
    CaptureRecoveryState,
)
from app.services.scribe_capture_working_contract import canonical_hash

from app.models.authority import AuthorityActorExternalIdentity
from sqlmodel import Session

from tests.authority_kernel.scribe_capture_client_test import client_for
from tests.authority_kernel.scribe_capture_gateway_test import (
    NativeScribe,
    admission_case,
    arguments,
    capture_engine,
    ready,
)

__all__ = ["admission_case", "capture_engine"]
FIXTURE = Path(__file__).parent / "fixtures" / "capture_recovery.json"


def test_contract_round_trips_the_actual_scribe_fixture():
    fixture = json.loads(FIXTURE.read_text())
    for key, model in (
        ("request", CaptureRecoveryRequest),
        ("receipt", CaptureRecoveryReceipt),
        ("state", CaptureRecoveryState),
    ):
        assert (
            model.model_validate(fixture[key]).model_dump(mode="json") == fixture[key]
        )


class RecoveryScribe(NativeScribe):
    practitioner = None

    async def capture_operation(
        self, tenant_id, session_id, operation, *, idempotency_key, practitioner_id=None
    ):
        self.practitioner = practitioner_id
        return await super().capture_operation(
            tenant_id,
            session_id,
            operation,
            idempotency_key=idempotency_key,
        )


async def setup(f):
    scope = await ready(f)
    f.caller = replace(
        f.caller,
        capture_settings=replace(f.caller.capture_settings, recovery_enabled=True),
    )
    f.client = RecoveryScribe()
    return scope


def pair(scope, kind="recover_capture", *, expired=False):
    at = datetime.now(UTC) - (timedelta(hours=2) if expired else timedelta(seconds=1))
    request = CaptureRecoveryRequest(
        capture_lease_id=uuid4(),
        capture_lease_generation=1,
        segment_id=uuid4(),
        segment_ordinal=0,
        first_sequence_number=0,
        next_sequence_number=1,
        segment_started_at=at,
        gap_id=uuid4(),
        reason_code="capture_backgrounded",
        observed_at=at,
        acknowledged_at=at,
        decision="resume_capture",
    )
    receipt = CaptureRecoveryReceipt(
        receipt_id=uuid4(),
        session_id=scope.session_id,
        request=request,
        request_sha256=canonical_hash(request),
        acknowledged_by_practitioner_id=scope.session_request.practitioner_id,
        following_segment_id=uuid4(),
        lease=CaptureLeaseReceipt(
            receipt_id=uuid4(),
            lease_id=request.capture_lease_id,
            session_id=scope.session_id,
            generation=2,
            issued_at=at,
            expires_at=at + timedelta(minutes=15),
        ),
    )
    if kind == "recover_capture":
        return protocol.RecoverCapture(kind=kind, payload=request), receipt
    return protocol.ReadOperation(kind="capture_recovery_state"), CaptureRecoveryState(
        history=CaptureRecoveryHistory(
            session_id=scope.session_id, recoveries=(receipt,)
        ),
        current_lease=receipt.lease,
        lease_closed=False,
        session_status="capturing",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["recover_capture", "capture_recovery_state"])
@pytest.mark.parametrize("expired", [False, True])
async def test_scoped_receipt_and_historical_state_never_invent_a_new_lease(
    admission_case,
    kind,
    expired,
):
    f = admission_case
    scope = await setup(f)
    operation, f.client.result = pair(scope, kind, expired=expired)
    receipt = await gateway.execute_capture_operation(**arguments(f, operation))
    assert receipt == f.client.result
    assert f.client.practitioner == scope.session_request.practitioner_id
    assert f.client.operation_calls[0][:2] == (scope.tenant_id, scope.session_id)
    assert "private_sentinel" not in repr(receipt)


@pytest.mark.asyncio
async def test_dark_recovery_gate_makes_no_remote_call(admission_case):
    f = admission_case
    scope = await setup(f)
    f.caller = replace(
        f.caller,
        capture_settings=replace(f.caller.capture_settings, recovery_enabled=False),
    )
    operation, f.client.result = pair(scope)
    observed = []
    f.client.after_native = observed.append
    with pytest.raises(gateway.CaptureOperationUnavailable):
        await gateway.execute_capture_operation(**arguments(f, operation))
    assert observed == [] and f.client.operation_calls == []


@pytest.mark.asyncio
async def test_lost_response_preserves_one_namespaced_recovery_key(admission_case):
    f = admission_case
    scope = await setup(f)
    operation, f.client.result = pair(scope)
    args = arguments(f, operation)
    f.client.lose = True
    with pytest.raises(gateway.CaptureOperationUnconfirmed):
        await gateway.execute_capture_operation(**args)
    assert await gateway.execute_capture_operation(**args) == f.client.result
    assert len(f.client.receipts) == 1
    assert f.client.operation_calls[0][3] == f.client.operation_calls[1][3]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["recover_capture", "capture_recovery_state"])
@pytest.mark.parametrize("phase", ["status", "operation"])
async def test_current_identity_revocation_denies_recovery_data(
    admission_case, kind, phase
):
    f = admission_case
    scope = await setup(f)
    operation, f.client.result = pair(scope, kind)

    def revoke(observed):
        if observed == phase:
            with Session(f.engine) as session:
                row = session.get(AuthorityActorExternalIdentity, f.case.identity.id)
                row.revoked_at = datetime.now(UTC)
                session.add(row)
                session.commit()

    f.client.after_native = revoke
    with pytest.raises(authority.AuthorityKernelError):
        await gateway.execute_capture_operation(**arguments(f, operation))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["session", "practitioner", "command", "future_lease"]
)
async def test_foreign_or_changed_recovery_receipt_is_rejected(admission_case, change):
    f = admission_case
    scope = await setup(f)
    operation, result = pair(scope)
    value = result.model_dump(mode="json")
    if change == "session":
        value["session_id"] = value["lease"]["session_id"] = str(uuid4())
    elif change == "practitioner":
        value["acknowledged_by_practitioner_id"] = "private_sentinel"
    elif change == "command":
        value["request"]["next_sequence_number"] = 2
        value["request_sha256"] = canonical_hash(
            CaptureRecoveryRequest.model_validate(value["request"])
        )
    else:
        future = datetime.now(UTC) + timedelta(minutes=1)
        value["lease"]["issued_at"] = future.isoformat()
        value["lease"]["expires_at"] = (future + timedelta(minutes=15)).isoformat()
    with pytest.raises(gateway.CaptureReceiptInvalid) as failure:
        gateway.validate_receipt(operation, RootModel(value), scope, upload_origins=())
    assert "private_sentinel" not in str(failure.value)


@pytest.mark.parametrize(
    "change",
    [
        "foreign_current_lease",
        "missing_current_lease",
        "closed_string",
        "duplicate_gap",
    ],
)
def test_recovery_state_contract_rejects_inconsistent_evidence(change):
    value = json.loads(FIXTURE.read_text())["state"]
    if change == "foreign_current_lease":
        value["current_lease"]["session_id"] = str(uuid4())
    elif change == "missing_current_lease":
        value["current_lease"] = None
    elif change == "closed_string":
        value["lease_closed"] = "false"
    else:
        value["history"]["recoveries"].append(value["history"]["recoveries"][0])
    with pytest.raises(ValidationError):
        CaptureRecoveryState.model_validate(value)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["recover_capture", "capture_recovery_state"])
async def test_transport_uses_closed_paths_and_trusted_practitioner_assertion(kind):
    fixture = json.loads(FIXTURE.read_text())
    body = fixture["receipt" if kind == "recover_capture" else "state"]
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(201 if kind == "recover_capture" else 200, json=body)

    operation = (
        protocol.RecoverCapture(
            kind=kind, payload=CaptureRecoveryRequest.model_validate(fixture["request"])
        )
        if kind == "recover_capture"
        else protocol.ReadOperation(kind=kind)
    )
    tenant, session_id = (
        uuid4(),
        CaptureRecoveryReceipt.model_validate(fixture["receipt"]).session_id,
    )
    practitioner = fixture["receipt"]["acknowledged_by_practitioner_id"]
    async with httpx.AsyncClient(
        base_url="https://scribe.test.invalid", transport=httpx.MockTransport(handler)
    ) as http:
        client = client_for(http)
        result = await client.capture_operation(
            tenant,
            session_id,
            operation,
            idempotency_key="recovery-stable-operation",
            practitioner_id=practitioner,
        )
    assert result.model_dump(mode="json") == body
    assert seen[0].headers["X-Capture-Practitioner"] == practitioner
    assert seen[0].headers["X-Tenant-ID"] == str(tenant)
    assert seen[0].url.path == f"/v1/sessions/{session_id}/" + (
        "capture-recoveries" if kind == "recover_capture" else "capture-recovery-state"
    )
