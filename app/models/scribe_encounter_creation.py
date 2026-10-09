"""Atomic retry receipt for provider-entered Core encounters; no clinical text."""

from uuid import UUID

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from sqlmodel import Field


class ScribeEncounterCreation(
    BaseModel, TimestampsMixin, TypeIDMixin("scribe_encounter_creation"), table=True
):
    __table_args__ = (
        sa.UniqueConstraint(
            "doctor_id", "creation_key", name="uq_scribe_encounter_creation_key"
        ),
        sa.UniqueConstraint(
            "appointment_id", name="uq_scribe_encounter_creation_appointment"
        ),
        sa.UniqueConstraint(
            "workspace_id", name="uq_scribe_encounter_creation_workspace"
        ),
        sa.CheckConstraint(
            "length(request_sha256) = 64", name="ck_scribe_encounter_creation_digest"
        ),
    )

    doctor_id: TypeIDType = Doctor.foreign_key(index=True)
    provider_link_id: TypeIDType = ScribeCaptureProviderLink.foreign_key()
    patient_id: TypeIDType = Patient.foreign_key()
    appointment_id: TypeIDType = Appointment.foreign_key()
    creation_key: UUID
    workspace_id: UUID
    request_sha256: str = Field(max_length=64)
