"""Authorize the retained recorder's operations through one durable Core admission."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel

from app.services import scribe_capture_protocol as protocol
from app.services.scribe_capture_admission import (
    AdmissionConsentRequired,
    AdmittedCaptureScope,
    CaptureCaller,
    authorize_admitted_capture,
)
from app.services.scribe_capture_recovery_contract import (
    MAX_RECOVERY_STATE_BYTES,
    CaptureRecoveryReceipt,
    CaptureRecoveryState,
)
from app.services.scribe_capture_source_contract import MAX_CAPTURE_SOURCE_BYTES
from app.services.scribe_capture_working_contract import (
    MAX_WORKING_BYTES,
    CaptureWorkingTranscript,
    canonical_hash,
)
from app.services.scribe_configured_capture_contract import ConfiguredSessionCreate
from app.services.scribe_creator_edit_contract import CreatorTranscriptEditReceiptV1
from app.services.scribe_creator_review_contract import (
    MAX_CREATOR_REVIEW_BYTES,
    CreatorDecisionReceipt,
    CreatorReviewView,
)
from app.services.scribe_source_extension_contract import validate_native_extension
from app.services.scribe_source_start_contract import (
    NativeStartDecision,
    NativeStartReceipt,
    NativeStartSequenceResult,
    NativeUnstartedRetirement,
)
from app.services.scribe_workflow import (
    CaptureSession,
    CaptureSource,
    CaptureTranscript,
    CaptureTranscriptionStatus,
    ScribeWorkflowClient,
    ScribeWorkflowConflictError,
    ScribeWorkflowError,
    ScribeWorkflowRejectedError,
    SessionCreate,
)


class CaptureGatewayError(Exception):
    """Value-free facade error. A remote effect may already have committed."""


class CaptureOperationUnconfirmed(CaptureGatewayError):
    pass


class CaptureReceiptInvalid(CaptureGatewayError):
    pass


class CaptureOperationUnavailable(CaptureGatewayError):
    pass


def _audio_scope(
    encoded: str,
    scope: AdmittedCaptureScope,
    object_id: UUID,
    *,
    sequence: int | None = None,
    channel: str | None = None,
) -> protocol.AudioScope:
    decoded = base64.b64decode(
        encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
    )
    aad = protocol.AudioScope.model_validate_json(decoded)
    canonical = json.dumps(
        aad.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode()
    if (
        base64.urlsafe_b64encode(canonical).decode().rstrip("=") != encoded
        or aad.tenant_id != scope.tenant_id
        or aad.session_id != scope.session_id
        or aad.object_id != object_id
        or sequence is not None
        and aad.sequence_number != sequence
        or channel is not None
        and aad.channel != channel
    ):
        raise ValueError
    return aad


def _reservation(receipt: protocol.Reservation, scope: AdmittedCaptureScope) -> None:
    if receipt.session_id != scope.session_id:
        raise ValueError
    _audio_scope(
        receipt.additional_authenticated_data,
        scope,
        receipt.object_id,
        sequence=receipt.sequence_number,
        channel=receipt.channel,
    )


def _current(deadline: datetime, now: datetime) -> None:
    if not now < deadline <= now + timedelta(seconds=905):
        raise ValueError


def _profile_receipt(
    value: protocol.CaptureProfileReceipt,
    scope: AdmittedCaptureScope,
    now: datetime,
) -> protocol.CaptureProfileReceipt:
    receipt = protocol.CaptureProfileReceipt.model_validate(
        value.model_dump(mode="json")
    )
    if receipt.session_id != scope.session_id or receipt.created_at > now + timedelta(
        seconds=5
    ):
        raise ValueError
    return receipt


def _acknowledged_profile(
    operation: protocol.CaptureOperation,
    profile: protocol.CaptureProfileReceipt | None,
    scope: AdmittedCaptureScope,
    now: datetime,
) -> protocol.CaptureProfileReceipt | None:
    receipt_id = getattr(operation, "profile_receipt_id", None)
    if receipt_id is None:
        if profile is not None:
            raise ValueError
        return None
    if profile is None:
        raise ValueError
    selected = _profile_receipt(profile, scope, now)
    if selected.receipt_id != receipt_id:
        raise ValueError
    if isinstance(operation, protocol.ReserveChunks) and (
        operation.payload.channel not in selected.request.channels
        or operation.payload.first_sequence_number + operation.payload.count
        > selected.maximum_chunks_per_channel
    ):
        raise ValueError
    return selected


def _upload_target(
    url: str, origins: tuple[str, ...], aad: protocol.AudioScope
) -> None:
    parsed = urlsplit(url)
    origin = f"https://{parsed.netloc}"
    suffix = (
        f"/tenants/{aad.tenant_id}/sessions/{aad.session_id}/{aad.channel}/"
        f"{aad.sequence_number:08d}-{aad.object_id}.enc"
    )
    if (
        parsed.scheme != "https"
        or parsed.port not in (None, 443)
        or parsed.username
        or parsed.password
        or parsed.fragment
        or origin not in origins
        or not parsed.path.endswith(suffix)
        or not parsed.query
        or any(c.isspace() for c in url)
        or "\\" in url
        or "%" in parsed.path
        or "/../" in parsed.path
    ):
        raise ValueError


def _validate_source_start(
    operation: protocol.NativeSourceStart,
    result: BaseModel,
    scope: AdmittedCaptureScope,
    now: datetime,
) -> BaseModel:
    try:
        if not isinstance(scope.session_request, ConfiguredSessionCreate):
            raise ValueError
        if (
            operation.action == "stop"
            and result.model_dump(mode="json").get("state") == "unstarted"
        ):
            checked = NativeUnstartedRetirement.model_validate(
                result.model_dump(mode="json")
            )
            receipt = checked
            if receipt.retired_at > now or not hmac.compare_digest(
                receipt.owner_proof_sha256,
                hashlib.sha256(
                    operation.payload.owner_proof.encode("ascii")
                ).hexdigest(),
            ):
                raise ValueError
        elif operation.action == "authorize":
            checked = NativeStartDecision.model_validate(result.model_dump(mode="json"))
            receipt = checked.receipt
            if not hmac.compare_digest(checked.secret, operation.payload.owner_proof):
                raise ValueError
            if (
                receipt.state != "issued"
                or receipt.issued_at is None
                or receipt.expires_at is None
                or receipt.issued_at > now
                or receipt.expires_at <= now
                or receipt.expires_at <= receipt.issued_at
                or receipt.expires_at > receipt.issued_at + timedelta(seconds=15)
                or receipt.issued_at >= receipt.preparation_expires_at
                or receipt.expires_at > receipt.capture_authorized_until
            ):
                raise ValueError
        else:
            checked = NativeStartReceipt.model_validate(result.model_dump(mode="json"))
            receipt = checked
            if operation.action == "prepare" and (
                receipt.state != "prepared"
                or receipt.preparation_expires_at <= now
                or receipt.preparation_expires_at > receipt.capture_authorized_until
                or receipt.issued_at is not None
                or receipt.expires_at is not None
            ):
                raise ValueError
            if operation.action == "cancel" and receipt.state != "cancelled":
                raise ValueError
            if operation.action == "stop" and receipt.state not in {
                "stopped",
                "cancelled",
                "expired",
            }:
                raise ValueError
        expected = operation.payload
        if (
            receipt.session_id != scope.session_id
            or receipt.creator_actor_id != scope.creator_actor_id
            or receipt.consent_record_id != scope.session_request.consent_record_id
            or receipt.attempt_id != expected.attempt_id
            or receipt.source != expected.source
            or receipt.lease_id != expected.lease_id
            or receipt.lease_receipt_id != expected.lease_receipt_id
            or receipt.lease_generation != expected.lease_generation
            or receipt.first_sequence_number != expected.first_sequence_number
            or receipt.capture_authorized_until != expected.capture_authorized_until
            or (operation.action != "stop" and receipt.capture_authorized_until <= now)
        ):
            raise ValueError
        if isinstance(receipt, NativeUnstartedRetirement):
            return checked
        if receipt.preparation_expires_at > receipt.capture_authorized_until or (
            receipt.state not in {"issued", "stopped"}
            and (receipt.issued_at is not None or receipt.expires_at is not None)
        ):
            raise ValueError
        if receipt.state == "stopped" and (
            receipt.issued_at is None
            or receipt.expires_at is None
            or receipt.issued_at > now
            or receipt.expires_at <= receipt.issued_at
            or receipt.expires_at > receipt.issued_at + timedelta(seconds=15)
            or receipt.issued_at >= receipt.preparation_expires_at
            or receipt.expires_at > receipt.capture_authorized_until
        ):
            raise ValueError
        return checked
    except (ValueError, TypeError, AttributeError):
        raise CaptureReceiptInvalid from None


def validate_receipt(
    operation: protocol.CaptureOperation,
    receipt: BaseModel,
    scope: AdmittedCaptureScope,
    *,
    upload_origins: tuple[str, ...],
    now: datetime | None = None,
    capture_profile: protocol.CaptureProfileReceipt | None = None,
) -> BaseModel:
    """Validate even injected clients; retained or malformed receipts grant nothing."""
    if isinstance(operation, protocol.NativeSourceExtension):
        try:
            if (
                not isinstance(scope.session_request, ConfiguredSessionCreate)
                or scope.creator_actor_id is None
            ):
                raise ValueError
            return validate_native_extension(
                operation,
                receipt.model_dump(mode="json"),
                scope.session_id,
                scope.creator_actor_id,
                now or datetime.now(UTC),
                scope.session_request.consent_record_id,
            )
        except (ValueError, TypeError, AttributeError):
            raise CaptureReceiptInvalid from None
    if isinstance(operation, protocol.PrepareContinuationWindow):
        from app.services.scribe_continuation_window import (
            validate_prepared_continuation_window,
        )

        return validate_prepared_continuation_window(operation, receipt, scope)
    if isinstance(operation, protocol.NativeSourceStart):
        return _validate_source_start(
            operation, receipt, scope, now or datetime.now(UTC)
        )
    types: dict[str, type[BaseModel]] = {
        "encryption_key": protocol.EncryptionKey,
        "capabilities": protocol.LongVisitCapabilities
        if getattr(operation, "profile_receipt_id", None) is not None
        else protocol.Capabilities,
        "capture_profile": protocol.CaptureProfileReceipt,
        "select_capture_profile": protocol.CaptureProfileReceipt,
        "bind_key": protocol.KeyReceipt,
        "issue_lease": protocol.LeaseReceipt,
        "renew_lease": protocol.LeaseReceipt,
        "capture_lease_state": protocol.LeaseState,
        "reserve_chunks": protocol.ReservationReceipt,
        "authorize_upload": protocol.UploadReceipt,
        "chunk_receipt": protocol.ChunkReceipt,
        "register_chunk": protocol.RegisteredChunk,
        "complete": CaptureSession,
        "transcription_status": CaptureTranscriptionStatus,
        "transcript": CaptureTranscript,
        "working_transcript": CaptureWorkingTranscript,
        "correct_transcript": CaptureWorkingTranscript,
        "capture_recovery_state": CaptureRecoveryState,
        "recover_capture": CaptureRecoveryReceipt,
        "capture_source": CaptureSource,
        "correct_capture_source": CaptureSource,
        "creator_review": CreatorReviewView,
        "decide_translation": CreatorDecisionReceipt,
        "edit_transcript": CreatorTranscriptEditReceiptV1,
    }
    try:
        result = types[operation.kind].model_validate(receipt.model_dump(mode="json"))
        at = now or datetime.now(UTC)
        _acknowledged_profile(operation, capture_profile, scope, at)
        if getattr(result, "session_id", scope.session_id) != scope.session_id:
            raise ValueError
        if isinstance(
            result,
            (CreatorReviewView, CreatorDecisionReceipt, CreatorTranscriptEditReceiptV1),
        ) and (
            scope.creator_actor_id is None
            or len(result.model_dump_json().encode("utf-8")) > MAX_CREATOR_REVIEW_BYTES
        ):
            raise ValueError
        if isinstance(result, protocol.CaptureProfileReceipt):
            _profile_receipt(result, scope, at)
            if (
                isinstance(operation, protocol.SelectCaptureProfile)
                and result.request != operation.payload
            ):
                raise ValueError
        if isinstance(operation, protocol.EditTranscript) and (
            not isinstance(result, CreatorTranscriptEditReceiptV1)
            or result.working_transcript_id
            != operation.payload.expected_working_transcript_id
            or result.working_transcript_version
            != operation.payload.expected_working_transcript_version + 1
        ):
            raise ValueError
        if isinstance(result, CaptureRecoveryReceipt) and (
            not isinstance(operation, protocol.RecoverCapture)
            or result.acknowledged_by_practitioner_id
            != scope.session_request.practitioner_id
            or result.request_sha256 != canonical_hash(operation.payload)
            or result.lease.issued_at > at + timedelta(seconds=5)
        ):
            raise ValueError
        # A replay may contain an expired lease. This immutable receipt is
        # evidence only; current reservations still gate every recording run.
        if isinstance(result, CaptureRecoveryState) and (
            result.history.session_id != scope.session_id
            or len(result.model_dump_json().encode("utf-8")) > MAX_RECOVERY_STATE_BYTES
            or any(
                row.acknowledged_by_practitioner_id
                != scope.session_request.practitioner_id
                or row.lease.issued_at > at + timedelta(seconds=5)
                for row in result.history.recoveries
            )
            or (
                result.current_lease is not None
                and result.current_lease.issued_at > at + timedelta(seconds=5)
            )
        ):
            raise ValueError
        if (
            isinstance(result, CaptureTranscript)
            and len(result.model_dump_json().encode("utf-8")) > 4_194_304
        ):
            raise ValueError
        if isinstance(result, CaptureSource):
            result.validate_practitioner(scope.session_request.practitioner_id)
            if (
                len(result.model_dump_json().encode("utf-8")) > MAX_CAPTURE_SOURCE_BYTES
                or result.raw_transcript.created_at > at + timedelta(seconds=5)
                or result.working_transcript.created_at > at + timedelta(seconds=5)
                or any(
                    row.lease.issued_at > at + timedelta(seconds=5)
                    for row in result.recovery_history.recoveries
                )
            ):
                raise ValueError
        if isinstance(result, (CaptureWorkingTranscript, CaptureSource)):
            working_receipt = (
                result.working_transcript
                if isinstance(result, CaptureSource)
                else result
            )
            if (
                len(working_receipt.model_dump_json().encode("utf-8"))
                > MAX_WORKING_BYTES
            ):
                raise ValueError
            if isinstance(
                operation, (protocol.CorrectTranscript, protocol.CorrectCaptureSource)
            ) and (
                working_receipt.raw_transcript_id != operation.payload.raw_transcript_id
                or working_receipt.raw_transcript_sha256
                != operation.payload.raw_transcript_sha256
                or working_receipt.previous_working_sha256
                != operation.payload.expected_working_sha256
                or working_receipt.working_source.source_transcript_version
                != operation.payload.expected_working_version + 1
            ):
                raise ValueError
            if isinstance(
                operation, (protocol.CorrectTranscript, protocol.CorrectCaptureSource)
            ):
                segments = {
                    s.source_segment_id: s
                    for s in working_receipt.working_source.segments
                }
                for edit in operation.payload.corrections:
                    saved = segments[edit.source_segment_id]
                    if (
                        saved.content != edit.content
                        or saved.speaker_role != edit.speaker_role
                    ):
                        raise ValueError
        if isinstance(operation, protocol.BindKey):
            assert isinstance(result, protocol.KeyReceipt)
            if result.key_version != operation.payload.key_version:
                raise ValueError
        elif isinstance(operation, (protocol.IssueLease, protocol.RenewLease)):
            assert isinstance(result, protocol.LeaseReceipt)
            _current(result.expires_at, at)
            if (
                not timedelta(0)
                < result.expires_at - result.issued_at
                <= timedelta(seconds=900)
            ):
                raise ValueError
            if isinstance(operation, protocol.RenewLease):
                if (
                    result.lease_id != operation.lease_id
                    or result.generation != operation.payload.expected_generation + 1
                ):
                    raise ValueError
            elif result.generation != 1:
                raise ValueError
        elif isinstance(operation, protocol.ReserveChunks):
            assert isinstance(result, protocol.ReservationReceipt)
            _current(result.capture_authorized_until, at)
            if (
                len(result.reservations) != operation.payload.count
                or len({r.object_id for r in result.reservations})
                != operation.payload.count
            ):
                raise ValueError
            for index, item in enumerate(result.reservations):
                _reservation(item, scope)
                if (
                    item.sequence_number
                    != operation.payload.first_sequence_number + index
                    or item.channel != operation.payload.channel
                ):
                    raise ValueError
        elif isinstance(operation, protocol.AuthorizeUpload):
            assert isinstance(result, protocol.UploadReceipt)
            _current(result.expires_at, at)
            if result.object_id != operation.object_id:
                raise ValueError
            aad = _audio_scope(
                result.additional_authenticated_data, scope, operation.object_id
            )
            _upload_target(result.upload_url, upload_origins, aad)
        elif isinstance(operation, protocol.ReadChunk):
            assert isinstance(result, protocol.ChunkReceipt)
            if result.reservation.object_id != operation.object_id:
                raise ValueError
            _reservation(result.reservation, scope)
        elif isinstance(operation, protocol.RegisterChunk):
            if any(
                getattr(result, key) != value
                for key, value in operation.payload.model_dump().items()
            ):
                raise ValueError
        elif isinstance(operation, protocol.CompleteCapture):
            assert isinstance(result, CaptureSession)
            if result.id != scope.session_id or result.status not in {
                "uploaded",
                "processing",
                "transcript_ready",
                "handed_off",
            }:
                raise ValueError
            if any(
                getattr(result, key) != getattr(scope.session_request, key)
                for key in SessionCreate.model_fields
            ):
                raise ValueError
        return result
    except (ValueError, TypeError, AttributeError, AssertionError, KeyError):
        raise CaptureReceiptInvalid from None


async def execute_capture_operation(
    *,
    caller: CaptureCaller,
    binding_ref: str,
    workspace_id: UUID,
    operation: protocol.CaptureOperation,
    client_request_key: str,
    client: ScribeWorkflowClient,
    upload_origins: tuple[str, ...] = (),
) -> BaseModel:
    try:
        if not re.fullmatch(r"[!-~]{8,255}", client_request_key):
            raise ValueError
        operation = protocol.OPERATION_ADAPTER.validate_python(operation.model_dump())
    except (ValueError, TypeError, AttributeError):
        raise CaptureReceiptInvalid from None
    if (
        isinstance(operation, protocol.NativeSourceStartSequence)
        and client_request_key == operation.authorize_idempotency_key
    ):
        raise CaptureReceiptInvalid
    try:
        scope = authorize_admitted_capture(caller, binding_ref, workspace_id)
    except AdmissionConsentRequired:
        # authorize_admitted_capture checks current caller/binding access before
        # discovering that no admission exists. Other conflicts, revocations and
        # uncertain admission states must never masquerade as a fresh encounter.
        if operation.kind == "provider_encounter":
            return protocol.ProviderEncounter(status="not_prepared", review=None)
        raise
    if isinstance(operation, protocol.PrepareRecordingWindow):
        from app.services.scribe_recording_window import execute_recording_window

        return await execute_recording_window(
            caller=caller,
            binding_ref=binding_ref,
            workspace_id=workspace_id,
            scope=scope,
            operation=operation,
            client_request_key=client_request_key,
            client=client,
            upload_origins=upload_origins,
        )
    if isinstance(operation, protocol.NativeSourceStartSequence):
        # Both original intents and stop custody already live in the native
        # journal. Reuse each existing decision and its post-I/O access check.
        prepared = await _execute_creator_review(
            caller,
            binding_ref,
            workspace_id,
            scope,
            protocol.NativeSourceStart(
                kind="source_start", action="prepare", payload=operation.payload
            ),
            client_request_key,
            client,
        )
        # A cancelled request cannot proceed to the next decision. A changed
        # admission must be rejected before sending the original source proof
        # to any replacement session.
        await asyncio.sleep(0)
        if authorize_admitted_capture(caller, binding_ref, workspace_id) != scope:
            raise CaptureReceiptInvalid
        authorized = await _execute_creator_review(
            caller,
            binding_ref,
            workspace_id,
            scope,
            protocol.NativeSourceStart(
                kind="source_start", action="authorize", payload=operation.payload
            ),
            operation.authorize_idempotency_key,
            client,
        )
        try:
            return NativeStartSequenceResult.model_validate(
                {
                    "preparation": prepared.model_dump(),
                    "authorization": authorized.model_dump(),
                }
            )
        except ValueError:
            raise CaptureReceiptInvalid from None
    if operation.kind == "provider_encounter":
        if not isinstance(scope.session_request, ConfiguredSessionCreate):
            raise CaptureOperationUnavailable
        view = await _execute_creator_review(
            caller,
            binding_ref,
            workspace_id,
            scope,
            protocol.ReadOperation(kind="creator_review"),
            client_request_key,
            client,
        )
        if not isinstance(view, CreatorReviewView):
            raise CaptureReceiptInvalid
        return protocol.ProviderEncounter(status="available", review=view)
    if operation.kind == "native_recording_context":
        if not isinstance(scope.session_request, ConfiguredSessionCreate):
            raise CaptureOperationUnavailable
        # Reuse the creator/consent receiver and the post-I/O admission check.
        # The Scribe tenant is an admitted binding, not the provider's Core
        # authority-tenant reference or a caller-supplied namespace.
        view = await _execute_creator_review(
            caller,
            binding_ref,
            workspace_id,
            scope,
            protocol.ReadOperation(kind="creator_review"),
            client_request_key,
            client,
        )
        if not isinstance(view, CreatorReviewView) or (
            view.status != "created" and view.status != "capturing"
        ):
            raise CaptureOperationUnavailable
        return protocol.NativeRecordingContext(
            workspace_id=workspace_id,
            tenant_id=scope.tenant_id,
            session_id=scope.session_id,
            status=view.status,
        )
    if (
        operation.kind == "source_extend"
        and not caller.capture_settings.long_visits_enabled
    ):
        raise CaptureOperationUnavailable
    if isinstance(operation, protocol.PrepareContinuationWindow):
        body = operation.payload
        if (
            not isinstance(scope.session_request, ConfiguredSessionCreate)
            or scope.creator_actor_id is None
            or client_request_key
            in {body.renew_idempotency_key, body.reservation_idempotency_key}
        ):
            raise CaptureOperationUnavailable
        # Preserve the exact downstream keys used by ordinary component replay.
        # The native reservation journal may resume through that older path.
        mapped = operation.model_copy(
            update={
                "payload": body.model_copy(
                    update={
                        "renew_idempotency_key": component_request_key(
                            scope, "renew_lease", body.renew_idempotency_key
                        ),
                        "reservation_idempotency_key": component_request_key(
                            scope, "reserve_chunks", body.reservation_idempotency_key
                        ),
                    }
                )
            }
        )
        return await _execute_creator_review(
            caller, binding_ref, workspace_id, scope, mapped, client_request_key, client
        )
    if operation.kind in {
        "creator_review",
        "decide_translation",
        "edit_transcript",
        "source_start",
        "source_extend",
    }:
        return await _execute_creator_review(
            caller,
            binding_ref,
            workspace_id,
            scope,
            operation,
            client_request_key,
            client,
        )
    working = operation.kind in {"working_transcript", "correct_transcript"}
    recovery = operation.kind in {"recover_capture", "capture_recovery_state"}
    source = operation.kind in {"capture_source", "correct_capture_source"}
    configured = isinstance(scope.session_request, ConfiguredSessionCreate)
    if configured and (
        working
        or recovery
        or source
        or operation.kind in {"transcript", "transcription_status"}
    ):
        raise CaptureOperationUnavailable
    if (
        isinstance(operation, protocol.SelectCaptureProfile)
        and not caller.capture_settings.long_visits_enabled
    ):
        raise CaptureOperationUnavailable
    if source and not caller.capture_settings.source_envelopes_enabled:
        raise CaptureOperationUnavailable
    if working and not caller.capture_settings.working_transcripts_enabled:
        raise CaptureOperationUnavailable
    if recovery and not caller.capture_settings.recovery_enabled:
        raise CaptureOperationUnavailable
    creator_window = (
        configured
        and isinstance(
            operation,
            (protocol.IssueLease, protocol.RenewLease, protocol.ReserveChunks),
        )
        and getattr(operation, "profile_receipt_id", None) is None
    )
    if creator_window and scope.creator_actor_id is None:
        raise CaptureOperationUnavailable
    # The creator preparation receiver checks the original creator and consent
    # in the mutation transaction. Other operations retain the separate review.
    # The status receiver rechecks current processing consent, including before
    # global encryption-key reads. The operation receiver checks it atomically.
    try:
        if configured and not creator_window:
            await _execute_creator_review(
                caller,
                binding_ref,
                workspace_id,
                scope,
                protocol.ReadOperation(kind="creator_review"),
                "configured-capture-current",
                client,
            )
        elif not configured:
            async with asyncio.timeout(30):
                status = await client.get_capture_status(
                    scope.tenant_id, scope.session_id
                )
            if status.session_id != scope.session_id:
                raise CaptureReceiptInvalid
            if (
                operation.kind == "transcript" or working
            ) and not status.transcript_available:
                raise CaptureOperationUnavailable
    except (ScribeWorkflowConflictError, ScribeWorkflowRejectedError):
        raise CaptureOperationUnavailable from None
    except (ScribeWorkflowError, TimeoutError):
        raise CaptureOperationUnconfirmed from None
    if authorize_admitted_capture(caller, binding_ref, workspace_id) != scope:
        raise CaptureReceiptInvalid
    capture_profile = None
    if getattr(operation, "profile_receipt_id", None) is not None:
        profile_read = protocol.ReadOperation(kind="capture_profile")
        try:
            async with asyncio.timeout(30):
                remote_profile = await client.capture_operation(
                    scope.tenant_id,
                    scope.session_id,
                    profile_read,
                    idempotency_key="core-capture-profile-read",
                )
        except (ScribeWorkflowConflictError, ScribeWorkflowRejectedError):
            raise CaptureOperationUnavailable from None
        except (ScribeWorkflowError, TimeoutError):
            raise CaptureOperationUnconfirmed from None
        if authorize_admitted_capture(caller, binding_ref, workspace_id) != scope:
            raise CaptureReceiptInvalid
        checked_profile = validate_receipt(
            profile_read,
            remote_profile,
            scope,
            upload_origins=upload_origins,
        )
        if not isinstance(checked_profile, protocol.CaptureProfileReceipt):
            raise CaptureReceiptInvalid
        try:
            capture_profile = _acknowledged_profile(
                operation,
                checked_profile,
                scope,
                datetime.now(UTC),
            )
        except (ValueError, TypeError, AttributeError):
            raise CaptureReceiptInvalid from None
    raw = None
    if working:
        try:
            async with asyncio.timeout(30):
                raw = await client.get_capture_transcript(
                    scope.tenant_id, scope.session_id
                )
            raw = CaptureTranscript.model_validate(raw.model_dump(mode="json"))
            if raw.session_id != scope.session_id:
                raise CaptureReceiptInvalid
            if isinstance(operation, protocol.CorrectTranscript) and (
                operation.payload.raw_transcript_id != raw.id
                or operation.payload.raw_transcript_sha256 != canonical_hash(raw)
            ):
                raise CaptureReceiptInvalid
        except (ScribeWorkflowConflictError, ScribeWorkflowRejectedError):
            raise CaptureOperationUnavailable from None
        except (ScribeWorkflowError, TimeoutError):
            raise CaptureOperationUnconfirmed from None
        except (ValueError, TypeError, AttributeError):
            raise CaptureReceiptInvalid from None
        if authorize_admitted_capture(caller, binding_ref, workspace_id) != scope:
            raise CaptureReceiptInvalid
    key = component_request_key(scope, operation.kind, client_request_key)
    try:
        async with asyncio.timeout(30):
            extra = (
                {"practitioner_id": scope.session_request.practitioner_id}
                if working or recovery or source
                else {"creator_actor_id": scope.creator_actor_id}
                if creator_window
                else {}
            )
            result = await client.capture_operation(
                scope.tenant_id,
                scope.session_id,
                operation,
                idempotency_key=key,
                **extra,
            )
    except (ScribeWorkflowConflictError, ScribeWorkflowRejectedError):
        raise CaptureOperationUnavailable from None
    except (ScribeWorkflowError, TimeoutError):
        raise CaptureOperationUnconfirmed from None
    # The remote receipt/operation remains durable in Scribe if access changed.
    if authorize_admitted_capture(caller, binding_ref, workspace_id) != scope:
        raise CaptureReceiptInvalid
    checked = validate_receipt(
        operation,
        result,
        scope,
        upload_origins=upload_origins,
        capture_profile=capture_profile,
    )
    if working:
        try:
            assert raw is not None and isinstance(checked, CaptureWorkingTranscript)
            checked.validate_raw(raw, scope.session_request.practitioner_id)
        except (ValueError, TypeError, AssertionError):
            raise CaptureReceiptInvalid from None
    return checked


async def _execute_creator_review(
    caller: CaptureCaller,
    binding_ref: str,
    workspace_id: UUID,
    scope: AdmittedCaptureScope,
    operation: protocol.CaptureOperation,
    key: str,
    client: ScribeWorkflowClient,
) -> BaseModel:
    if scope.creator_actor_id is None or (
        operation.kind in {"source_start", "source_extend"}
        and not isinstance(scope.session_request, ConfiguredSessionCreate)
    ):
        raise CaptureOperationUnavailable
    try:
        # No pre-read or automatic retry: an exact replay must reach Scribe's
        # immutable receipt recovery even when its first reply was lost.
        async with asyncio.timeout(30):
            result = await client.capture_operation(
                scope.tenant_id,
                scope.session_id,
                operation,
                idempotency_key=key,
                creator_actor_id=scope.creator_actor_id,
            )
    except (ScribeWorkflowConflictError, ScribeWorkflowRejectedError):
        raise CaptureOperationUnavailable from None
    except (ScribeWorkflowError, TimeoutError):
        raise CaptureOperationUnconfirmed from None
    # Retained remote evidence grants nothing after local revocation or switch.
    if authorize_admitted_capture(caller, binding_ref, workspace_id) != scope:
        raise CaptureReceiptInvalid
    return validate_receipt(operation, result, scope, upload_origins=())


def component_request_key(
    scope: AdmittedCaptureScope, kind: str, client_request_key: str
) -> str:
    """Stable component namespace shared by direct and grouped native requests."""
    return (
        "core-capture-"
        + hashlib.sha256(
            json.dumps(
                [scope.operation_ref, kind, client_request_key],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
