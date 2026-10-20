"""Immutable gap provenance and the small review projection derived from it."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from app.services.scribe_capture_recovery_contract import GapReason
from app.services.scribe_capture_working_contract import (
    Digest,
    ProtectedContract,
    canonical_document,
)
from app.services.scribe_workflow import CaptureSource

from app.models.scribe_capture_gap_manifest import ScribeCaptureGapManifest
from sqlmodel import Session, select


class GapProvenanceConflict(ValueError):
    """Persisted evidence does not match the current qualified source."""


class GapObservation(ProtectedContract):
    gap_id: UUID
    reason_code: GapReason
    observed_at: AwareDatetime
    acknowledged_at: AwareDatetime
    decision: Literal["resume_capture", "finish_capture"]


class CaptureGapReview(ProtectedContract):
    recovery_sha256: Digest
    observations: tuple[GapObservation, ...] = Field(min_length=1, max_length=1000)
    review_required: Literal[True] = True


def review_projection(source: CaptureSource | None) -> CaptureGapReview | None:
    if source is None or not source.capture_incomplete:
        return None
    return CaptureGapReview(
        recovery_sha256=source.recovery_sha256,
        observations=tuple(
            GapObservation(
                gap_id=row.request.gap_id,
                reason_code=row.request.reason_code,
                observed_at=row.request.observed_at,
                acknowledged_at=row.request.acknowledged_at,
                decision=row.request.decision,
            )
            for row in source.recovery_history.recoveries
        ),
    )


def current_manifest(
    session: Session, binding_id, session_id: UUID | None = None
) -> ScribeCaptureGapManifest | None:
    if session_id is None:
        from app.models.scribe_capture_admission import ScribeCaptureAdmission

        legacy = session.exec(
            select(ScribeCaptureAdmission.session_id).where(
                ScribeCaptureAdmission.binding_id == binding_id,
                ScribeCaptureAdmission.recording_key_sha256 == "legacy",
            )
        ).one_or_none()
        if legacy is None:
            return None
        session_id = legacy
    return session.exec(
        select(ScribeCaptureGapManifest).where(
            ScribeCaptureGapManifest.binding_id == binding_id,
            ScribeCaptureGapManifest.session_id == session_id,
        )
    ).one_or_none()


def validate_manifest(
    manifest: ScribeCaptureGapManifest, source: CaptureSource, raw_id
) -> None:
    if (
        not source.capture_incomplete
        or manifest.session_id != source.session_id
        or manifest.raw_source_id != source.raw_transcript.id
        or manifest.raw_source_sha256 != source.working_transcript.raw_transcript_sha256
        or manifest.raw_core_transcript_id != raw_id
        or manifest.recovery_sha256 != source.recovery_sha256
        or manifest.recovery_canonical != canonical_document(source.recovery_history)
    ):
        raise GapProvenanceConflict


def import_manifest(
    session: Session,
    binding_id,
    source: CaptureSource,
    raw_id,
    initial_transcript_id,
    initial_source_sha256,
) -> None:
    retained = current_manifest(session, binding_id, source.session_id)
    if retained is not None:
        validate_manifest(retained, source, raw_id)
        return
    if not source.capture_incomplete:
        return
    session.add(
        ScribeCaptureGapManifest(
            binding_id=binding_id,
            session_id=source.session_id,
            raw_source_id=source.raw_transcript.id,
            raw_source_sha256=source.working_transcript.raw_transcript_sha256,
            recovery_sha256=source.recovery_sha256,
            recovery_canonical=canonical_document(source.recovery_history),
            raw_core_transcript_id=raw_id,
            initial_transcript_id=initial_transcript_id,
            initial_source_sha256=initial_source_sha256,
        )
    )
    session.flush()


def gap_grounding(source: CaptureSource | None) -> str:
    if source is None or not source.capture_incomplete:
        return ""
    # Do not feed device timing as encounter duration or speculate about what
    # happened while capture was unavailable. Detailed observations belong in
    # the separate human review surface, not fabricated clinical content.
    return (
        "\nCAPTURE LIMITATION: This recording has acknowledged interruptions. "
        "It is incomplete. Do not infer missing speech, gap duration, findings, "
        "or completed actions. Use only the captured and explicitly corrected "
        "source. The clinician must separately review these capture gaps.\n"
    )
