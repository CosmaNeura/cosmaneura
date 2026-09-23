"""Closed consumer of Scribe creator-review v1; no processing or note authority.

Wire fields follow the Scribe OpenAPI at fea25741. Cross-field checks bind every
projection to the same current source. Scribe owns immutable review persistence.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    Field,
    SerializerFunctionWrapHandler,
    StrictBool,
    model_serializer,
    model_validator,
)

from app.services.scribe_capture_continuity import CaptureContinuity
from app.services.scribe_capture_working_contract import (
    Content,
    ProtectedContract,
    Role,
    SegmentOrigin,
    Version,
)

MAX_CREATOR_REVIEW_BYTES = 8 * 1024 * 1024
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
BlockerCode = Literal[
    "source_locale_unknown",
    "source_overlap",
    "translation_missing",
    "translation_review_required",
    "translation_retry_required",
    "translation_failed",
]


class CreatorTranslationDecision(ProtectedContract):
    schema_version: Literal["1.0"]
    expected_source_transcript_version: Version
    source_segment_id: UUID
    expected_source_segment_version: Version
    translation_id: UUID
    expected_translation_version: Version
    decision: Literal["accepted", "rejected"]


class CreatorDecisionReceipt(ProtectedContract):
    schema_version: Literal["1.0"]
    session_id: UUID
    review_id: UUID
    saved: StrictBool

    @model_validator(mode="after")
    def saved_decision(self):
        if not self.saved:
            raise ValueError("invalid_creator_decision_receipt")
        return self


class CreatorSourceSegment(SegmentOrigin):
    content: Content
    locale_confidence: float | None = Field(default=None, ge=0, le=1, strict=True)


class CreatorSource(ProtectedContract):
    schema_version: Literal["1.0"]
    source_transcript_version: Version
    language_mode: Literal["en-US", "en-US+es-US"]
    transcription_adapter_id: Identifier
    transcription_model_profile_id: Identifier
    normalization_profile_id: Identifier
    duration_ms: int = Field(strict=True, gt=0, le=86_400_000)
    segments: tuple[CreatorSourceSegment, ...] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def valid_source(self):
        if (
            len({s.source_segment_id for s in self.segments}) != len(self.segments)
            or tuple(
                sorted(
                    self.segments,
                    key=lambda s: (s.start_ms, s.end_ms, s.source_segment_id.int),
                )
            )
            != self.segments
            or sum(len(s.content) for s in self.segments) > 100_000
            or any(
                not s.content.strip() or s.end_ms > self.duration_ms
                for s in self.segments
            )
            or self.language_mode == "en-US"
            and any(s.source_locale == "es-US" for s in self.segments)
        ):
            raise ValueError("invalid_creator_source")
        return self


class CreatorWorkingSegment(SegmentOrigin):
    resolution: Literal["english_source", "spanish_translation", "unresolved"]
    content: Content | None = None
    translation_id: UUID | None = None
    translation_version: Version | None = None
    translation_review_id: UUID | None = None

    @model_validator(mode="after")
    def valid_resolution(self):
        evidence = (
            self.translation_id,
            self.translation_version,
            self.translation_review_id,
        )
        if self.resolution == "spanish_translation":
            valid = (
                self.source_locale == "es-US"
                and self.content is not None
                and all(v is not None for v in evidence)
            )
        elif self.resolution == "english_source":
            valid = (
                self.source_locale == "en-US"
                and self.content is not None
                and all(v is None for v in evidence)
            )
        else:
            valid = self.content is None and all(v is None for v in evidence)
        if not valid or self.content is not None and not self.content.strip():
            raise ValueError("invalid_creator_resolution")
        return self


class CreatorBlocker(ProtectedContract):
    source_segment_id: UUID
    code: BlockerCode


class CreatorWorkingTranscript(ProtectedContract):
    schema_version: Literal["1.0"]
    working_transcript_id: UUID
    working_transcript_version: Version
    source_transcript_version: Version
    segments: tuple[CreatorWorkingSegment, ...] = Field(min_length=1, max_length=10_000)
    blockers: tuple[CreatorBlocker, ...] = Field(max_length=20_000)
    note_ready: StrictBool


class CreatorReviewItem(ProtectedContract):
    source_segment_id: UUID
    source_segment_version: Version
    translation_id: UUID
    translation_version: Version
    source_locale: Literal["es-US"]
    target_locale: Literal["en-US"]
    speaker_role: Role
    start_ms: int = Field(strict=True, ge=0)
    end_ms: int = Field(strict=True, gt=0)
    source_content: Content
    translated_content: Content


class CreatorReviewQueue(ProtectedContract):
    schema_version: Literal["1.0"]
    session_id: UUID
    source_transcript_version: Version
    working_transcript_id: UUID
    working_transcript_version: Version
    status: Literal["not_ready", "review_required", "complete", "quality_blocked"]
    items: tuple[CreatorReviewItem, ...] = Field(default=(), max_length=10_000)


class CreatorTranslationProgress(ProtectedContract):
    source_segment_id: UUID
    source_segment_version: Version
    state: Literal["queued", "unconfirmed", "result_saved"]


class CreatorReviewView(ProtectedContract):
    schema_version: Literal["1.0"]
    session_id: UUID
    status: Literal[
        "created",
        "capturing",
        "uploaded",
        "processing",
        "speech_failed",
        "speech_retryable",
        "handed_off",
        "translation_pending",
        "review_required",
        "transcript_ready",
        "quality_blocked",
    ]
    source: CreatorSource | None = None
    working: CreatorWorkingTranscript | None = None
    review_queue: CreatorReviewQueue | None = None
    translation_progress: tuple[CreatorTranslationProgress, ...] = Field(
        default=(), max_length=10_000
    )
    capture_continuity: CaptureContinuity | None = None

    @model_serializer(mode="wrap")
    def compatible_continuity(self, handler: SerializerFunctionWrapHandler):
        result = handler(self)
        if self.capture_continuity is None:
            result.pop("capture_continuity", None)
        return result

    @model_validator(mode="after")
    def current_source_projection(self):
        source, working, queue = self.source, self.working, self.review_queue
        statuses = {
            "translation_pending": "not_ready",
            "review_required": "review_required",
            "transcript_ready": "complete",
            "quality_blocked": "quality_blocked",
        }
        if self.status not in statuses:
            if (
                any(
                    v is not None
                    for v in (source, working, queue, self.capture_continuity)
                )
                or self.translation_progress
            ):
                raise ValueError("invalid_creator_metadata_view")
            return self
        if source is None or working is None or queue is None:
            raise ValueError("incomplete_creator_view")
        if (
            queue.session_id != self.session_id
            or working.source_transcript_version != source.source_transcript_version
            or queue.source_transcript_version != source.source_transcript_version
            or queue.working_transcript_id != working.working_transcript_id
            or queue.working_transcript_version != working.working_transcript_version
            or queue.status != statuses[self.status]
            or len(source.segments) != len(working.segments)
        ):
            raise ValueError("invalid_creator_lineage")
        originals = {s.source_segment_id: s for s in source.segments}
        blockers = {(b.source_segment_id, b.code) for b in working.blockers}
        if len(blockers) != len(working.blockers) or any(
            i not in originals for i, _ in blockers
        ):
            raise ValueError("invalid_creator_blockers")
        codes_by_segment: dict[UUID, set[str]] = {}
        for segment_id, code in blockers:
            codes_by_segment.setdefault(segment_id, set()).add(code)
        end = 0
        unresolved = set()
        for original, projected in zip(source.segments, working.segments, strict=True):
            if any(
                getattr(original, k) != getattr(projected, k)
                for k in SegmentOrigin.model_fields
            ):
                raise ValueError("invalid_creator_segment_lineage")
            codes = codes_by_segment.get(original.source_segment_id, set()).copy()
            overlap = original.start_ms < end
            end = max(end, original.end_ms)
            if ("source_overlap" in codes) != overlap:
                raise ValueError("invalid_creator_overlap")
            codes.discard("source_overlap")
            if original.source_locale == "en-US":
                if (
                    projected.resolution != "english_source"
                    or projected.content != original.content
                    or codes
                ):
                    raise ValueError("invalid_creator_english_source")
            elif original.source_locale == "unknown":
                if projected.resolution != "unresolved" or codes != {
                    "source_locale_unknown"
                }:
                    raise ValueError("invalid_creator_unknown_source")
            elif projected.resolution == "unresolved":
                if len(codes) != 1 or not codes <= {
                    "translation_missing",
                    "translation_failed",
                    "translation_retry_required",
                    "translation_review_required",
                }:
                    raise ValueError("invalid_creator_translation_blocker")
            elif codes:
                raise ValueError("invalid_creator_reviewed_blocker")
            if projected.resolution == "unresolved":
                unresolved.add(original.source_segment_id)
        ready = not blockers and not unresolved
        codes = {code for _, code in blockers}
        expected = "transcript_ready"
        if codes & {"source_locale_unknown", "source_overlap"}:
            expected = "quality_blocked"
        elif codes & {
            "translation_missing",
            "translation_failed",
            "translation_retry_required",
        }:
            expected = "translation_pending"
        elif "translation_review_required" in codes:
            expected = "review_required"
        if self.status != expected or working.note_ready != ready:
            raise ValueError("invalid_creator_readiness")
        review_ids = {i.source_segment_id for i in queue.items}
        expected_ids = (
            {i for i, code in blockers if code == "translation_review_required"}
            if self.status == "review_required"
            else set()
        )
        if review_ids != expected_ids or len(review_ids) != len(queue.items):
            raise ValueError("invalid_creator_queue")
        for item in queue.items:
            original = originals[item.source_segment_id]
            if (
                original.source_locale != "es-US"
                or original.content != item.source_content
                or any(
                    getattr(original, k) != getattr(item, k)
                    for k in (
                        "source_segment_version",
                        "speaker_role",
                        "start_ms",
                        "end_ms",
                    )
                )
                or not item.translated_content.strip()
            ):
                raise ValueError("invalid_creator_review_item")
        progress_ids = {p.source_segment_id for p in self.translation_progress}
        if (
            len(progress_ids) != len(self.translation_progress)
            or progress_ids & review_ids
        ):
            raise ValueError("invalid_creator_progress")
        for progress in self.translation_progress:
            original = originals.get(progress.source_segment_id)
            if (
                original is None
                or original.source_locale != "es-US"
                or original.source_segment_version != progress.source_segment_version
                or original.source_segment_id not in unresolved
            ):
                raise ValueError("invalid_creator_progress_lineage")
        return self
