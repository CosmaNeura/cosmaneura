from datetime import datetime
from enum import Enum

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Column, DateTime, Field, Relationship

from .appointment import Appointment
from .doctor import Doctor
from .patient import Patient
from .patient_external_identity import SchedulingProviderSource


class AmbiguousPatientMatchStatus(str, Enum):
    pending = "pending"
    resolved = "resolved"
    dismissed = "dismissed"


class AmbiguousPatientMatch(
    BaseModel, TimestampsMixin, TypeIDMixin("amb_match"), table=True
):
    """
    "Needs your confirmation" queue for a SchedulingProvider roster-sync
    event that had no deterministic PatientExternalIdentity match. A
    candidate name/email/phone is shown as-is; it is never auto-applied — a
    doctor always resolves the row explicitly. See
    contracts/scheduling-provider-v1.md.

    Scoped to roster sync only, not Zoom capture: Zoom never records without
    a prior explicit, occurrence-scoped authorization (see
    app/services/zoom_webhooks.py::_consume_bound_appointment), which is
    also this platform's recording-consent gate — a "capture first, match
    later" path would mean recording before consent exists, so it isn't
    built (decided 2026-09-09).
    """

    __table_args__ = (
        sa.CheckConstraint(
            "(status = 'pending' AND resolved_at IS NULL AND resolved_by IS NULL) OR "
            "(status != 'pending' AND resolved_at IS NOT NULL AND resolved_by IS NOT NULL)",
            name="ck_ambiguous_patient_match_resolution_complete",
        ),
    )

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    status: AmbiguousPatientMatchStatus = Field(
        default=AmbiguousPatientMatchStatus.pending
    )

    source: SchedulingProviderSource
    external_appointment_ref: str
    external_patient_ref: str | None = Field(default=None, nullable=True)
    candidate_patient_name: str | None = Field(default=None, nullable=True)
    candidate_patient_email: str | None = Field(default=None, nullable=True)
    candidate_patient_phone: str | None = Field(default=None, nullable=True)
    candidate_appointment_start_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )

    resolved_patient_id: TypeIDType | None = Patient.foreign_key(nullable=True)
    resolved_patient: Patient | None = Relationship()

    resolved_appointment_id: TypeIDType | None = Appointment.foreign_key(nullable=True)
    resolved_appointment: Appointment | None = Relationship()
    resolved_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    resolved_by: str | None = Field(default=None, nullable=True)
    "opaque actor reference (doctor_ref) who confirmed or dismissed this row"
