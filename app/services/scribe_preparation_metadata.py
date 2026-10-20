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
    scope: AdmittedCaptureScope | None,
    client_request_key: str,
    client: ScribeWorkflowClient,
) -> protocol.PreparationMetadata:
    _, result = await _read_preparation_metadata(
        caller=caller,
        binding_ref=binding_ref,
        workspace_id=workspace_id,
        scope=scope,
        client_request_key=client_request_key,
        client=client,
    )
    return result


async def execute_native_recording_preparation(
    *,
    caller: CaptureCaller,
    binding_ref: str,
    workspace_id: UUID,
    client_request_key: str,
    client: ScribeWorkflowClient,
) -> protocol.NativeRecordingPreparation:
    scope, result = await _read_preparation_metadata(
        caller=caller,
        binding_ref=binding_ref,
        workspace_id=workspace_id,
        scope=None,
        client_request_key=client_request_key,
        client=client,
    )
    return native_preparation_from_metadata(scope, result, workspace_id)


def native_preparation_from_metadata(
    scope: AdmittedCaptureScope,
    result: protocol.PreparationMetadata,
    workspace_id: UUID,
) -> protocol.NativeRecordingPreparation:
    from app.services.scribe_capture_gateway import CaptureOperationUnavailable

    if result.review.status != "created" and result.review.status != "capturing":
        raise CaptureOperationUnavailable
    # The same freshly reauthorized admission supplies the native namespace.
    # Do not substitute the Core account tenant or accept caller-selected IDs.
    return protocol.NativeRecordingPreparation(
        context=protocol.NativeRecordingContext(
            workspace_id=workspace_id,
            tenant_id=scope.tenant_id,
            session_id=scope.session_id,
            status=result.review.status,
        ),
        encryption_key=result.encryption_key,
    )


def validate_admitted_preparation(
    caller: CaptureCaller,
    binding_ref: str,
    workspace_id: UUID,
    session_id: UUID,
    result: protocol.PreparationMetadata | None,
) -> tuple[AdmittedCaptureScope, protocol.PreparationMetadata]:
    from app.services.scribe_capture_gateway import (
        CaptureReceiptInvalid,
        authorize_admitted_capture,
        validate_receipt,
    )

    check_async_capture_deadline()
    scope = authorize_admitted_capture(caller, binding_ref, workspace_id)
    if result is None or scope.session_id != session_id:
        raise CaptureReceiptInvalid
    checked = validate_receipt(
        protocol.ReadOperation(kind="preparation_metadata"),
        result,
        scope,
        upload_origins=(),
    )
    metadata = protocol.PreparationMetadata.model_validate(
        checked.model_dump(mode="json")
    )
    # Validation/serialization can cross expiry or a committed revocation.
    # Match the ordinary metadata path's final current-admission/context fence.
    check_async_capture_deadline()
    if authorize_admitted_capture(caller, binding_ref, workspace_id) != scope:
        raise CaptureReceiptInvalid
    return scope, metadata


async def _read_preparation_metadata(
    *,
    caller: CaptureCaller,
    binding_ref: str,
    workspace_id: UUID,
    scope: AdmittedCaptureScope | None,
    client_request_key: str,
    client: ScribeWorkflowClient,
) -> tuple[AdmittedCaptureScope, protocol.PreparationMetadata]:
    from app.services.scribe_capture_gateway import (
        CaptureOperationUnavailable,
        CaptureReceiptInvalid,
        _execute_creator_review,
        authorize_admitted_capture,
    )

    # Take the ordinary fresh authorization directly before dispatch. One remote
    # receiver now checks creator/consent before key I/O and again before its
    # complete response; it performs no source mutation. The helper validates
    # all three receipts and reauthorizes Core after that I/O before returning.
    check_async_capture_deadline()
    await asyncio.sleep(0)
    check_async_capture_deadline()
    current = authorize_admitted_capture(caller, binding_ref, workspace_id)
    if scope is not None and current != scope:
        raise CaptureReceiptInvalid
    scope = current
    if (
        not isinstance(scope.session_request, ConfiguredSessionCreate)
        or scope.creator_actor_id is None
    ):
        raise CaptureOperationUnavailable
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
    return scope, result
