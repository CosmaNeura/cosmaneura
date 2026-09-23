"""Closed consumer of Scribe native restart evidence; no inferred missing audio."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.services.scribe_capture_working_contract import ProtectedContract


class NativeIntervalStart(ProtectedContract):
    source_start_id: UUID
    first_sequence_number: int = Field(strict=True, ge=0, le=999)


class CaptureContinuity(ProtectedContract):
    schema_version: Literal["1.0"]
    continuity: Literal["interrupted_or_unconfirmed"]
    missing_audio: Literal["unknown"]
    starts: tuple[NativeIntervalStart, ...] = Field(min_length=2, max_length=1000)

    @model_validator(mode="after")
    def ordered_starts(self):
        positions = [item.first_sequence_number for item in self.starts]
        if (
            positions[0] != 0
            or positions != sorted(positions)
            or len({item.source_start_id for item in self.starts}) != len(self.starts)
        ):
            raise ValueError("capture_continuity_invalid")
        return self


def continuity_grounding(evidence: CaptureContinuity | None) -> str:
    if evidence is None:
        return ""
    return (
        "\nCAPTURE LIMITATION: This source includes multiple native start attempts. "
        "Continuous capture is unconfirmed. Do not infer missing speech, gap "
        "duration, findings, or completed actions. Transcript offsets describe "
        "retained audio, not total encounter duration. Use only the supplied "
        "reviewed source. The clinician must review possible recording gaps.\n"
    )
