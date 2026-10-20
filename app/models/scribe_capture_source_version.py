"""Immutable mapping of a corrected full capture snapshot to its Core transcript."""

from uuid import UUID

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from app.models.ai_visit_transcript import AIVisitTranscript
from app.models.scribe_capture_binding import ScribeCaptureBinding
from sqlmodel import Field


class ScribeCaptureSourceVersion(
    BaseModel, TimestampsMixin, TypeIDMixin("scribe_source_version"), table=True
):
    __table_args__ = (
        sa.UniqueConstraint(
            "binding_id",
            "session_id",
            "working_version",
            name="uq_scribe_working_version",
        ),
        sa.UniqueConstraint("transcript_id", name="uq_scribe_working_core_transcript"),
        sa.CheckConstraint("working_version >= 2", name="ck_scribe_working_version"),
        sa.CheckConstraint(
            "raw_source_sha256 ~ '^[0-9a-f]{64}$' AND working_sha256 ~ '^[0-9a-f]{64}$' "
            "AND source_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_scribe_working_hashes",
        ),
    )

    binding_id: TypeIDType = ScribeCaptureBinding.foreign_key()
    session_id: UUID
    raw_source_id: UUID
    raw_source_sha256: str = Field(max_length=64)
    working_transcript_id: UUID
    working_version: int
    working_sha256: str = Field(max_length=64)
    source_sha256: str = Field(max_length=64)
    raw_core_transcript_id: TypeIDType = AIVisitTranscript.foreign_key()
    transcript_id: TypeIDType = AIVisitTranscript.foreign_key()
