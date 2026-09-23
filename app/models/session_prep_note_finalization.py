"""Revision-bound clinician review evidence for notes used by Session Prep."""

from datetime import datetime

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TypeIDMixin
from activemodel.types import TypeIDType
from app.models.ai_visit_note import AIVisitNote
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field


class SessionPrepNoteFinalization(
    BaseModel, TypeIDMixin("prep_note_finalization"), table=True
):
    """Immutable snapshot of the exact note revision a clinician reviewed.

    This is input eligibility for Session Prep, not an EHR signature. A changed
    or superseded source immediately becomes ineligible even if this evidence
    remains. The reviewed content stays inside the platform data boundary.
    """

    __table_args__ = (
        sa.UniqueConstraint(
            "note_id", "source_version", name="uq_prep_note_finalization_version"
        ),
        sa.UniqueConstraint(
            "note_id", "content_sha256", name="uq_prep_note_finalization_content"
        ),
        sa.CheckConstraint(
            "source_version > 0", name="ck_prep_note_finalization_version"
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_prep_note_finalization_digest",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(reviewed_content) = 'object'",
            name="ck_prep_note_finalization_content_object",
        ),
    )

    note_id: TypeIDType = AIVisitNote.foreign_key(index=True)
    doctor_id: TypeIDType = Doctor.foreign_key(index=True)
    appointment_id: TypeIDType = Appointment.foreign_key(index=True)
    patient_id: TypeIDType = Patient.foreign_key(index=True)
    source_version: int = Field(ge=1)
    content_sha256: str = Field(min_length=64, max_length=64)
    # JSONB object keys are unordered; section/text pairs preserve the reviewed
    # note's section order inside the immutable snapshot without a schema change.
    reviewed_content: dict[str, list[list[str]]] = Field(
        sa_type=JSONB, exclude=True, repr=False
    )
    finalized_at: datetime = Field(sa_type=sa.DateTime(timezone=True))  # type: ignore
