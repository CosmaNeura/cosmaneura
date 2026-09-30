"""Recoverable bind/issue/reserve sequence; never starts or authorizes a source.

The caller durably retains the exact recipe before dispatch. A lost response can
replay these component keys, including a mutation already committed by Scribe.
This is deliberately not an atomic transaction spanning services.
"""

from __future__ import annotations

import asyncio
from typing import Literal
from uuid import UUID

from app.services import scribe_capture_protocol as protocol
from app.services.scribe_capture_admission import (
    AdmittedCaptureScope,
    CaptureCaller,
    authorize_admitted_capture,
)
from app.services.scribe_capture_deadline import check_async_capture_deadline
from app.services.scribe_configured_capture_contract import ConfiguredSessionCreate
from app.services.scribe_recording_window_contract import PrepareRecordingWindow
from app.services.scribe_workflow import ScribeWorkflowClient


class PreparedRecordingWindow(protocol.ProtectedContract):
    schema_version: Literal["1.0"] = "1.0"
    binding: protocol.KeyReceipt
    lease: protocol.LeaseReceipt
    reservations: protocol.ReservationReceipt


async def execute_recording_window(
    *,
    caller: CaptureCaller,
    binding_ref: str,
    workspace_id: UUID,
    scope: AdmittedCaptureScope,
    operation: PrepareRecordingWindow,
    client_request_key: str,
    client: ScribeWorkflowClient,
    upload_origins: tuple[str, ...] = (),
) -> PreparedRecordingWindow:
    # Import after the gateway has parsed and admitted the outer request. Each
    # component then uses that same gateway's ordinary current authorization.
    from app.services.scribe_capture_gateway import (
        CaptureOperationUnavailable,
        CaptureReceiptInvalid,
        execute_capture_operation,
    )

    body = operation.payload
    if not isinstance(scope.session_request, ConfiguredSessionCreate) or scope.creator_actor_id is None:
        raise CaptureOperationUnavailable
    if client_request_key in {body.bind_idempotency_key, body.lease_idempotency_key, body.reserve_idempotency_key}:
        raise CaptureReceiptInvalid

    async def invoke(component: protocol.CaptureOperation, key: str):
        check_async_capture_deadline()
        await asyncio.sleep(0)
        check_async_capture_deadline()
        if authorize_admitted_capture(caller, binding_ref, workspace_id) != scope:
            raise CaptureReceiptInvalid
        result = await execute_capture_operation(
            caller=caller,
            binding_ref=binding_ref,
            workspace_id=workspace_id,
            operation=component,
            client_request_key=key,
            client=client,
            upload_origins=upload_origins,
        )
        check_async_capture_deadline()
        if authorize_admitted_capture(caller, binding_ref, workspace_id) != scope:
            raise CaptureReceiptInvalid
        return result

    binding = await invoke(
        protocol.BindKey(kind="bind_key", payload=protocol.KeyPayload(**body.binding.model_dump())),
        body.bind_idempotency_key,
    )
    lease = await invoke(
        protocol.IssueLease(kind="issue_lease", payload=protocol.ProtocolPayload()),
        body.lease_idempotency_key,
    )
    if not isinstance(binding, protocol.KeyReceipt) or not isinstance(lease, protocol.LeaseReceipt):
        raise CaptureReceiptInvalid
    if binding.session_id != scope.session_id or lease.session_id != scope.session_id:
        raise CaptureReceiptInvalid
    reservations = await invoke(
        protocol.ReserveChunks(
            kind="reserve_chunks",
            payload=protocol.ReservePayload(
                capture_lease_id=lease.lease_id,
                capture_lease_generation=lease.generation,
                channel=body.channel,
                first_sequence_number=0,
                count=body.reservation_count,
            ),
        ),
        body.reserve_idempotency_key,
    )
    if not isinstance(reservations, protocol.ReservationReceipt):
        raise CaptureReceiptInvalid
    if reservations.session_id != lease.session_id or reservations.capture_authorized_until != lease.expires_at:
        raise CaptureReceiptInvalid
    check_async_capture_deadline()
    return PreparedRecordingWindow(binding=binding, lease=lease, reservations=reservations)
