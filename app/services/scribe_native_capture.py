"""One native request composes existing Core decisions; no cached authority."""

import hashlib
import re
import time
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from app.configuration.authority import AuthorityKernelSettings
from app.configuration.scribe_capture import ScribeCaptureSettings
from app.configuration.scribe_encounters import ScribeEncounterSettings
from app.configuration.scribe_native import native_existing_patient_visits_enabled
from app.services import authority, scribe_capture_scope, scribe_encounters
from app.services import scribe_capture_note_edits as note_edits
from app.services import scribe_capture_notes as notes
from app.services import scribe_capture_protocol as protocol
from app.services.doctor_access_row_ownership import provider_record_access_row_scope
from app.services.provider_context_exchange import provider_exchange_blocked
from app.services.scribe_capture_admission import (
    AdmissionUnconfirmed,
    CaptureCaller,
    ConsentAttestation,
    admit_capture,
)
from app.services.scribe_capture_deadline import (
    check_async_capture_deadline,
    check_capture_deadline,
)
from app.services.scribe_capture_gateway import execute_capture_operation
from app.services.scribe_capture_note_request import NoteRequest
from app.services.scribe_capture_protocol import CaptureOperation, ReadOperation
from app.services.scribe_configured_capture_contract import (
    ConfiguredCaptureSelection,
    ConfiguredSessionCreate,
)
from app.services.scribe_encounter_contract import LookupRequest
from app.services.scribe_workflow import ScribeWorkflowClient
from app.utils.native_request_timing import phase

from activemodel.session_manager import global_session
from app.models.authority import MountContextToken
from sqlmodel import select

NATIVE_READ_OPERATIONS = frozenset(
    {
        "creator_review",
        "provider_encounter",
        "capabilities",
        "native_recording_context",
        "native_recording_preparation",
        # Existing public wrapping key read; no private key or key mutation.
        "encryption_key",
        "preparation_metadata",
    }
)


# Existing native renderer/queue and note workflow only. Ordinary `operation`
# remains read-only, and the generic Agent/Session Prep API is untouched.
NATIVE_WORKFLOW_OPERATIONS = NATIVE_READ_OPERATIONS | frozenset(
    {
        "prepare_recording_window",
        "prepare_continuation_window",
        "bind_key",
        "issue_lease",
        "renew_lease",
        "reserve_chunks",
        "authorize_upload",
        "chunk_receipt",
        "register_chunk",
        "complete",
        "decide_translation",
        "edit_transcript",
        "source_start",
        "source_start_sequence",
    }
)
WORKFLOW_ACTIONS = frozenset(
    {"workflow_operation", "workflow_admission", "workflow_notes"}
)


class NativeWorkflowCaller(CaptureCaller):
    def authorize(self, session, binding_ref, workspace_id):
        # Existing services call this again inside their locked commit boundary.
        # A global capture setting cannot replace the dedicated native gate.
        if not native_existing_patient_visits_enabled():
            raise authority.AuthorityKernelDisabledError
        return super().authorize(session, binding_ref, workspace_id)


class PreparedWindowContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    context_token: SecretStr = Field(repr=False)
    interaction_ref: str = Field(pattern=r"^[A-Za-z0-9._~-]{16,160}$")
    client_turn_id: UUID

    @model_validator(mode="after")
    def bounded_token(self):
        if not re.fullmatch(r"[!-~]{16,8192}", self.context_token.get_secret_value()):
            raise ValueError("invalid_prepared_context")
        return self


class NativeCaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    action: Literal[
        "mount",
        "operation",
        "preparation_context",
        "prepared_window",
        "prepared_admission",
        "fresh_admission",
        "prepared_native_read",
        "prepared_source",
        "workflow_operation",
        "workflow_admission",
        "workflow_notes",
    ]
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
    note_request: NoteRequest | None = Field(default=None, repr=False)
    opening_nonce: UUID | None = None
    preparation: Literal["admission", "recording_window"] | None = None
    prepared_context: PreparedWindowContext | None = Field(default=None, repr=False)
    retain_preparation_context: Literal[True] | None = None
    attestation: ConsentAttestation | None = None
    configured_capture: ConfiguredCaptureSelection | None = None
    include_native_preparation: Literal[True] | None = None
    include_preparation_metadata: Literal[True] | None = None

    @field_validator("include_preparation_metadata", mode="before")
    @classmethod
    def explicit_preparation_metadata(cls, value):
        if value is not None and value is not True:
            raise ValueError("invalid_preparation_metadata_opt_in")
        return value

    @model_validator(mode="after")
    def action_fields(self):
        if self.action in WORKFLOW_ACTIONS:
            if (
                any(
                    value is not None
                    for value in (
                        self.opening_nonce,
                        self.preparation,
                        self.prepared_context,
                        self.retain_preparation_context,
                        self.include_native_preparation,
                        self.include_preparation_metadata,
                    )
                )
                or self.binding_ref is None
            ):
                raise ValueError("invalid_native_workflow")
            if self.action == "workflow_admission":
                if (
                    self.operation is not None
                    or self.note_request is not None
                    or self.configured_capture is None
                ):
                    raise ValueError("invalid_native_workflow_admission")
            elif self.attestation is not None or self.configured_capture is not None:
                raise ValueError("unexpected_native_workflow_admission")
            elif self.action == "workflow_operation":
                if (
                    self.note_request is not None
                    or self.operation is None
                    or self.operation.kind not in NATIVE_WORKFLOW_OPERATIONS
                ):
                    raise ValueError("invalid_native_workflow_operation")
            elif (
                self.operation is not None
                or self.note_request is None
                or (
                    self.note_request.workspace_id,
                    self.note_request.binding_ref,
                    self.note_request.request_nonce,
                )
                != (self.workspace_id, self.binding_ref, self.request_nonce)
            ):
                raise ValueError("invalid_native_workflow_note")
            return self
        if self.note_request is not None:
            raise ValueError("unexpected_native_workflow_note")
        if self.retain_preparation_context is not None and self.action not in {
            "mount",
            "fresh_admission",
        }:
            raise ValueError("unexpected_context_retention")
        if self.include_native_preparation is not None and self.action not in {
            "prepared_admission",
            "fresh_admission",
        }:
            raise ValueError("unexpected_native_preparation")
        if self.include_preparation_metadata is not None and self.action not in {
            "prepared_admission",
            "fresh_admission",
        }:
            raise ValueError("unexpected_preparation_metadata")
        if (
            self.include_native_preparation is not None
            and self.include_preparation_metadata is not None
        ):
            raise ValueError("conflicting_admission_preparation")
        if self.action == "fresh_admission":
            if (
                self.binding_ref is None
                or self.prepared_context is not None
                or self.attestation is None
                or self.configured_capture is None
                or self.operation is not None
                or self.opening_nonce is not None
                or self.preparation is not None
                or self.retain_preparation_context is not True
            ):
                raise ValueError("invalid_fresh_admission")
            return self
        if self.action == "prepared_admission":
            if (
                self.binding_ref is None
                or self.prepared_context is None
                or self.attestation is None
                or self.configured_capture is None
                or self.operation is not None
                or self.opening_nonce is not None
                or self.preparation is not None
            ):
                raise ValueError("invalid_prepared_admission")
            return self
        if any(
            value is not None for value in (self.attestation, self.configured_capture)
        ):
            raise ValueError("unexpected_admission_fields")
        if self.action == "prepared_source":
            if (
                self.binding_ref is None
                or self.operation is None
                or self.operation.kind != "source_start_sequence"
                or self.operation.payload.first_sequence_number != 0
                or self.operation.payload.lease_generation != 1
                or self.prepared_context is None
                or self.opening_nonce is not None
                or self.preparation is not None
            ):
                raise ValueError("invalid_prepared_source")
            return self
        if self.action == "prepared_native_read":
            if (
                self.binding_ref is None
                or self.operation is None
                or self.operation.kind != "native_recording_preparation"
                or self.prepared_context is None
                or self.opening_nonce is not None
                or self.preparation is not None
            ):
                raise ValueError("invalid_prepared_native_read")
            return self
        if self.action == "prepared_window":
            if (
                self.binding_ref is None
                or self.operation is None
                or self.operation.kind != "prepare_recording_window"
                or self.prepared_context is None
                or self.opening_nonce is not None
                or self.preparation is not None
            ):
                raise ValueError("invalid_prepared_window")
            return self
        if self.prepared_context is not None:
            raise ValueError("unexpected_prepared_context")
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
    preparation_started = time.monotonic()
    if (
        data.preparation == "admission" or data.action == "fresh_admission"
    ) and not capture_settings.enabled:
        raise authority.AuthorityKernelDisabledError

    def lookup(*, include_status=False):
        if (
            data.action in WORKFLOW_ACTIONS
            and not native_existing_patient_visits_enabled()
        ):
            raise authority.AuthorityKernelDisabledError
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
                # Admission already supplies the binding created at mount.
                # Validate its complete current scope in every fresh lookup;
                # the durable admission service authorizes it again before I/O.
                expected_capture_binding_ref=(
                    data.binding_ref
                    if data.preparation == "admission"
                    or data.action in {"prepared_admission", "fresh_admission"}
                    else None
                ),
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

    async def execute_prepared(context: PreparedWindowContext) -> dict[str, object]:
        # The native route still verifies current Clerk identity, both workload
        # policies and encounter/capture enablement. These complete current
        # visit lookups retain its provider-session/view-as checks. Only token
        # issuance and new interaction construction are omitted; the ordinary
        # dispatcher locks and reauthorizes all token/clinical/consent
        # inputs before mutation and after remote I/O.
        assert data.binding_ref is not None
        token = context.context_token.get_secret_value()
        with global_session() as session:
            stored = session.exec(
                select(MountContextToken).where(
                    MountContextToken.token_hash
                    == hashlib.sha256(token.encode()).hexdigest()
                )
            ).one_or_none()
            if stored is None:
                raise authority.ContextTokenInvalidError
            now = datetime.now(UTC)
            if stored.issued_at.tzinfo is None:
                now = now.replace(tzinfo=None)
            if not 0 <= (now - stored.issued_at).total_seconds() < 30:
                raise authority.ContextTokenExpiredError

        class SameProviderCaller(CaptureCaller):
            def authorize(self, session, binding_ref, workspace_id):
                if (
                    data.action in WORKFLOW_ACTIONS
                    and not native_existing_patient_visits_enabled()
                ):
                    raise authority.AuthorityKernelDisabledError
                binding = super().authorize(session, binding_ref, workspace_id)
                if (
                    str(binding.actor_id) != data.expected_actor_ref
                    or str(binding.tenant_id) != data.expected_tenant_ref
                    or str(binding.appointment_id) != first["appointment_ref"]
                    or str(binding.patient_id) != first["patient_ref"]
                ):
                    raise authority.AuthorityRequestDeniedError
                stored = session.exec(
                    select(MountContextToken)
                    .where(
                        MountContextToken.token_hash
                        == hashlib.sha256(token.encode()).hexdigest()
                    )
                    .execution_options(populate_existing=True)
                ).one_or_none()
                if stored is None:
                    raise authority.ContextTokenInvalidError
                now = datetime.now(UTC)
                if stored.issued_at.tzinfo is None:
                    now = now.replace(tzinfo=None)
                if not 0 <= (now - stored.issued_at).total_seconds() < 30:
                    raise authority.ContextTokenExpiredError
                return binding

        caller = SameProviderCaller(
            workload_client_id=workload_client_id,
            mount_context_token=token,
            interaction_ref=context.interaction_ref,
            client_turn_id=context.client_turn_id,
            settings=authority_settings,
            capture_settings=capture_settings,
        )
        if data.action in {
            "prepared_admission",
            "fresh_admission",
            "workflow_admission",
        }:
            combined_metadata: protocol.PreparationMetadata | None = None
            prepared_session = None
            if (
                data.include_preparation_metadata or data.include_native_preparation
            ) and capture_settings.combined_session_preparation_enabled:

                async def prepare_session(
                    tenant: UUID,
                    payload: ConfiguredSessionCreate,
                    creator: str,
                    request_key: str,
                ):
                    nonlocal combined_metadata
                    result = await client.create_prepared_configured_session(
                        tenant,
                        payload,
                        creator_actor_id=creator,
                        idempotency_key=request_key,
                    )
                    combined_metadata = result.preparation_metadata
                    return result.admission

                prepared_session = prepare_session
            if data.action == "fresh_admission":
                # Same fixed preparation-entry budget before any durable claim.
                # This timer never starts again at context issuance or admission.
                check_async_capture_deadline()
                if not 0 <= time.monotonic() - preparation_started < 15:
                    raise authority.ContextTokenExpiredError
            with phase("operation"):
                admitted = await admit_capture(
                    caller=caller,
                    binding_ref=data.binding_ref,
                    workspace_id=data.workspace_id,
                    client_request_key=key,
                    attestation=data.attestation,
                    configured_capture=data.configured_capture,
                    client=client,
                    prepared_session=prepared_session,
                )
            check_async_capture_deadline()
            current()
            result: dict[str, object] = {
                "contract_version": "1",
                **{
                    name: str(value)
                    for name, value in asdict(admitted).items()
                    if name != "configured_capture"
                },
                "request_nonce": str(data.request_nonce),
                "checked_at": datetime.now(UTC).isoformat(),
            }
            if admitted.configured_capture is not None:
                result["configured_capture"] = admitted.configured_capture.model_dump(
                    mode="json"
                )
            if data.include_native_preparation:
                # Consent has committed. Run the unchanged read's fresh pre/post
                # authority checks before publishing one bounded native reply.
                from app.services.scribe_preparation_metadata import (
                    execute_native_recording_preparation,
                    native_preparation_from_metadata,
                    validate_admitted_preparation,
                )

                if prepared_session is not None:
                    scope, combined = validate_admitted_preparation(
                        caller,
                        data.binding_ref,
                        data.workspace_id,
                        admitted.session_id,
                        combined_metadata,
                    )
                    metadata = native_preparation_from_metadata(
                        scope, combined, data.workspace_id
                    )
                else:
                    with phase("preparation_metadata"):
                        metadata = await execute_native_recording_preparation(
                            caller=caller,
                            binding_ref=data.binding_ref,
                            workspace_id=data.workspace_id,
                            client_request_key="admission-native-preparation:"
                            + str(data.request_nonce),
                            client=client,
                        )
                if metadata.context.session_id != admitted.session_id:
                    raise authority.AuthorityRequestDeniedError
                check_async_capture_deadline()
                current()
                result["native_recording_preparation"] = metadata.model_dump(
                    mode="json"
                )
                result["checked_at"] = datetime.now(UTC).isoformat()
            elif data.include_preparation_metadata:
                # Desktop needs the complete existing creator/key/capability
                # receipt. Consent has committed; retain the read's current
                # pre/post authority checks and the final encounter fence.
                from app.services.scribe_preparation_metadata import (
                    execute_preparation_metadata,
                    validate_admitted_preparation,
                )

                if prepared_session is not None:
                    # Same ordinary scope and complete receipt validation after
                    # admission's durable accept/finish and fresh authority fences.
                    _, metadata = validate_admitted_preparation(
                        caller,
                        data.binding_ref,
                        data.workspace_id,
                        admitted.session_id,
                        combined_metadata,
                    )
                else:
                    with phase("preparation_metadata"):
                        metadata = await execute_preparation_metadata(
                            caller=caller,
                            binding_ref=data.binding_ref,
                            workspace_id=data.workspace_id,
                            scope=None,
                            client_request_key="admission-preparation-metadata:"
                            + str(data.request_nonce),
                            client=client,
                        )
                if metadata.review.session_id != admitted.session_id:
                    raise authority.AuthorityRequestDeniedError
                check_async_capture_deadline()
                current()
                result["preparation_metadata"] = metadata.model_dump(mode="json")
                result["checked_at"] = datetime.now(UTC).isoformat()
            return {
                "version": "1",
                "action": data.action,
                "actor_ref": data.expected_actor_ref,
                "tenant_ref": data.expected_tenant_ref,
                "result": result,
            }
        assert data.operation is not None
        with phase("operation"):
            receipt = await execute_capture_operation(
                caller=caller,
                binding_ref=data.binding_ref,
                workspace_id=data.workspace_id,
                operation=data.operation,
                client_request_key=key,
                client=client,
                upload_origins=capture_settings.upload_origins,
            )
        check_async_capture_deadline()
        current()
        return {
            "version": "1",
            "action": data.action,
            "actor_ref": data.expected_actor_ref,
            "tenant_ref": data.expected_tenant_ref,
            "result": {
                "contract_version": "1",
                "workspace_id": str(data.workspace_id),
                "binding_ref": data.binding_ref,
                "request_nonce": str(data.request_nonce),
                "checked_at": datetime.now(UTC).isoformat(),
                "operation": data.operation.kind,
                "receipt": receipt.model_dump(mode="json"),
            },
        }

    if data.action in {
        "prepared_window",
        "prepared_admission",
        "prepared_native_read",
        "prepared_source",
    }:
        assert data.prepared_context is not None
        return await execute_prepared(data.prepared_context)

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
    caller_type = (
        NativeWorkflowCaller if data.action in WORKFLOW_ACTIONS else CaptureCaller
    )
    caller = caller_type(
        workload_client_id=workload_client_id,
        mount_context_token=issued.context_token,
        interaction_ref=resolved.interaction_ref,
        client_turn_id=turn,
        settings=authority_settings,
        capture_settings=capture_settings,
    )
    if data.action in {"fresh_admission", "workflow_admission"}:
        # Only the server-to-server receiver sends this action. Keep issuance,
        # mount resolution and every fresh selection boundary above; share the
        # exact prepared admission/metadata implementation rather than memoizing
        # authority or holding SQL locks over consent/session I/O.
        context = PreparedWindowContext(
            context_token=SecretStr(issued.context_token),
            interaction_ref=resolved.interaction_ref,
            client_turn_id=turn,
        )
        answer = await execute_prepared(context)
        check_async_capture_deadline()
        if data.action == "workflow_admission":
            return answer
        if not 0 <= time.monotonic() - preparation_started < 30:
            raise authority.ContextTokenExpiredError
        # Private credential is stripped by the receiver before native output.
        # Its original issue epoch is retained; it does not authorize a next step.
        cast(dict[str, object], answer["result"])["prepared_context"] = {
            "context_token": issued.context_token,
            "interaction_ref": resolved.interaction_ref,
            "client_turn_id": str(turn),
        }
        return answer
    binding = data.binding_ref
    if data.action == "mount":
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
        binding = snapshot.binding_ref
        last = current()
    if binding is None:
        raise authority.AuthorityRequestDeniedError
    if data.action == "workflow_notes":
        assert data.note_request is not None
        note = data.note_request
        if not capture_settings.notes_enabled:
            raise notes.CaptureNoteDisabled
        current()
        if note.action == "templates":
            receipt = await notes.capture_note_templates(
                caller=caller,
                binding_ref=binding,
                workspace_id=data.workspace_id,
                expected_session_id=note.expected_session_id,
                client=client,
            )
        elif note.action == "template_preference":
            receipt = await notes.capture_template_preference(
                caller=caller,
                binding_ref=binding,
                workspace_id=data.workspace_id,
                expected_session_id=note.expected_session_id,
                client=client,
            )
        elif note.action in {"save", "resolve", "save_status"}:
            assert note.schema_ref is not None and note.edit is not None
            method = {
                "save": note_edits.save_capture_note,
                "resolve": note_edits.resolve_capture_note_save,
                "save_status": note_edits.check_capture_note_save,
            }[note.action]
            receipt = await method(
                caller=caller,
                binding_ref=binding,
                workspace_id=data.workspace_id,
                expected_session_id=note.expected_session_id,
                client=client,
                schema_ref=note.schema_ref,
                edit=note.edit,
            )
        else:
            assert note.schema_ref is not None
            receipt = await notes.capture_note(
                caller=caller,
                binding_ref=binding,
                workspace_id=data.workspace_id,
                expected_session_id=note.expected_session_id,
                client=client,
                schema_ref=note.schema_ref,
                prepare=note.action == "prepare",
                scratch_notes=note.scratch_notes,
            )
        check_async_capture_deadline()
        current()
        return {
            "version": "1",
            "action": data.action,
            "actor_ref": data.expected_actor_ref,
            "tenant_ref": data.expected_tenant_ref,
            "result": {
                "contract_version": "1",
                "workspace_id": str(data.workspace_id),
                "binding_ref": binding,
                "request_nonce": str(data.request_nonce),
                "checked_at": datetime.now(UTC).isoformat(),
                "action": note.action,
                "receipt": receipt.model_dump(mode="json"),
            },
        }
    operation = data.operation or (
        ReadOperation(kind="provider_encounter") if data.opening_nonce else None
    )
    receipt = None
    opening_recovery = False
    if operation is not None:
        try:
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
        except AdmissionUnconfirmed:
            if data.action != "mount" or data.opening_nonce is None:
                raise
            # The existing admission remains uncertain. Opening is read-only:
            # publish recovery only after the final current encounter check,
            # never a fresh/ready receipt or an admission/source replay.
            opening_recovery = True
        check_async_capture_deadline()
    # Only the final mount row is published. All other lookups still execute
    # their full fresh authority checks without unused saved-note display reads.
    # A preparation request cannot contain an operation. Its current row was
    # just read after the last synchronous service boundary (mount or binding),
    # with no intervening service call or await. Do not repeat that same final
    # lookup. Admission's existing binding is checked in these lookups, rather
    # than resolving/creating it again. Ordinary reads still reauthorize after
    # I/O, and mounts still
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
    elif data.action in {"operation", "workflow_operation"}:
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
        if opening_recovery:
            result["opening_recovery"] = {
                "contract_version": "1",
                "workspace_id": str(data.workspace_id),
                "binding_ref": binding,
                "request_nonce": str(data.opening_nonce),
                "disposition": "provider_operation_conflict",
            }
        if data.retain_preparation_context:
            # Private server-to-server field, stripped before native publication.
            # Reuse never skips fresh provider/token/consent authorization.
            result["prepared_context"] = {
                "context_token": issued.context_token,
                "interaction_ref": resolved.interaction_ref,
                "client_turn_id": str(turn),
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
