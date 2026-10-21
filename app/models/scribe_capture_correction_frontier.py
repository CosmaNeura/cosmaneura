"""Content-free, monotonic boundary established by an acknowledged native edit."""

from uuid import UUID

from pydantic import field_validator

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from app.models.scribe_capture_admission import ScribeCaptureAdmission
from app.models.scribe_capture_binding import ScribeCaptureBinding
from sqlmodel import Field


class ScribeCaptureCorrectionFrontier(
    BaseModel, TimestampsMixin, TypeIDMixin("scribe_correction"), table=True
):
    """A correction never authorizes access or rewrites retained note evidence."""

    __table_args__ = (
        sa.UniqueConstraint("admission_id", name="uq_scribe_correction_admission"),
        sa.UniqueConstraint(
            "binding_id", "session_id", name="uq_scribe_correction_session"
        ),
        sa.CheckConstraint(
            "minimum_working_version >= 2", name="ck_scribe_correction_version"
        ),
    )

    admission_id: TypeIDType = ScribeCaptureAdmission.foreign_key()
    binding_id: TypeIDType = ScribeCaptureBinding.foreign_key()
    session_id: UUID
    working_transcript_id: UUID
    minimum_working_version: int = Field(ge=2)
    edit_id: UUID

    @field_validator("minimum_working_version", mode="before")
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int:
            raise ValueError("Capture correction version is unavailable")
        return value
