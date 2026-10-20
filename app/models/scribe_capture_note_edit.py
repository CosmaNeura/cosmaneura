"""Retained idempotency/provenance for a human's explicit captured-note edit."""

from uuid import UUID

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from app.models.ai_visit_note import AIVisitNote
from app.models.scribe_capture_binding import ScribeCaptureBinding
from sqlmodel import Field


class ScribeCaptureNoteEdit(
    BaseModel, TimestampsMixin, TypeIDMixin("scribe_note_edit"), table=True
):
    __table_args__ = (
        sa.UniqueConstraint("binding_id", "client_key", name="uq_scribe_note_edit_key"),
        sa.UniqueConstraint("result_note_id", name="uq_scribe_note_edit_result"),
        sa.CheckConstraint(
            "request_sha256 ~ '^[0-9a-f]{64}$' AND source_sha256 ~ '^[0-9a-f]{64}$' "
            "AND result_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_scribe_note_edit_digests",
        ),
    )

    binding_id: TypeIDType = ScribeCaptureBinding.foreign_key()
    client_key: UUID
    session_id: UUID
    base_note_id: TypeIDType = AIVisitNote.foreign_key()
    result_note_id: TypeIDType = AIVisitNote.foreign_key()
    request_sha256: str = Field(max_length=64)
    source_sha256: str = Field(max_length=64)
    result_sha256: str = Field(max_length=64)


# Immutable closure of an exact, unpublished edit request.
class ScribeCaptureNoteSaveFence(
    BaseModel, TimestampsMixin, TypeIDMixin("scribe_note_close"), table=True
):
    __table_args__ = (
        sa.UniqueConstraint(
            "binding_id", "client_key", name="uq_scribe_note_close_key"
        ),
        sa.CheckConstraint(
            "request_sha256 ~ '^[0-9a-f]{64}$'", name="ck_scribe_note_close_digest"
        ),
    )

    binding_id: TypeIDType = ScribeCaptureBinding.foreign_key()
    client_key: UUID
    session_id: UUID
    base_note_id: TypeIDType = AIVisitNote.foreign_key()
    request_sha256: str = Field(max_length=64)


# The retained 9172abd84a3d schema has no inferred TypeID column comment.
ScribeCaptureNoteSaveFence.metadata.tables[
    "scribe_capture_note_save_fence"
].c.id.comment = None
