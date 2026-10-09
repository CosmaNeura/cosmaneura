"""One native request composes existing Core decisions; no cached authority."""

import time
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.configuration.authority import AuthorityKernelSettings
from app.configuration.scribe_capture import ScribeCaptureSettings
from app.configuration.scribe_encounters import ScribeEncounterSettings
from app.services import authority, scribe_capture_scope, scribe_encounters
from app.services.doctor_access_row_ownership import provider_record_access_row_scope
from app.services.provider_context_exchange import provider_exchange_blocked
from app.services.scribe_capture_admission import CaptureCaller
from app.services.scribe_capture_deadline import (
    check_async_capture_deadline,
    check_capture_deadline,
)
from app.services.scribe_capture_gateway import execute_capture_operation
from app.services.scribe_capture_protocol import CaptureOperation, ReadOperation
from app.services.scribe_encounter_contract import LookupRequest
from app.services.scribe_workflow import ScribeWorkflowClient
from app.utils.native_request_timing import phase

from activemodel.session_manager import global_session

NATIVE_READ_OPERATIONS = frozenset(
    {
        "creator_review",
        "provider_encounter",
        "capabilities",
        "native_recording_context",
        # Existing public wrapping key read; no private key or key mutation.
        "encryption_key",
        "preparation_metadata",
    }
)


class NativeCaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    action: Literal["mount", "operation", "preparation_context"]
    workspace_id: UUID
    request_nonce: UUID
    expected_actor_ref: str = Field(
        pattern=r"^authority_actor_[0-7][0-9a-hjkmnp-tv-z]{25}$"
    )
    expected_tenant_ref: str = Field(
        pattern=r"^authority_tenant_[0-7][0-9a-hjkmnp-tv-z]{25}$"
    )
    binding_ref: str | None = Field(
        default=None, pattern=r"^scribe_capture_binding_[0-7][0-9a-hjkmnp-tv-z]{25}$"
    )
    operation: CaptureOperation | None = None
    opening_nonce: UUID | None = None
    preparation: Literal["admission", "recording_window"] | None = None

    @model_validator(mode="after")
    def action_fields(self):
        if self.action == "mount":
            if (
                self.binding_ref is not None
                or self.operation is not None
                or self.preparation is not None
            ):
                raise ValueError("invalid_native_mount")
        elif self.action == "preparation_context":
            if (
                self.binding_ref is None
                or self.preparation is None
                or self.operation is not None
                or self.opening_nonce is not None
            ):
                raise ValueError("invalid_native_preparation_context")
        elif (
            self.binding_ref is None
            or self.operation is None
            or self.opening_nonce is not None
            or self.preparation is not None
        ):
            raise ValueError("invalid_native_operation")
        if (
            self.operation is not None
            and self.operation.kind not in NATIVE_READ_OPERATIONS
        ):
            raise ValueError("native_capture_read_required")
        return self


async def execute_native_capture(
    *,
    issuer: str,
    subject: str,
    clerk_session_id: str,
    workload_client_id: str,
    data: NativeCaptureRequest,
    key: str,
    authority_settings: AuthorityKernelSettings,
    capture_settings: ScribeCaptureSettings,
    encounter_settings: ScribeEncounterSettings,
    client: ScribeWorkflowClient,
) -> dict[str, object]:
    """Keep fresh Core lookup/authorization at each original service boundary.

    Each synchronous service closes its transaction before the downstream
    await. The existing capture worker owns SQL, cancellation and publication.
    Local Scribe registry checks bracket this request in the native receiver.
    """

    def lookup(*, include_status=False):
        check_async_capture_deadline()
        remaining = check_capture_deadline()
        if remaining is None:
            raise ValueError("native_capture_deadline_required")
        with phase("policy_lookup"):
            result = scribe_encounters.encounter_workflow(
                issuer=issuer,
                subject=subject,
                session_id=clerk_session_id,
                request=LookupRequest(
                    action="lookup",
                    workspace_id=data.workspace_id,
                    request_nonce=uuid4(),
                    expected_actor_ref=data.expected_actor_ref,
                    expected_tenant_ref=data.expected_tenant_ref,
                ),
                settings_provider=lambda: encounter_settings,
                authority_settings=authority_settings,
                # This shorter SQL budget supplements the unchanged capture budget.
                deadline=time.monotonic() + min(15, remaining),
                include_lookup_status=include_status,
            )
        check_async_capture_deadline()
        row = result["encounter"]
        if row["workspace_id"] != str(data.workspace_id):
            raise authority.AuthorityRequestDeniedError
        return row

    first = lookup()

    def current(*, include_status=False):
        row = lookup(include_status=include_status)
        if any(
            row[name] != first[name]
            for name in ("workspace_id", "appointment_ref", "patient_ref")
        ):
            raise authority.AuthorityRequestDeniedError
        return row

    # Retain the exchange route's guard/issuance transaction and lock ordering.
    # Close it before the next lookup or any downstream await.
    with phase("policy_exchange"):
        with global_session() as session, provider_record_access_row_scope(session):
            if provider_exchange_blocked(
                issuer=issuer,
                subject=subject,
                clerk_session_id=clerk_session_id,
                requested_surface="provider_assistant",
                record_ref=first["appointment_ref"],
                settings=authority_settings,
            ):
                raise authority.AuthorityRequestDeniedError
            issued = authority.exchange_context_token(
                clerk_issuer=issuer,
                clerk_subject=subject,
                requested_surface="provider_assistant",
                record_ref=first["appointment_ref"],
                settings=authority_settings,
            )
    current()
    turn = uuid4()
    with phase("policy_mount"):
        resolved = authority.resolve_mount_context(
            workload_client_id=workload_client_id,
            mount_context_token=issued.context_token,
            client_turn_id=turn,
            settings=authority_settings,
        )
    context = resolved.context
    if (
        context.surface,
        context.auth_tier,
        str(context.actor_id),
        str(context.tenant_id),
    ) != (
        "provider_assistant",
        "provider",
        data.expected_actor_ref,
        data.expected_tenant_ref,
    ):
        raise authority.AuthorityRequestDeniedError
    last = current()
    caller = CaptureCaller(
        workload_client_id=workload_client_id,
        mount_context_token=issued.context_token,
        interaction_ref=resolved.interaction_ref,
        client_turn_id=turn,
        settings=authority_settings,
        capture_settings=capture_settings,
    )
    binding = data.binding_ref
    if data.action == "mount" or data.preparation == "admission":
        with phase("policy_binding"):
            snapshot = scribe_capture_scope.resolve_capture_binding(
                workload_client_id=caller.workload_client_id,
                mount_context_token=caller.mount_context_token,
                interaction_ref=caller.interaction_ref,
                client_turn_id=caller.client_turn_id,
                settings=caller.settings,
                capture_settings=caller.capture_settings,
                workspace_id=data.workspace_id,
                appointment_ref=first["appointment_ref"],
                request_nonce=data.request_nonce,
            )
        if data.action == "preparation_context" and binding != snapshot.binding_ref:
            raise authority.AuthorityRequestDeniedError
        binding = snapshot.binding_ref
        last = current()
    if binding is None:
        raise authority.AuthorityRequestDeniedError
    operation = data.operation or (
        ReadOperation(kind="provider_encounter") if data.opening_nonce else None
    )
    receipt = None
    if operation is not None:
        with phase("operation"):
            receipt = await execute_capture_operation(
                caller=caller,
                binding_ref=binding,
                workspace_id=data.workspace_id,
                operation=operation,
                client_request_key=key,
                client=client,
                upload_origins=capture_settings.upload_origins,
            )
        check_async_capture_deadline()
    # Only the final mount row is published. All other lookups still execute
    # their full fresh authority checks without unused saved-note display reads.
    # A preparation request cannot contain an operation. Its current row was
    # just read after the last synchronous service boundary (mount or binding),
    # with no intervening service call or await. Do not repeat that same final
    # lookup. Ordinary reads still reauthorize after I/O, and mounts still
    # obtain their display status in the final publication lookup.
    if data.action != "preparation_context":
        last = current(include_status=data.action == "mount")
    envelope = {
        "contract_version": "1",
        "workspace_id": str(data.workspace_id),
        "binding_ref": binding,
        "request_nonce": str(data.request_nonce),
        "checked_at": datetime.now(UTC).isoformat(),
    }
    if data.action == "preparation_context":
        # This replaces the existing server-to-server negotiation only. No
        # admission/window mutation runs here. The native receiver consumes the
        # credential once in this invocation after its fresh account fence.
        result = {
            **envelope,
            "appointment_ref": last["appointment_ref"],
            "preparation": data.preparation,
            "context_token": issued.context_token,
            "interaction_ref": resolved.interaction_ref,
            "client_turn_id": str(turn),
        }
    elif data.action == "operation":
        assert operation is not None and receipt is not None
        result = {
            **envelope,
            "operation": operation.kind,
            "receipt": receipt.model_dump(mode="json"),
        }
    else:
        result = {
            **envelope,
            "appointment_ref": last["appointment_ref"],
            "encounter": last,
        }
        if receipt is not None:
            result["opening_review"] = {
                **envelope,
                "request_nonce": str(data.opening_nonce),
                "operation": "provider_encounter",
                "receipt": receipt.model_dump(mode="json"),
            }
    # Ordinary native responses contain no credentials. A preparation context
    # replaces the existing server-side negotiation and must never reach a device.
    return {
        "version": "1",
        "action": data.action,
        "actor_ref": data.expected_actor_ref,
        "tenant_ref": data.expected_tenant_ref,
        "result": result,
    }
