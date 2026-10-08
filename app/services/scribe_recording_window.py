"""Recoverable first-window receiver; never starts or authorizes a source.

The caller durably retains the exact recipe before dispatch. A lost response can
replay these component keys, including a mutation already committed by Scribe.
Scribe retains all three ordinary component keys in its own transaction.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.services import scribe_capture_protocol as protocol
from app.services.scribe_capture_admission import (
    AdmittedCaptureScope,
    CaptureCaller,
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


def validate_prepared_recording_window(
    operation: PrepareRecordingWindow,
    receipt: BaseModel,
    scope: AdmittedCaptureScope,
    *,
    upload_origins: tuple[str, ...],
    now: datetime | None = None,
) -> PreparedRecordingWindow:
    from app.services.scribe_capture_gateway import (
        CaptureReceiptInvalid,
        validate_receipt,
    )

    try:
        result = PreparedRecordingWindow.model_validate(receipt.model_dump(mode="json"))
    except (ValueError, TypeError, AttributeError):
        raise CaptureReceiptInvalid from None
    body = operation.payload
    for component, evidence in (
        (
            protocol.BindKey(
                kind="bind_key",
                payload=protocol.KeyPayload(**body.binding.model_dump()),
            ),
            result.binding,
        ),
        (
            protocol.IssueLease(kind="issue_lease", payload=protocol.ProtocolPayload()),
            result.lease,
        ),
        (
            protocol.ReserveChunks(
                kind="reserve_chunks",
                payload=protocol.ReservePayload(
                    capture_lease_id=result.lease.lease_id,
                    capture_lease_generation=result.lease.generation,
                    channel=body.channel,
                    first_sequence_number=0,
                    count=body.reservation_count,
                ),
            ),
            result.reservations,
        ),
    ):
        validate_receipt(
            component, evidence, scope, upload_origins=upload_origins, now=now
        )
    if result.reservations.capture_authorized_until != result.lease.expires_at:
        raise CaptureReceiptInvalid
    return result


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
    from app.services.scribe_capture_gateway import (
        CaptureOperationUnavailable,
        CaptureReceiptInvalid,
        _execute_creator_review,
        authorize_admitted_capture,
        component_request_key,
    )

    body = operation.payload
    if (
        not isinstance(scope.session_request, ConfiguredSessionCreate)
        or scope.creator_actor_id is None
    ):
        raise CaptureOperationUnavailable
    if client_request_key in {
        body.bind_idempotency_key,
        body.lease_idempotency_key,
        body.reserve_idempotency_key,
    }:
        raise CaptureReceiptInvalid

    check_async_capture_deadline()
    await asyncio.sleep(0)
    check_async_capture_deadline()
    if authorize_admitted_capture(caller, binding_ref, workspace_id) != scope:
        raise CaptureReceiptInvalid
    # Preserve each original direct component namespace so old partial journals
    # and lost responses recover the same receipts through the new receiver.
    remote_operation = operation.model_copy(
        update={
            "payload": body.model_copy(
                update={
                    field: component_request_key(scope, kind, getattr(body, field))
                    for field, kind in (
                        ("bind_idempotency_key", "bind_key"),
                        ("lease_idempotency_key", "issue_lease"),
                        ("reserve_idempotency_key", "reserve_chunks"),
                    )
                }
            )
        }
    )
    result = await _execute_creator_review(
        caller,
        binding_ref,
        workspace_id,
        scope,
        remote_operation,
        component_request_key(scope, operation.kind, client_request_key),
        client,
    )
    check_async_capture_deadline()
    if not isinstance(result, PreparedRecordingWindow):
        raise CaptureReceiptInvalid
    return result
