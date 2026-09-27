"""Immutable captured-gap provenance bound to one admitted raw source."""

from uuid import UUID

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.scribe_capture_binding import ScribeCaptureBinding
from sqlmodel import Field


class ScribeCaptureGapManifest(
    BaseModel, TimestampsMixin, TypeIDMixin("scribe_gap_manifest"), table=True
):
    __table_args__ = (
        sa.UniqueConstraint("binding_id", name="uq_scribe_gap_manifest_binding"),
        sa.UniqueConstraint("session_id", name="uq_scribe_gap_manifest_session"),
        sa.UniqueConstraint(
            "initial_transcript_id", name="uq_scribe_gap_initial_transcript"
        ),
        sa.CheckConstraint(
            "raw_source_sha256 ~ '^[0-9a-f]{64}$' AND recovery_sha256 ~ '^[0-9a-f]{64}$' "
            "AND (initial_source_sha256 IS NULL OR initial_source_sha256 ~ '^[0-9a-f]{64}$')",
            name="ck_scribe_gap_hashes",
        ),
        sa.CheckConstraint(
            "(initial_transcript_id IS NULL) = (initial_source_sha256 IS NULL)",
            name="ck_scribe_gap_initial_mapping",
        ),
        sa.CheckConstraint(
            "COALESCE((octet_length(recovery_canonical) BETWEEN 2 AND 2000000 "
            "AND encode(sha256(convert_to(recovery_canonical, 'UTF8')), 'hex') = recovery_sha256 "
            "AND jsonb_typeof(recovery_canonical::jsonb) = 'object' "
            "AND recovery_canonical::jsonb->>'session_id' = session_id::text "
            "AND jsonb_typeof(recovery_canonical::jsonb->'recoveries') = 'array' "
            "AND jsonb_array_length(recovery_canonical::jsonb->'recoveries') BETWEEN 1 AND 1000), false)",
            name="ck_scribe_gap_canonical_manifest",
        ),
    )

    binding_id: TypeIDType = ScribeCaptureBinding.foreign_key()
    session_id: UUID
    raw_source_id: UUID
    raw_source_sha256: str = Field(max_length=64)
    recovery_sha256: str = Field(max_length=64)
    recovery_canonical: str = Field(sa_type=sa.Text)  # type: ignore
    raw_core_transcript_id: TypeIDType = AIVisitTranscript.foreign_key()
    initial_transcript_id: TypeIDType | None = AIVisitTranscript.foreign_key(
        nullable=True
    )
    initial_source_sha256: str | None = Field(default=None, max_length=64)
