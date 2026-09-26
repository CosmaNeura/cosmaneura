from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Literal, Protocol, TypeVar
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from azure.core.credentials import AccessToken
from azure.core.exceptions import AzureError
from azure.identity.aio import DefaultAzureCredential
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    ValidationError,
    model_validator,
)

from app.configuration.scribe_workflow import (
    SCRIBE_WORKFLOW_SETTINGS,
    ScribeWorkflowSettings,
)
from app.services import scribe_capture_protocol as capture_protocol
from app.services.scribe_capture_deadline import check_async_capture_deadline
from app.services.scribe_capture_recovery_contract import (
    MAX_RECOVERY_STATE_BYTES,
    CaptureRecoveryReceipt,
    CaptureRecoveryState,
)
from app.services.scribe_capture_source_contract import (
    MAX_CAPTURE_SOURCE_BYTES,
    CaptureSourceEnvelope,
)
from app.services.scribe_capture_working_contract import (
    MAX_WORKING_BYTES,
    CaptureWorkingTranscript,
)
from app.services.scribe_configured_capture_contract import (
    ConfiguredSessionCreate,
    ConfiguredSessionReceipt,
)
from app.services.scribe_creator_review_contract import (
    MAX_CREATOR_REVIEW_BYTES,
    CreatorDecisionReceipt,
    CreatorReviewView,
)


class ScribeWorkflowError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ScribeWorkflowDisabledError(ScribeWorkflowError):
    pass


class ScribeWorkflowRetryableError(ScribeWorkflowError):
    pass


class ScribeWorkflowRejectedError(ScribeWorkflowError):
    pass


class ScribeWorkflowConflictError(ScribeWorkflowError):
    pass


class ConsentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    appointment_id: str = Field(min_length=1, max_length=128)
    practitioner_id: str = Field(min_length=1, max_length=128)
    patient_id: str = Field(min_length=1, max_length=128)
    modality: Literal["in_person", "virtual", "phone"]
    participant_regions: set[str] = Field(min_length=1)
    method: Literal["pre_visit_form", "verbal_attestation", "phone_ivr"]
    all_parties_consented: bool
    granted_at: datetime
    evidence_object_id: UUID | None = None


class ConsentRecord(ConsentCreate):
    id: UUID
    policy_version: str
    revoked_at: datetime | None = None


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    appointment_id: str = Field(min_length=1, max_length=128)
    practitioner_id: str = Field(min_length=1, max_length=128)
    patient_id: str = Field(min_length=1, max_length=128)
    consent_record_id: UUID
    modality: Literal["in_person", "virtual", "phone"]


class ExternalTranscriptSessionCreate(SessionCreate):
    """Create a session whose ingest mode is selected by the Scribe receiver."""

    model_config = ConfigDict(extra="forbid")


CaptureSessionStatus = Literal[
    "created",
    "capturing",
    "uploaded",
    "processing",
    "transcript_ready",
    "handed_off",
    "failed",
]


class CaptureSession(SessionCreate):
    id: UUID
    ingest_mode: Literal["chunk_upload"] = "chunk_upload"
    status: CaptureSessionStatus
    created_at: datetime


class ExternalTranscriptCaptureSession(ExternalTranscriptSessionCreate):
    id: UUID
    ingest_mode: Literal["external_async_transcript"]
    status: CaptureSessionStatus
    created_at: datetime


class CaptureTranscriptionStatus(BaseModel):
    """The chunk-capture status contract; readiness is backed by stored output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: UUID
    status: Literal["not_requested", "queued", "processing", "completed", "terminal"]
    transcript_available: StrictBool
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def consistent_availability(self) -> CaptureTranscriptionStatus:
        if self.transcript_available != (self.status == "completed"):
            raise ValueError("inconsistent_capture_status")
        return self


class CaptureTranscriptSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    content: str = Field(min_length=1, max_length=100_000, repr=False)
    start_ms: int = Field(strict=True, ge=0, lt=86_400_000)
    end_ms: int = Field(strict=True, gt=0, le=86_400_000)
    speaker_id: str = Field(min_length=1, max_length=80)
    speaker_role: Literal["clinician", "patient", "other", "unknown"]
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def ordered_times(self) -> CaptureTranscriptSegment:
        if self.end_ms <= self.start_ms:
            raise ValueError("invalid_segment_times")
        return self


class CaptureTranscript(BaseModel):
    """Source transcript, never a versioned working transcript or reviewed note."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    id: UUID
    session_id: UUID
    created_at: AwareDatetime
    language_code: str = Field(min_length=2, max_length=16)
    stt_provider: str = Field(min_length=1, max_length=40)
    segments: tuple[CaptureTranscriptSegment, ...] = Field(
        min_length=1, max_length=10_000, repr=False
    )

    @model_validator(mode="after")
    def ordered_segments(self) -> CaptureTranscript:
        starts = [segment.start_ms for segment in self.segments]
        if starts != sorted(starts):
            raise ValueError("invalid_segment_order")
        return self


class CaptureSource(CaptureSourceEnvelope[CaptureTranscript]):
    """Concrete first-party projection for a qualified recorded source."""


class AsyncTokenCredential(Protocol):
    def get_token(self, *scopes: str, **kwargs: Any) -> Awaitable[AccessToken]: ...

    def close(self) -> Awaitable[None]: ...


class ScribeWorkflowClient(Protocol):
    async def create_configured_session(
        self,
        tenant_id: UUID,
        payload: ConfiguredSessionCreate,
        *,
        creator_actor_id: str,
        idempotency_key: str,
    ) -> ConfiguredSessionReceipt: ...

    async def capture_operation(
        self,
        tenant_id: UUID,
        session_id: UUID,
        operation: capture_protocol.CaptureOperation,
        *,
        idempotency_key: str,
        practitioner_id: str | None = None,
        creator_actor_id: str | None = None,
    ) -> BaseModel: ...

    async def create_consent(
        self,
        tenant_id: UUID,
        payload: ConsentCreate,
        *,
        idempotency_key: str,
    ) -> ConsentRecord: ...

    async def create_session(
        self,
        tenant_id: UUID,
        payload: SessionCreate,
        *,
        idempotency_key: str,
    ) -> CaptureSession: ...

    async def create_external_transcript_session(
        self,
        tenant_id: UUID,
        payload: ExternalTranscriptSessionCreate,
        *,
        idempotency_key: str,
    ) -> ExternalTranscriptCaptureSession: ...

    async def get_capture_status(
        self, tenant_id: UUID, session_id: UUID
    ) -> CaptureTranscriptionStatus: ...

    async def get_capture_transcript(
        self, tenant_id: UUID, session_id: UUID
    ) -> CaptureTranscript: ...

    async def close(self) -> None: ...


class DisabledScribeWorkflowClient:
    async def create_configured_session(
        self,
        tenant_id: UUID,
        payload: ConfiguredSessionCreate,
        *,
        creator_actor_id: str,
        idempotency_key: str,
    ) -> ConfiguredSessionReceipt:
        raise ScribeWorkflowDisabledError("scribe_workflow_disabled")

    async def capture_operation(
        self,
        tenant_id: UUID,
        session_id: UUID,
        operation: capture_protocol.CaptureOperation,
        *,
        idempotency_key: str,
        practitioner_id: str | None = None,
        creator_actor_id: str | None = None,
    ) -> BaseModel:
        raise ScribeWorkflowDisabledError("scribe_workflow_disabled")

    async def create_consent(
        self,
        tenant_id: UUID,
        payload: ConsentCreate,
        *,
        idempotency_key: str,
    ) -> ConsentRecord:
        del tenant_id, payload, idempotency_key
        raise ScribeWorkflowDisabledError("scribe_workflow_disabled")

    async def create_session(
        self,
        tenant_id: UUID,
        payload: SessionCreate,
        *,
        idempotency_key: str,
    ) -> CaptureSession:
        del tenant_id, payload, idempotency_key
        raise ScribeWorkflowDisabledError("scribe_workflow_disabled")

    async def create_external_transcript_session(
        self,
        tenant_id: UUID,
        payload: ExternalTranscriptSessionCreate,
        *,
        idempotency_key: str,
    ) -> ExternalTranscriptCaptureSession:
        del tenant_id, payload, idempotency_key
        raise ScribeWorkflowDisabledError("scribe_workflow_disabled")

    async def get_capture_status(
        self, tenant_id: UUID, session_id: UUID
    ) -> CaptureTranscriptionStatus:
        raise ScribeWorkflowDisabledError("scribe_workflow_disabled")

    async def get_capture_transcript(
        self, tenant_id: UUID, session_id: UUID
    ) -> CaptureTranscript:
        raise ScribeWorkflowDisabledError("scribe_workflow_disabled")

    async def close(self) -> None:
        return None


ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class HttpScribeWorkflowClient:
    """Call Scribe with a short-lived app token from the platform managed identity."""

    def __init__(
        self,
        *,
        base_url: str,
        scope: str,
        timeout_seconds: float,
        credential: AsyncTokenCredential,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.port not in (None, 443)
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
            or any(character.isspace() for character in base_url)
        ):
            raise ValueError("Scribe requires a configured HTTPS origin")
        if client is not None and str(client.base_url).rstrip("/") != str(
            httpx.URL(base_url)
        ).rstrip("/"):
            raise ValueError("Scribe client origin does not match configuration")
        self._scope = scope
        self._credential = credential
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )

    async def create_configured_session(
        self,
        tenant_id: UUID,
        payload: ConfiguredSessionCreate,
        *,
        creator_actor_id: str,
        idempotency_key: str,
    ) -> ConfiguredSessionReceipt:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", creator_actor_id):
            raise ScribeWorkflowRejectedError("scribe_invalid_creator")
        if not re.fullmatch(r"[!-~]{8,255}", idempotency_key):
            raise ScribeWorkflowRejectedError("scribe_invalid_admission_key")
        receipt = await self._post(
            "/v1/captured-sessions",
            tenant_id,
            payload,
            idempotency_key,
            ConfiguredSessionReceipt,
            creator_actor_id=creator_actor_id,
        )
        if receipt.creator_actor_id != creator_actor_id or any(
            getattr(receipt, field) != getattr(payload, field)
            for field in ConfiguredSessionCreate.model_fields
        ):
            raise ScribeWorkflowRejectedError("scribe_response_scope_mismatch")
        return receipt

    async def create_consent(
        self,
        tenant_id: UUID,
        payload: ConsentCreate,
        *,
        idempotency_key: str,
    ) -> ConsentRecord:
        return await self._post(
            "/v1/consents",
            tenant_id,
            payload,
            idempotency_key,
            ConsentRecord,
        )

    async def create_session(
        self,
        tenant_id: UUID,
        payload: SessionCreate,
        *,
        idempotency_key: str,
    ) -> CaptureSession:
        return await self._post(
            "/v1/sessions",
            tenant_id,
            payload,
            idempotency_key,
            CaptureSession,
        )

    async def create_external_transcript_session(
        self,
        tenant_id: UUID,
        payload: ExternalTranscriptSessionCreate,
        *,
        idempotency_key: str,
    ) -> ExternalTranscriptCaptureSession:
        return await self._post(
            "/v1/external-transcript-sessions",
            tenant_id,
            payload,
            idempotency_key,
            ExternalTranscriptCaptureSession,
        )

    async def _post(
        self,
        path: str,
        tenant_id: UUID,
        payload: BaseModel,
        idempotency_key: str,
        response_model: type[ResponseModel],
        *,
        max_bytes: int = 16_384,
        practitioner_id: str | None = None,
        profile_receipt_id: UUID | None = None,
        creator_actor_id: str | None = None,
    ) -> ResponseModel:
        if not isinstance(tenant_id, UUID):
            raise ScribeWorkflowRejectedError("scribe_invalid_scope")
        try:
            check_async_capture_deadline()
            access_token = await self._credential.get_token(self._scope)
            check_async_capture_deadline()
            async with self._client.stream(
                "POST",
                path,
                headers={
                    "Authorization": f"Bearer {access_token.token}",
                    "X-Tenant-ID": str(tenant_id),
                    "Idempotency-Key": idempotency_key,
                    **(
                        {"X-Scribe-Creator-Actor-ID": creator_actor_id}
                        if creator_actor_id
                        else {}
                    ),
                    **(
                        {"X-Capture-Practitioner": practitioner_id}
                        if practitioner_id
                        else {}
                    ),
                    **(
                        {"X-Capture-Profile-Receipt": str(profile_receipt_id)}
                        if profile_receipt_id
                        else {}
                    ),
                    "Accept": "application/json",
                    "Cache-Control": "no-store",
                },
                json=payload.model_dump(mode="json"),
                follow_redirects=False,
            ) as response:
                if response.status_code == 409:
                    raise ScribeWorkflowConflictError("scribe_conflict")
                if (
                    response.status_code in {408, 425, 429}
                    or response.status_code >= 500
                ):
                    raise ScribeWorkflowRetryableError(
                        f"scribe_http_{response.status_code}"
                    )
                if response.status_code not in {200, 201}:
                    raise ScribeWorkflowRejectedError(
                        f"scribe_http_{response.status_code}"
                    )
                if (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                    != "application/json"
                ):
                    raise ScribeWorkflowRetryableError("scribe_invalid_response")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    check_async_capture_deadline()
                    if len(body) + len(chunk) > max_bytes:
                        raise ScribeWorkflowRetryableError("scribe_response_too_large")
                    body.extend(chunk)
        except httpx.TimeoutException:
            raise ScribeWorkflowRetryableError("scribe_request_timeout") from None
        except AzureError:
            raise ScribeWorkflowRetryableError("scribe_identity_unavailable") from None
        except httpx.HTTPError:
            raise ScribeWorkflowRetryableError("scribe_transport_unavailable") from None
        try:
            check_async_capture_deadline()
            return response_model.model_validate_json(body)
        except (ValueError, ValidationError):
            raise ScribeWorkflowRetryableError("scribe_invalid_response") from None

    async def get_capture_status(
        self, tenant_id: UUID, session_id: UUID
    ) -> CaptureTranscriptionStatus:
        return await self._get_capture(
            tenant_id,
            session_id,
            "transcription-status",
            CaptureTranscriptionStatus,
            max_bytes=65_536,
        )

    async def get_capture_transcript(
        self, tenant_id: UUID, session_id: UUID
    ) -> CaptureTranscript:
        # The status receiver rejects external/versioned sessions. Do not
        # bypass that boundary through the legacy transcript endpoint.
        status = await self.get_capture_status(tenant_id, session_id)
        if not status.transcript_available:
            raise ScribeWorkflowConflictError("scribe_transcript_not_ready")
        return await self._get_capture(
            tenant_id,
            session_id,
            "transcript",
            CaptureTranscript,
            max_bytes=MAX_CAPTURE_SOURCE_BYTES,
        )

    async def _get_capture(
        self,
        tenant_id: UUID,
        session_id: UUID,
        resource: Literal["transcription-status", "transcript"],
        response_model: type[ResponseModel],
        *,
        max_bytes: int,
    ) -> ResponseModel:
        """Transport only: a caller must first authorize its durable Core binding.

        Never mount this client directly as a browser proxy. The UUIDs are
        routing references, not human/encounter authorization.
        """
        if not isinstance(tenant_id, UUID) or not isinstance(session_id, UUID):
            raise ScribeWorkflowRejectedError("scribe_invalid_scope")
        result = await self._get_resource(
            tenant_id,
            f"/v1/sessions/{session_id}/{resource}",
            response_model,
            max_bytes=max_bytes,
        )
        if getattr(result, "session_id", None) != session_id:
            raise ScribeWorkflowRejectedError("scribe_response_scope_mismatch")
        return result

    async def _get_resource(
        self,
        tenant_id: UUID,
        path: str,
        response_model: type[ResponseModel],
        *,
        max_bytes: int,
        practitioner_id: str | None = None,
        profile_receipt_id: UUID | None = None,
        creator_actor_id: str | None = None,
    ) -> ResponseModel:
        try:
            check_async_capture_deadline()
            access_token = await self._credential.get_token(self._scope)
            check_async_capture_deadline()
            async with self._client.stream(
                "GET",
                path,
                headers={
                    "Authorization": f"Bearer {access_token.token}",
                    "X-Tenant-ID": str(tenant_id),
                    "Accept": "application/json",
                    "Cache-Control": "no-store",
                    **(
                        {"X-Scribe-Creator-Actor-ID": creator_actor_id}
                        if creator_actor_id
                        else {}
                    ),
                    **(
                        {"X-Capture-Practitioner": practitioner_id}
                        if practitioner_id
                        else {}
                    ),
                    **(
                        {"X-Capture-Profile-Receipt": str(profile_receipt_id)}
                        if profile_receipt_id
                        else {}
                    ),
                },
                follow_redirects=False,
            ) as response:
                if response.status_code == 409:
                    raise ScribeWorkflowConflictError("scribe_conflict")
                if (
                    response.status_code in {408, 425, 429}
                    or response.status_code >= 500
                ):
                    raise ScribeWorkflowRetryableError(
                        f"scribe_http_{response.status_code}"
                    )
                if response.status_code != 200:
                    raise ScribeWorkflowRejectedError(
                        f"scribe_http_{response.status_code}"
                    )
                if (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                    != "application/json"
                ):
                    raise ScribeWorkflowRetryableError("scribe_invalid_response")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    check_async_capture_deadline()
                    if len(body) + len(chunk) > max_bytes:
                        raise ScribeWorkflowRetryableError("scribe_response_too_large")
                    body.extend(chunk)
        except httpx.TimeoutException:
            raise ScribeWorkflowRetryableError("scribe_request_timeout") from None
        except AzureError:
            raise ScribeWorkflowRetryableError("scribe_identity_unavailable") from None
        except httpx.HTTPError:
            # Remote payloads and credentials must not appear in error text.
            raise ScribeWorkflowRetryableError("scribe_transport_unavailable") from None
        try:
            check_async_capture_deadline()
            result = response_model.model_validate_json(body)
        except (ValueError, ValidationError):
            raise ScribeWorkflowRetryableError("scribe_invalid_response") from None
        return result

    async def capture_operation(
        self,
        tenant_id: UUID,
        session_id: UUID,
        operation: capture_protocol.CaptureOperation,
        *,
        idempotency_key: str,
        practitioner_id: str | None = None,
        creator_actor_id: str | None = None,
    ) -> BaseModel:
        """Closed routing only; the gateway rechecks admitted scope and receipts."""
        if not isinstance(tenant_id, UUID) or not isinstance(session_id, UUID):
            raise ScribeWorkflowRejectedError("scribe_invalid_scope")
        try:
            operation = capture_protocol.OPERATION_ADAPTER.validate_python(
                operation.model_dump()
            )
        except (ValueError, TypeError, AttributeError):
            raise ScribeWorkflowRejectedError("scribe_invalid_operation") from None
        prefix = f"/v1/sessions/{session_id}"
        response_type: type[BaseModel]
        if operation.kind == "source_start":
            from app.services.scribe_source_start_contract import (
                NativeStartDecision,
                NativeStartReceipt,
                NativeStopResult,
            )

            if creator_actor_id is None or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", creator_actor_id
            ):
                raise ScribeWorkflowRejectedError("scribe_invalid_creator")
            if not re.fullmatch(r"[!-~]{8,255}", idempotency_key):
                raise ScribeWorkflowRejectedError("scribe_invalid_start_key")
            return await self._post(
                f"/v1/captured-sessions/{session_id}/source-start/{operation.action}",
                tenant_id,
                operation.payload,
                idempotency_key,
                NativeStartDecision
                if operation.action == "authorize"
                else NativeStopResult
                if operation.action == "stop"
                else NativeStartReceipt,
                max_bytes=8192,
                creator_actor_id=creator_actor_id,
            )
        if operation.kind in {"creator_review", "decide_translation"}:
            if creator_actor_id is None or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", creator_actor_id
            ):
                raise ScribeWorkflowRejectedError("scribe_invalid_creator")
            if not re.fullmatch(r"[!-~]{8,255}", idempotency_key):
                raise ScribeWorkflowRejectedError("scribe_invalid_review_key")
            path = f"/v1/captured-sessions/{session_id}/review"
            # This receiver atomically checks the configured session's immutable
            # creator and current consent. Legacy transcript status cannot do so.
            if isinstance(operation, capture_protocol.DecideTranslation):
                result = await self._post(
                    path,
                    tenant_id,
                    operation.payload,
                    idempotency_key,
                    CreatorDecisionReceipt,
                    creator_actor_id=creator_actor_id,
                )
            else:
                result = await self._get_resource(
                    tenant_id,
                    path,
                    CreatorReviewView,
                    max_bytes=MAX_CREATOR_REVIEW_BYTES,
                    creator_actor_id=creator_actor_id,
                )
            if result.session_id != session_id:
                raise ScribeWorkflowRejectedError("scribe_response_scope_mismatch")
            return result
        if operation.kind in {
            "working_transcript",
            "correct_transcript",
            "recover_capture",
            "capture_recovery_state",
            "capture_source",
            "correct_capture_source",
        }:
            if practitioner_id is None or not re.fullmatch(
                r"doctor_[0-7][0-9a-hjkmnp-tv-z]{25}", practitioner_id
            ):
                raise ScribeWorkflowRejectedError("scribe_invalid_practitioner")
            if isinstance(operation, capture_protocol.CorrectCaptureSource):
                return await self._post(
                    prefix + "/capture-source/corrections",
                    tenant_id,
                    operation.payload,
                    idempotency_key,
                    CaptureSource,
                    max_bytes=MAX_CAPTURE_SOURCE_BYTES,
                    practitioner_id=practitioner_id,
                )
            if operation.kind == "capture_source":
                return await self._get_resource(
                    tenant_id,
                    prefix + "/capture-source",
                    CaptureSource,
                    max_bytes=MAX_CAPTURE_SOURCE_BYTES,
                    practitioner_id=practitioner_id,
                )
            if isinstance(operation, capture_protocol.RecoverCapture):
                return await self._post(
                    prefix + "/capture-recoveries",
                    tenant_id,
                    operation.payload,
                    idempotency_key,
                    CaptureRecoveryReceipt,
                    max_bytes=16_384,
                    practitioner_id=practitioner_id,
                )
            if operation.kind == "capture_recovery_state":
                return await self._get_resource(
                    tenant_id,
                    prefix + "/capture-recovery-state",
                    CaptureRecoveryState,
                    max_bytes=MAX_RECOVERY_STATE_BYTES,
                    practitioner_id=practitioner_id,
                )
            if isinstance(operation, capture_protocol.CorrectTranscript):
                return await self._post(
                    prefix + "/working-transcript",
                    tenant_id,
                    operation.payload,
                    idempotency_key,
                    CaptureWorkingTranscript,
                    max_bytes=MAX_WORKING_BYTES,
                    practitioner_id=practitioner_id,
                )
            return await self._get_resource(
                tenant_id,
                prefix + "/working-transcript",
                CaptureWorkingTranscript,
                max_bytes=MAX_WORKING_BYTES,
                practitioner_id=practitioner_id,
            )
        if isinstance(operation, capture_protocol.ReadOperation):
            if operation.kind == "transcription_status":
                return await self.get_capture_status(tenant_id, session_id)
            if operation.kind == "transcript":
                return await self.get_capture_transcript(tenant_id, session_id)
            if operation.kind == "encryption_key":
                path, response_type = (
                    "/v1/audio-encryption-key",
                    capture_protocol.EncryptionKey,
                )
            elif operation.kind == "capture_profile":
                path, response_type = (
                    prefix + "/capture-profile",
                    capture_protocol.CaptureProfileReceipt,
                )
            else:
                path, response_type = (
                    prefix + "/capture-capabilities",
                    capture_protocol.LongVisitCapabilities
                    if operation.profile_receipt_id is not None
                    else capture_protocol.Capabilities,
                )
            return await self._get_resource(
                tenant_id,
                path,
                response_type,
                max_bytes=16_384,
                profile_receipt_id=operation.profile_receipt_id,
            )
        if isinstance(operation, capture_protocol.ReadChunk):
            return await self._get_resource(
                tenant_id,
                prefix + f"/chunks/{operation.object_id}",
                capture_protocol.ChunkReceipt,
                max_bytes=16_384,
            )
        if isinstance(operation, capture_protocol.SelectCaptureProfile):
            path, response_type = (
                prefix + "/capture-profile",
                capture_protocol.CaptureProfileReceipt,
            )
        elif isinstance(operation, capture_protocol.BindKey):
            path, response_type = prefix + "/audio-key", capture_protocol.KeyReceipt
        elif isinstance(operation, capture_protocol.IssueLease):
            path, response_type = (
                prefix + "/capture-lease",
                capture_protocol.LeaseReceipt,
            )
        elif isinstance(operation, capture_protocol.RenewLease):
            path = prefix + f"/capture-leases/{operation.lease_id}/renew"
            response_type = capture_protocol.LeaseReceipt
        elif isinstance(operation, capture_protocol.ReserveChunks):
            path, response_type = (
                prefix + "/chunk-reservations",
                capture_protocol.ReservationReceipt,
            )
        elif isinstance(operation, capture_protocol.AuthorizeUpload):
            path = prefix + f"/chunks/{operation.object_id}/upload-authorization"
            response_type = capture_protocol.UploadReceipt
        elif isinstance(operation, capture_protocol.RegisterChunk):
            path, response_type = prefix + "/chunks", capture_protocol.RegisteredChunk
        else:
            path, response_type = prefix + "/capture-complete", CaptureSession
        return await self._post(
            path,
            tenant_id,
            operation.payload,
            idempotency_key,
            response_type,
            max_bytes=131_072
            if isinstance(operation, capture_protocol.ReserveChunks)
            else 16_384,
            profile_receipt_id=(
                operation.profile_receipt_id
                if isinstance(operation, capture_protocol.ReserveChunks)
                else None
            ),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
        await self._credential.close()


def build_scribe_workflow_client(
    settings: ScribeWorkflowSettings = SCRIBE_WORKFLOW_SETTINGS,
    credential_factory: Callable[[str], AsyncTokenCredential] | None = None,
) -> ScribeWorkflowClient:
    if not settings.enabled:
        return DisabledScribeWorkflowClient()
    if not settings.is_complete():
        raise ValueError("Scribe workflow settings are incomplete")

    if credential_factory is None:
        credential: AsyncTokenCredential = DefaultAzureCredential(
            managed_identity_client_id=settings.managed_identity_client_id
        )
    else:
        credential = credential_factory(settings.managed_identity_client_id)

    return HttpScribeWorkflowClient(
        base_url=settings.base_url,
        scope=settings.scope,
        timeout_seconds=settings.timeout_seconds,
        credential=credential,
    )
