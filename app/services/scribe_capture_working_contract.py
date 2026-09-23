"""Closed consumer of Scribe's recorded working-transcript contract.

This validates provenance and the current English/unresolved receiver surface;
it does not translate, approve translations, or implement Scribe persistence.
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Literal
from uuid import UUID, uuid5

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

if TYPE_CHECKING:
    from app.services.scribe_workflow import CaptureTranscript

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Version = Annotated[int, Field(strict=True, ge=1)]
Content = Annotated[str, Field(min_length=1, max_length=100_000, repr=False)]
Role = Literal["clinician", "patient", "other", "unknown"]
Locale = Literal["en-US", "es-US", "unknown"]
MAX_WORKING_BYTES = 16_777_216


class ProtectedContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<protected>)"

    def __str__(self) -> str:
        return self.__repr__()


def canonical_document(value: object) -> str:
    def normalize(item):
        if isinstance(item, BaseModel):
            return normalize(item.model_dump(mode="python"))
        if isinstance(item, datetime):
            return (
                item.astimezone(UTC)
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z")
            )
        if isinstance(item, UUID):
            return str(item)
        if isinstance(item, dict):
            return {key: normalize(val) for key, val in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalize(val) for val in item]
        return item

    return json.dumps(
        normalize(value), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    )


def canonical_hash(value: object) -> str:
    return hashlib.sha256(canonical_document(value).encode()).hexdigest()


class SegmentCorrection(ProtectedContract):
    source_segment_id: UUID
    content: Content
    speaker_role: Role

    @field_validator("content")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip() or "\0" in value:
            raise ValueError("invalid_correction_content")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("invalid_correction_content") from None
        return value


class TranscriptCorrection(ProtectedContract):
    raw_transcript_id: UUID
    raw_transcript_sha256: Digest
    expected_working_version: Version
    expected_working_sha256: Digest
    corrections: tuple[SegmentCorrection, ...] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def bounded_unique(self):
        if (
            len({edit.source_segment_id for edit in self.corrections})
            != len(self.corrections)
            or sum(len(edit.content) for edit in self.corrections) > 100_000
        ):
            raise ValueError("invalid_correction_set")
        return self


class SegmentOrigin(ProtectedContract):
    source_segment_id: UUID
    source_segment_version: Version
    source_locale: Locale
    speaker_id: UUID
    speaker_role: Role
    start_ms: int = Field(strict=True, ge=0, lt=86_400_000)
    end_ms: int = Field(strict=True, gt=0, le=86_400_000)

    @model_validator(mode="after")
    def ordered_times(self):
        if self.start_ms >= self.end_ms:
            raise ValueError("invalid_working_timing")
        return self


class SourceSegment(SegmentOrigin):
    content: Content
    locale_confidence: None


class WorkingSource(ProtectedContract):
    schema_version: Literal["1.0"]
    source_transcript_version: Version
    language_mode: Literal["en-US", "en-US+es-US"]
    transcription_adapter_id: Literal["captured_raw_transcript_v1"]
    transcription_model_profile_id: Literal["persisted_capture"]
    normalization_profile_id: Literal["capture_corrections_v1"]
    duration_ms: int = Field(strict=True, gt=0, le=86_400_000)
    segments: tuple[SourceSegment, ...] = Field(min_length=1, max_length=10_000)


class WorkingSegment(SegmentOrigin):
    resolution: Literal["english_source", "unresolved"]
    content: Content | None
    translation_id: None
    translation_version: None
    translation_review_id: None


class WorkingBlocker(ProtectedContract):
    source_segment_id: UUID
    code: Literal["source_locale_unknown", "source_overlap", "translation_missing"]


class WorkingTranscript(ProtectedContract):
    schema_version: Literal["1.0"]
    working_transcript_id: UUID
    working_transcript_version: Version
    source_transcript_version: Version
    segments: tuple[WorkingSegment, ...] = Field(min_length=1, max_length=10_000)
    blockers: tuple[WorkingBlocker, ...] = Field(max_length=20_000)
    note_ready: StrictBool


class CaptureWorkingTranscript(ProtectedContract):
    receipt_id: UUID
    session_id: UUID
    raw_transcript_id: UUID
    raw_transcript_sha256: Digest
    previous_working_sha256: Digest | None
    working_sha256: Digest
    working_source: WorkingSource
    working_transcript: WorkingTranscript
    corrected_by_practitioner_id: str | None = Field(min_length=1, max_length=128)
    created_at: AwareDatetime

    @model_validator(mode="after")
    def consistent_projection(self):
        source, working = self.working_source, self.working_transcript
        version = source.source_transcript_version
        if (
            working.working_transcript_id
            != uuid5(self.raw_transcript_id, "capture-working-transcript-v1")
            or working.working_transcript_version != version
            or working.source_transcript_version != version
            or len(source.segments) != len(working.segments)
            or len({s.source_segment_id for s in source.segments})
            != len(source.segments)
            or sum(len(s.content) for s in source.segments) > 100_000
            or max(s.end_ms for s in source.segments) != source.duration_ms
            or tuple(
                sorted(
                    source.segments,
                    key=lambda s: (s.start_ms, s.end_ms, s.source_segment_id.int),
                )
            )
            != source.segments
            or any(
                s.source_segment_version > version
                or not s.content.strip()
                or "\0" in s.content
                for s in source.segments
            )
            or (
                version == 1
                and (
                    self.previous_working_sha256 is not None
                    or self.corrected_by_practitioner_id is not None
                )
            )
            or (
                version > 1
                and (
                    self.previous_working_sha256 is None
                    or self.corrected_by_practitioner_id is None
                )
            )
            or self.working_sha256
            != canonical_hash(
                {
                    "raw_transcript_sha256": self.raw_transcript_sha256,
                    "working_source": source,
                }
            )
        ):
            raise ValueError("invalid_working_projection")
        blockers: set[tuple[UUID, str]] = set()
        end = 0
        for original, projected in zip(source.segments, working.segments, strict=True):
            if any(
                getattr(original, key) != getattr(projected, key)
                for key in SegmentOrigin.model_fields
            ):
                raise ValueError("invalid_working_origin")
            if original.start_ms < end:
                blockers.add((original.source_segment_id, "source_overlap"))
            end = max(end, original.end_ms)
            english = original.source_locale == "en-US"
            if projected.resolution != (
                "english_source" if english else "unresolved"
            ) or (projected.content != (original.content if english else None)):
                raise ValueError("invalid_working_resolution")
            if not english:
                blockers.add(
                    (
                        original.source_segment_id,
                        "translation_missing"
                        if original.source_locale == "es-US"
                        else "source_locale_unknown",
                    )
                )
        actual = {(b.source_segment_id, b.code) for b in working.blockers}
        if (
            actual != blockers
            or len(actual) != len(working.blockers)
            or working.note_ready != (not blockers)
        ):
            raise ValueError("invalid_working_blockers")
        return self

    def validate_raw(self, raw: "CaptureTranscript", practitioner_id: str) -> None:
        locale = {"en": "en-US", "en-us": "en-US", "es": "es-US", "es-us": "es-US"}.get(
            raw.language_code.casefold(), "unknown"
        )
        if (
            self.session_id != raw.session_id
            or self.raw_transcript_id != raw.id
            or self.raw_transcript_sha256 != canonical_hash(raw)
            or self.working_source.language_mode
            != ("en-US+es-US" if locale == "es-US" else "en-US")
            or (
                self.corrected_by_practitioner_id is not None
                and self.corrected_by_practitioner_id != practitioner_id
            )
        ):
            raise ValueError("invalid_recorded_origin")
        by_id = {uuid5(raw.id, f"segment:{i}"): s for i, s in enumerate(raw.segments)}
        if set(by_id) != {s.source_segment_id for s in self.working_source.segments}:
            raise ValueError("invalid_recorded_segments")
        for segment in self.working_source.segments:
            original = by_id[segment.source_segment_id]
            if (
                segment.start_ms != original.start_ms
                or segment.end_ms != original.end_ms
                or segment.source_locale != locale
                or segment.speaker_id != uuid5(raw.id, f"speaker:{original.speaker_id}")
                or (
                    self.working_source.source_transcript_version == 1
                    and (
                        segment.content != original.content
                        or segment.speaker_role != original.speaker_role
                        or segment.source_segment_version != 1
                    )
                )
            ):
                raise ValueError("invalid_recorded_segment")
