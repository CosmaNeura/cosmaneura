"""Immutable Core source/completion evidence; never a CAB or model grant."""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TypeIDMixin
from activemodel.types import TypeIDType
from app.models.ai_visit_note import AIVisitNote
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.practice import Practice
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field


class BillingHeaderPreparedOperation(
    BaseModel, TypeIDMixin("billing_operation"), table=True
):
    __table_args__ = (
        sa.UniqueConstraint("operation_id", name="uq_billing_operation_id"),
        sa.UniqueConstraint(
            "appointment_id",
            "schema_ref",
            "source_revision",
            name="uq_billing_source_revision",
        ),
        sa.CheckConstraint("source_revision > 0", name="ck_billing_source_revision"),
        sa.CheckConstraint(
            "request_sha256 ~ '^[a-f0-9]{64}$'", name="ck_billing_request_digest"
        ),
    )
    operation_id: UUID = Field(sa_type=sa.Uuid, nullable=False)
    doctor_id: TypeIDType = Doctor.foreign_key()
    practice_id: TypeIDType = Practice.foreign_key()
    appointment_id: TypeIDType = Appointment.foreign_key()
    patient_id: TypeIDType = Patient.foreign_key()
    note_id: TypeIDType = AIVisitNote.foreign_key()
    schema_ref: str = Field(max_length=128)
    source_revision: int
    request_sha256: str = Field(max_length=64)
    # Server-derived attribution includes issuer/subject/session digest, never tokens.
    actor: dict = Field(sa_type=JSONB, exclude=True, repr=False)
    source: dict = Field(sa_type=JSONB, exclude=True, repr=False)
    ordered_content: list = Field(sa_type=JSONB, exclude=True, repr=False)
    projected_text: str = Field(sa_type=sa.Text, exclude=True, repr=False)
    visit: dict = Field(sa_type=JSONB, exclude=True, repr=False)
    admission: dict = Field(sa_type=JSONB, exclude=True, repr=False)
    completed_at: datetime = Field(
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False)
    )
