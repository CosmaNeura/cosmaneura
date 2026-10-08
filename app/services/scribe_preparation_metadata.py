"""Compose existing fresh reads in one invocation, without retaining authority."""

import asyncio
from typing import Literal
from uuid import UUID

from app.services import scribe_capture_protocol as protocol
from app.services.scribe_capture_admission import AdmittedCaptureScope, CaptureCaller
from app.services.scribe_capture_deadline import check_async_capture_deadline
from app.services.scribe_configured_capture_contract import ConfiguredSessionCreate
from app.services.scribe_creator_review_contract import CreatorReviewView
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
        execute_capture_operation,
    )

    if (
        not isinstance(scope.session_request, ConfiguredSessionCreate)
        or scope.creator_actor_id is None
    ):
        raise CaptureOperationUnavailable

    async def read(kind: Literal["creator_review", "encryption_key", "capabilities"]):
        # Keep every component's original pre/post-I/O authorization, receipt
        # validation and downstream consent checks. Only outer native setup and
        # the request-owned client/connection are shared. Never hold SQL locks
        # across an await or reuse authority from an earlier component.
        check_async_capture_deadline()
        await asyncio.sleep(0)
        check_async_capture_deadline()
        return await execute_capture_operation(
            caller=caller,
            binding_ref=binding_ref,
            workspace_id=workspace_id,
            operation=protocol.ReadOperation(kind=kind),
            client_request_key=client_request_key,
            client=client,
            _expected_scope=scope,
        )

    review = await read("creator_review")
    key = await read("encryption_key")
    capabilities = await read("capabilities")
    if (
        not isinstance(review, CreatorReviewView)
        or review.session_id != scope.session_id
        or not isinstance(key, protocol.EncryptionKey)
        or not isinstance(capabilities, protocol.Capabilities)
    ):
        raise CaptureReceiptInvalid
    check_async_capture_deadline()
    return protocol.PreparationMetadata(
        review=review, encryption_key=key, capabilities=capabilities
    )
