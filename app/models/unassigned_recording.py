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
from .zoom_meeting import ZoomMeeting


class UnassignedRecordingReason(str, Enum):
    zoom_no_calendar_match = "zoom_no_calendar_match"
    "a Zoom recording finished with no bound appointment (docs/zoom-flow-implementation.md 2.4/2.5)"

    scheduling_sync_ambiguous_patient = "scheduling_sync_ambiguous_patient"
    "a SchedulingProvider event had no PatientExternalIdentity match and no confident fallback"


class UnassignedRecordingStatus(str, Enum):
    pending = "pending"
    resolved = "resolved"
    dismissed = "dismissed"


class UnassignedRecording(
    BaseModel, TimestampsMixin, TypeIDMixin("unassigned_rec"), table=True
):
    """
    Shared "needs your confirmation" queue for anything this platform is not
    confident enough to auto-assign: an unmatched Zoom recording, or a
    scheduling-sync roster row with no deterministic patient match. A
    suggestion may be shown, but it is never auto-applied — a doctor always
    resolves the row explicitly. See contracts/scheduling-provider-v1.md.
    """

    __table_args__ = (
        sa.CheckConstraint(
            "(reason = 'zoom_no_calendar_match' AND zoom_meeting_id IS NOT NULL) OR "
            "(reason = 'scheduling_sync_ambiguous_patient' AND source IS NOT NULL "
            "AND external_appointment_ref IS NOT NULL)",
            name="ck_unassigned_recording_reason_fields_complete",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND resolved_at IS NULL AND resolved_by IS NULL) OR "
            "(status != 'pending' AND resolved_at IS NOT NULL AND resolved_by IS NOT NULL)",
            name="ck_unassigned_recording_resolution_complete",
        ),
    )

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    reason: UnassignedRecordingReason
    status: UnassignedRecordingStatus = Field(default=UnassignedRecordingStatus.pending)

    # -- zoom_no_calendar_match fields --
    zoom_meeting_id: TypeIDType | None = ZoomMeeting.foreign_key(nullable=True)
    zoom_meeting: ZoomMeeting | None = Relationship()

    suggested_patient_name: str | None = Field(default=None, nullable=True)
    "AI-inferred from the first 60-90s of transcript; a suggestion only, never an assignment"

    # -- scheduling_sync_ambiguous_patient fields --
    source: SchedulingProviderSource | None = Field(default=None, nullable=True)
    external_appointment_ref: str | None = Field(default=None, nullable=True)
    external_patient_ref: str | None = Field(default=None, nullable=True)
    candidate_patient_name: str | None = Field(default=None, nullable=True)
    candidate_patient_email: str | None = Field(default=None, nullable=True)
    candidate_patient_phone: str | None = Field(default=None, nullable=True)
    candidate_appointment_start_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )

    # -- resolution, either path --
    resolved_patient_id: TypeIDType | None = Patient.foreign_key(nullable=True)
    resolved_patient: Patient | None = Relationship()

    resolved_appointment_id: TypeIDType | None = Appointment.foreign_key(nullable=True)
    resolved_appointment: Appointment | None = Relationship()
    resolved_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    resolved_by: str | None = Field(default=None, nullable=True)
    "opaque actor reference (doctor_ref) who confirmed or dismissed this row"
