"""Immutable mapping from reviewed bilingual evidence to a canonical Core transcript."""

from uuid import UUID

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.scribe_capture_binding import ScribeCaptureBinding
from sqlmodel import Field


class ScribeCaptureReviewedSource(
    BaseModel, TimestampsMixin, TypeIDMixin("scribe_review_source"), table=True
):
    __table_args__ = (
        sa.UniqueConstraint(
            "binding_id", "working_version", name="uq_scribe_review_working_version"
        ),
        sa.UniqueConstraint("transcript_id", name="uq_scribe_review_core_transcript"),
        sa.CheckConstraint(
            "source_version >= 1 AND working_version >= 1",
            name="ck_scribe_review_versions",
        ),
        sa.CheckConstraint(
            "source_sha256 ~ '^[a-f0-9]{64}$' AND payload_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_scribe_review_source_digests",
        ),
    )

    binding_id: TypeIDType = ScribeCaptureBinding.foreign_key()
    session_id: UUID
    source_version: int
    working_transcript_id: UUID
    working_version: int
    source_sha256: str = Field(max_length=64)
    payload_sha256: str = Field(max_length=64)
    transcript_id: TypeIDType = AIVisitTranscript.foreign_key()
