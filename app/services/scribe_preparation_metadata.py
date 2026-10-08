"""One creator/consent receiver read, bracketed by current Core authority."""

import asyncio
from uuid import UUID

from app.services import scribe_capture_protocol as protocol
from app.services.scribe_capture_admission import AdmittedCaptureScope, CaptureCaller
from app.services.scribe_capture_deadline import check_async_capture_deadline
from app.services.scribe_configured_capture_contract import ConfiguredSessionCreate
from app.services.scribe_workflow import ScribeWorkflowClient


async def execute_preparation_metadata(
    *,
    caller: CaptureCaller,
    binding_ref: str,
    workspace_id: UUID,
    scope: AdmittedCaptureScope,
    client_request_key: str,
    client: ScribeWorkflowClient,
) -> protocol.PreparationMetadata:
    from app.services.scribe_capture_gateway import (
        CaptureOperationUnavailable,
        CaptureReceiptInvalid,
        _execute_creator_review,
        authorize_admitted_capture,
    )

    if (
        not isinstance(scope.session_request, ConfiguredSessionCreate)
        or scope.creator_actor_id is None
    ):
        raise CaptureOperationUnavailable

    # The outer gateway supplied its ordinary fresh authorization. One remote
    # receiver now checks creator/consent before key I/O and again before its
    # complete response; it performs no source mutation. The helper validates
    # all three receipts and reauthorizes Core after that I/O before returning.
    check_async_capture_deadline()
    await asyncio.sleep(0)
    check_async_capture_deadline()
    if authorize_admitted_capture(caller, binding_ref, workspace_id) != scope:
        raise CaptureReceiptInvalid
    result = await _execute_creator_review(
        caller,
        binding_ref,
        workspace_id,
        scope,
        protocol.ReadOperation(kind="preparation_metadata"),
        client_request_key,
        client,
    )
    check_async_capture_deadline()
    if not isinstance(result, protocol.PreparationMetadata):
        raise CaptureReceiptInvalid
    return result
