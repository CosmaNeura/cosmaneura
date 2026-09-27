"""Closed first-party projection of Scribe's retained native capture protocol 1.0.

These are wire contracts, not copied storage or capture implementations. Scribe
owns encryption, leases, chunks and idempotency. No caller may choose a session.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from app.services.scribe_capture_limits import (
    LEGACY_CAPTURE_PLAINTEXT_BYTES,
    MAX_CAPTURE_CHUNKS,
    MAX_CAPTURE_PLAINTEXT_BYTES,
)
from app.services.scribe_capture_recovery_contract import CaptureRecoveryRequest
from app.services.scribe_capture_working_contract import TranscriptCorrection
from app.services.scribe_creator_review_contract import (
    CreatorReviewView,
    CreatorTranslationDecision,
)
from app.services.scribe_source_extension_contract import NativeSourceExtension
from app.services.scribe_source_start_contract import NativeSourceStart

Channel = Literal["microphone", "system_audio", "mixed"]
Generation = Annotated[int, Field(strict=True, ge=1)]
Sequence = Annotated[int, Field(strict=True, ge=0, lt=MAX_CAPTURE_CHUNKS)]
KeyVersion = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
WrappedKey = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{512}$", repr=False)]
AssociatedData = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,1024}$", repr=False)]


class ProtectedContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<protected>)"

    def __str__(self) -> str:
        return self.__repr__()


class ProtocolPayload(ProtectedContract):
    protocol_version: Literal["1.0"] = "1.0"


class KeyPayload(ProtectedContract):
    key_version: KeyVersion
    wrapped_dek: WrappedKey


class RenewPayload(ProtocolPayload):
    expected_generation: Generation


class ReservePayload(ProtocolPayload):
    capture_lease_id: UUID
    capture_lease_generation: Generation
    channel: Channel
    first_sequence_number: Sequence
    count: int = Field(strict=True, ge=1, le=90)

    @model_validator(mode="after")
    def within_limit(self) -> ReservePayload:
        if self.first_sequence_number + self.count > MAX_CAPTURE_CHUNKS:
            raise ValueError("Capture limit exceeded")
        return self


class RegisterPayload(ProtectedContract):
    sequence_number: Sequence
    channel: Channel
    object_id: UUID
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$", repr=False)
    ciphertext_size_bytes: int = Field(strict=True, gt=0, le=327680)


class ChannelCompletion(ProtectedContract):
    channel: Channel
    final_sequence_number: Sequence


class CompletePayload(ProtocolPayload):
    capture_lease_id: UUID
    capture_lease_generation: Generation
    channels: tuple[ChannelCompletion, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def unique_channels(self) -> CompletePayload:
        if len({item.channel for item in self.channels}) != len(self.channels):
            raise ValueError("Duplicate capture channel")
        return self


class CaptureProfileRequest(ProtectedContract):
    schema_version: Literal["1.0"] = "1.0"
    profile_id: Literal["visit-60m-v1"]
    channels: tuple[Channel, ...] = Field(min_length=1, max_length=2)

    @field_validator("channels")
    @classmethod
    def selected_channels(cls, value: tuple[Channel, ...]) -> tuple[Channel, ...]:
        if len(set(value)) != len(value) or ("mixed" in value and len(value) != 1):
            raise ValueError("Invalid capture track selection")
        return tuple(sorted(value))


def profile_request_sha256(request: CaptureProfileRequest) -> str:
    payload = json.dumps(
        request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class CaptureProfileReceipt(ProtectedContract):
    schema_version: Literal["1.0"] = "1.0"
    receipt_id: UUID
    session_id: UUID
    request: CaptureProfileRequest
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)
    created_at: AwareDatetime
    maximum_aggregate_plaintext_bytes: Literal[268435456]
    maximum_chunks: Literal[720]
    maximum_chunks_per_channel: Literal[360]
    maximum_track_duration_ms: Literal[3600000]

    @model_validator(mode="after")
    def exact_request(self) -> CaptureProfileReceipt:
        if self.request_sha256 != profile_request_sha256(self.request):
            raise ValueError("Invalid capture profile receipt")
        return self


class SelectCaptureProfile(ProtectedContract):
    kind: Literal["select_capture_profile"]
    payload: CaptureProfileRequest


class ReadOperation(ProtectedContract):
    kind: Literal[
        "encryption_key",
        "capabilities",
        "transcription_status",
        "transcript",
        "working_transcript",
        "capture_recovery_state",
        "capture_source",
        "capture_profile",
        "capture_lease_state",
        "creator_review",
        "native_recording_context",
        "provider_encounter",
    ]
    profile_receipt_id: UUID | None = None

    @model_validator(mode="after")
    def scoped_profile_acknowledgment(self) -> ReadOperation:
        if self.profile_receipt_id is not None and self.kind != "capabilities":
            raise ValueError("Unexpected capture profile acknowledgment")
        return self


class BindKey(ProtectedContract):
    kind: Literal["bind_key"]
    payload: KeyPayload


class IssueLease(ProtectedContract):
    kind: Literal["issue_lease"]
    payload: ProtocolPayload


class RenewLease(ProtectedContract):
    kind: Literal["renew_lease"]
    lease_id: UUID
    payload: RenewPayload


class ReserveChunks(ProtectedContract):
    kind: Literal["reserve_chunks"]
    payload: ReservePayload
    profile_receipt_id: UUID | None = None


class AuthorizeUpload(ProtectedContract):
    kind: Literal["authorize_upload"]
    object_id: UUID
    payload: ProtocolPayload


class ReadChunk(ProtectedContract):
    kind: Literal["chunk_receipt"]
    object_id: UUID


class RegisterChunk(ProtectedContract):
    kind: Literal["register_chunk"]
    payload: RegisterPayload


class CompleteCapture(ProtectedContract):
    kind: Literal["complete"]
    payload: CompletePayload


class CorrectTranscript(ProtectedContract):
    kind: Literal["correct_transcript"]
    payload: TranscriptCorrection


class CorrectCaptureSource(ProtectedContract):
    kind: Literal["correct_capture_source"]
    payload: TranscriptCorrection


class RecoverCapture(ProtectedContract):
    kind: Literal["recover_capture"]
    payload: CaptureRecoveryRequest


class DecideTranslation(ProtectedContract):
    kind: Literal["decide_translation"]
    payload: CreatorTranslationDecision


CaptureOperation = Annotated[
    ReadOperation
    | BindKey
    | IssueLease
    | RenewLease
    | ReserveChunks
    | AuthorizeUpload
    | ReadChunk
    | RegisterChunk
    | CompleteCapture
    | CorrectTranscript
    | CorrectCaptureSource
    | RecoverCapture
    | DecideTranslation
    | NativeSourceStart
    | NativeSourceExtension
    | SelectCaptureProfile,
    Field(discriminator="kind"),
]
OPERATION_ADAPTER: TypeAdapter[CaptureOperation] = TypeAdapter(CaptureOperation)


class PublicJwk(ProtectedContract):
    kty: Literal["RSA"]
    n: WrappedKey
    e: str = Field(pattern=r"^[A-Za-z0-9_-]{1,16}$")
    alg: Literal["RSA-OAEP-256"]
    ext: Literal[True]
    key_ops: tuple[Literal["wrapKey"], ...] = Field(min_length=1, max_length=1)


class EncryptionKey(ProtectedContract):
    key_version: KeyVersion
    algorithm: Literal["RSA-OAEP-256"]
    public_jwk: PublicJwk


class NativeRecordingContext(ProtectedContract):
    """Core-admitted key namespace; never permission to open a microphone."""

    schema_version: Literal["1.0"] = "1.0"
    workspace_id: UUID
    tenant_id: UUID
    session_id: UUID
    status: Literal["created", "capturing"]


class ProviderEncounter(ProtectedContract):
    """Discover existing configured work without creating another admission."""

    schema_version: Literal["1.0"] = "1.0"
    status: Literal["not_prepared", "available"]
    review: CreatorReviewView | None

    @model_validator(mode="after")
    def consistent_review(self) -> ProviderEncounter:
        if (self.status == "available") != (self.review is not None):
            raise ValueError("Invalid provider encounter")
        return self


class KeyReceipt(ProtectedContract):
    session_id: UUID
    key_version: KeyVersion
    created_at: AwareDatetime


class MediaCapabilities(ProtectedContract):
    media_type: Literal["audio/wav"]
    encoding: Literal["pcm_s16le"]
    channels: Literal[1]
    sample_rate_hz: Literal[16000]


class BaseCapabilities(ProtocolPayload):
    media: MediaCapabilities
    target_chunk_duration_ms: Literal[10000]
    max_envelope_bytes: Literal[327680]
    capture_lease_ttl_seconds: Literal[900]
    reservation_window_seconds: Literal[900]
    max_reservations_per_batch: Literal[90]
    upload_concurrency: Literal[2]


class Capabilities(BaseCapabilities):
    max_chunks: Literal[1000, 2048]
    remaining_aggregate_plaintext_bytes: int = Field(
        strict=True, ge=0, le=MAX_CAPTURE_PLAINTEXT_BYTES
    )

    @model_validator(mode="after")
    def capability_budget(self) -> Capabilities:
        if (
            self.max_chunks == 1000
            and self.remaining_aggregate_plaintext_bytes
            > LEGACY_CAPTURE_PLAINTEXT_BYTES
        ):
            raise ValueError("Invalid legacy capture budget")
        return self


class LongVisitCapabilities(BaseCapabilities):
    max_chunks: Literal[720]
    remaining_aggregate_plaintext_bytes: int = Field(strict=True, ge=0, le=268435456)


class LeaseReceipt(ProtocolPayload):
    receipt_id: UUID
    lease_id: UUID
    session_id: UUID
    generation: Generation
    issued_at: AwareDatetime
    expires_at: AwareDatetime


class LeaseState(ProtectedContract):
    session_id: UUID
    current_lease: LeaseReceipt | None
    lease_closed: bool = Field(strict=True)
    session_status: Literal[
        "created",
        "capturing",
        "uploaded",
        "processing",
        "transcript_ready",
        "handed_off",
        "failed",
    ]

    @model_validator(mode="after")
    def scoped_lease(self):
        lease = self.current_lease
        if (
            lease is None
            and self.lease_closed
            or lease is not None
            and (
                lease.session_id != self.session_id
                or lease.expires_at <= lease.issued_at
            )
        ):
            raise ValueError("Capture lease state invalid")
        return self


class Reservation(ProtocolPayload):
    object_id: UUID
    session_id: UUID
    channel: Channel
    sequence_number: Sequence
    additional_authenticated_data: AssociatedData
    created_at: AwareDatetime


class ReservationReceipt(ProtocolPayload):
    batch_id: UUID
    session_id: UUID
    capture_authorized_until: AwareDatetime
    reservations: tuple[Reservation, ...] = Field(min_length=1, max_length=90)


class UploadReceipt(ProtocolPayload):
    authorization_receipt_id: UUID
    object_id: UUID
    additional_authenticated_data: AssociatedData
    upload_url: str = Field(min_length=1, max_length=8192, repr=False)
    expires_at: AwareDatetime


class Registration(ProtectedContract):
    chunk_id: UUID
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$", repr=False)
    ciphertext_size_bytes: int = Field(strict=True, gt=0, le=327680)
    registered_at: AwareDatetime


class ChunkReceipt(ProtectedContract):
    reservation: Reservation
    state: Literal["reserved", "registered"]
    registration: Registration | None

    @model_validator(mode="after")
    def consistent_state(self) -> ChunkReceipt:
        if (self.state == "registered") != (self.registration is not None):
            raise ValueError("Invalid chunk receipt")
        return self


class RegisteredChunk(RegisterPayload):
    id: UUID
    session_id: UUID
    registered_at: AwareDatetime


class AudioScope(ProtectedContract):
    tenant_id: UUID
    session_id: UUID
    object_id: UUID
    sequence_number: Sequence
    channel: Channel
