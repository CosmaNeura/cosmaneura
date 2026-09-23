"""Closed projection of Scribe recovery evidence; validated against receiver fixtures."""

from datetime import timedelta
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.services.scribe_capture_working_contract import (
    canonical_hash as canonical_sha256,
)

MAX_RECOVERY_STATE_BYTES = 2_000_000
SessionStatus = Literal[
    "created",
    "capturing",
    "uploaded",
    "processing",
    "transcript_ready",
    "handed_off",
    "failed",
]

SequenceBoundary = Annotated[int, Field(strict=True, ge=0, le=1000)]
GapReason = Literal[
    "audio_context_suspended",
    "audio_device_disconnected",
    "audio_route_changed",
    "audio_session_interrupted",
    "capture_backgrounded",
    "capture_source_stalled",
    "capture_source_failed",
    "capture_authority_expired",
    "process_recovery_unknown",
]


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<protected>)"

    def __str__(self) -> str:
        return self.__repr__()


class CaptureLeaseReceipt(_ClosedModel):
    protocol_version: Literal["1.0"] = "1.0"
    receipt_id: UUID
    lease_id: UUID
    session_id: UUID
    generation: int = Field(strict=True, ge=1)
    issued_at: AwareDatetime
    expires_at: AwareDatetime


class CaptureRecoveryRequest(_ClosedModel):
    protocol_version: Literal["1.0"] = "1.0"
    capture_lease_id: UUID
    capture_lease_generation: int = Field(strict=True, ge=1)
    channel: Literal["microphone"] = "microphone"
    segment_id: UUID
    segment_ordinal: int = Field(strict=True, ge=0, lt=1000)
    first_sequence_number: int = Field(strict=True, ge=0, lt=1000)
    next_sequence_number: SequenceBoundary
    segment_started_at: AwareDatetime
    gap_id: UUID
    reason_code: GapReason
    observed_at: AwareDatetime
    acknowledged_at: AwareDatetime
    decision: Literal["resume_capture", "finish_capture"]

    @model_validator(mode="after")
    def ordered_observation(self) -> Self:
        if (
            self.segment_id == self.gap_id
            or self.next_sequence_number < self.first_sequence_number
            or not self.segment_started_at <= self.observed_at <= self.acknowledged_at
            or (self.decision == "resume_capture" and self.next_sequence_number == 1000)
            or (self.decision == "finish_capture" and self.next_sequence_number == 0)
        ):
            raise ValueError("capture_recovery_observation_invalid")
        return self


class CaptureRecoveryReceipt(_ClosedModel):
    receipt_id: UUID
    session_id: UUID
    request: CaptureRecoveryRequest
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    acknowledged_by_practitioner_id: str = Field(min_length=1, max_length=128)
    following_segment_id: UUID | None
    lease: CaptureLeaseReceipt
    review_state: Literal["required"] = "required"

    @model_validator(mode="after")
    def bound_receipt(self) -> Self:
        if (
            self.request_sha256 != canonical_sha256(self.request)
            or self.lease.session_id != self.session_id
            or self.lease.lease_id != self.request.capture_lease_id
            or self.lease.generation != self.request.capture_lease_generation + 1
            or self.lease.issued_at.tzinfo is None
            or self.lease.expires_at.tzinfo is None
            or not timedelta(0)
            < self.lease.expires_at - self.lease.issued_at
            <= timedelta(seconds=900)
            or (self.following_segment_id is not None)
            != (self.request.decision == "resume_capture")
            or self.following_segment_id
            in {self.request.segment_id, self.request.gap_id}
        ):
            raise ValueError("capture_recovery_receipt_invalid")
        return self


class CaptureRecoveryHistory(_ClosedModel):
    session_id: UUID
    recoveries: tuple[CaptureRecoveryReceipt, ...] = Field(max_length=1000)

    @model_validator(mode="after")
    def consistent_history(self) -> Self:
        previous = None
        receipts: set[UUID] = set()
        segments: set[UUID] = set()
        gaps: set[UUID] = set()
        for receipt in self.recoveries:
            if (
                receipt.session_id != self.session_id
                or receipt.receipt_id in receipts
                or receipt.request.segment_id in segments | gaps
                or receipt.request.gap_id in segments | gaps
                or receipt.following_segment_id in segments | gaps
                or (
                    previous is not None
                    and receipt.acknowledged_by_practitioner_id
                    != previous.acknowledged_by_practitioner_id
                )
            ):
                raise ValueError("capture_recovery_scope_invalid")
            validate_recovery_successor(previous, receipt.request)
            receipts.add(receipt.receipt_id)
            segments.add(receipt.request.segment_id)
            gaps.add(receipt.request.gap_id)
            previous = receipt
        return self


class CaptureRecoveryConflict(ValueError):
    """Fixed reason codes only."""


class CaptureRecoveryState(_ClosedModel):
    """Current persisted facts for reconciliation; never an instruction to record."""

    history: CaptureRecoveryHistory
    current_lease: CaptureLeaseReceipt | None
    lease_closed: bool = Field(strict=True)
    session_status: SessionStatus

    @model_validator(mode="after")
    def current_scope(self) -> Self:
        lease = self.current_lease
        if lease is None:
            if self.history.recoveries or self.lease_closed:
                raise ValueError("capture_recovery_current_lease_missing")
        elif (
            lease.session_id != self.history.session_id
            or lease.issued_at.tzinfo is None
            or lease.expires_at.tzinfo is None
            or not timedelta(0)
            < lease.expires_at - lease.issued_at
            <= timedelta(seconds=900)
            or any(
                row.lease.lease_id != lease.lease_id
                or row.lease.generation > lease.generation
                for row in self.history.recoveries
            )
        ):
            raise ValueError("capture_recovery_current_lease_invalid")
        return self


def validate_recovery_successor(
    previous: CaptureRecoveryReceipt | None,
    request: CaptureRecoveryRequest,
) -> None:
    if previous is None:
        if request.segment_ordinal != 0 or request.first_sequence_number != 0:
            raise CaptureRecoveryConflict("capture_recovery_first_segment_invalid")
    elif (
        previous.request.decision != "resume_capture"
        or request.segment_ordinal != previous.request.segment_ordinal + 1
        or request.segment_id != previous.following_segment_id
        or request.first_sequence_number != previous.request.next_sequence_number
        or request.capture_lease_id != previous.lease.lease_id
        or request.capture_lease_generation < previous.lease.generation
        or request.segment_started_at < previous.request.acknowledged_at
    ):
        raise CaptureRecoveryConflict("capture_recovery_predecessor_changed")
