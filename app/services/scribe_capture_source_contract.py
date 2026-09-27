"""Closed qualified source projection, including immutable interruption evidence."""

from typing import TYPE_CHECKING, Generic, Literal, TypeVar, cast
from uuid import UUID

from pydantic import BaseModel, Field, StrictBool, model_validator

from app.services.scribe_capture_recovery_contract import CaptureRecoveryHistory
from app.services.scribe_capture_working_contract import (
    CaptureWorkingTranscript,
    Digest,
    ProtectedContract,
    canonical_hash,
)

if TYPE_CHECKING:
    from app.services.scribe_workflow import CaptureTranscript

MAX_CAPTURE_SOURCE_BYTES = 24 * 1024 * 1024
RawTranscript = TypeVar("RawTranscript", bound=BaseModel)


class CaptureSourceEnvelope(ProtectedContract, Generic[RawTranscript]):
    protocol_version: Literal["1.0"] = "1.0"
    session_id: UUID
    raw_transcript: RawTranscript = Field(repr=False)
    working_transcript: CaptureWorkingTranscript = Field(repr=False)
    recovery_history: CaptureRecoveryHistory = Field(repr=False)
    recovery_sha256: Digest
    source_sha256: Digest
    capture_incomplete: StrictBool

    @model_validator(mode="after")
    def bound_source(self):
        raw = cast("CaptureTranscript", self.raw_transcript)
        working = self.working_transcript
        if (
            raw.session_id != self.session_id
            or working.session_id != self.session_id
            or self.recovery_history.session_id != self.session_id
            or self.recovery_sha256 != canonical_hash(self.recovery_history)
            or self.source_sha256
            != canonical_hash(
                {
                    "raw_transcript_sha256": working.raw_transcript_sha256,
                    "working_sha256": working.working_sha256,
                    "recovery_sha256": self.recovery_sha256,
                }
            )
            or self.capture_incomplete != bool(self.recovery_history.recoveries)
        ):
            raise ValueError("capture_source_projection_invalid")
        # This proves recorded origin. The gateway separately supplies the
        # currently admitted practitioner, rather than trusting this assertion.
        working.validate_raw(raw, working.corrected_by_practitioner_id or "unmodified")
        return self

    def validate_practitioner(self, practitioner_id: str) -> None:
        self.working_transcript.validate_raw(
            cast("CaptureTranscript", self.raw_transcript), practitioner_id
        )
        if any(
            row.acknowledged_by_practitioner_id != practitioner_id
            for row in self.recovery_history.recoveries
        ):
            raise ValueError("capture_source_practitioner_invalid")
