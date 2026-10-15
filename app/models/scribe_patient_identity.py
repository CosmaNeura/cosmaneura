"""Provider-confirmed association; original capture and encounter IDs stay fixed."""

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from app.models.appointment import Appointment
from app.models.authority import AuthorityActor, AuthorityTenant
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.scribe_capture_binding import ScribeCaptureProviderLink
from sqlmodel import Field


class ScribePatientIdentity(
    BaseModel, TimestampsMixin, TypeIDMixin("scribe_patient_identity"), table=True
):
    __table_args__ = (
        sa.UniqueConstraint("appointment_id", name="uq_scribe_patient_identity_visit"),
        sa.CheckConstraint(
            "state IN ('provisional', 'matched', 'confirmed')",
            name="ck_scribe_patient_identity_state",
        ),
        sa.CheckConstraint("version >= 0", name="ck_scribe_patient_identity_version"),
    )
    appointment_id: TypeIDType = Appointment.foreign_key()
    doctor_id: TypeIDType = Doctor.foreign_key()
    actor_id: TypeIDType = AuthorityActor.foreign_key()
    tenant_id: TypeIDType = AuthorityTenant.foreign_key()
    provider_link_id: TypeIDType = ScribeCaptureProviderLink.foreign_key()
    source_patient_id: TypeIDType = Patient.foreign_key()
    canonical_patient_id: TypeIDType = Patient.foreign_key(index=True)
    state: str = Field(max_length=16)
    version: int = 0
