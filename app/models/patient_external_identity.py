from enum import Enum

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Relationship

from .doctor import Doctor
from .patient import Patient


class SchedulingProviderSource(str, Enum):
    acuity = "acuity"
    google_calendar = "google_calendar"


class PatientExternalIdentity(
    BaseModel, TimestampsMixin, TypeIDMixin("pat_ext_id"), table=True
):
    """
    Deterministic mapping from a scheduling provider's stable client/attendee
    reference to a local Patient, scoped per doctor. This is the only
    mechanism that auto-resolves a returning patient across syncs; see
    contracts/scheduling-provider-v1.md in cosmaneura-architecture. Never
    populated by fuzzy name/date-of-birth matching.
    """

    __table_args__ = (
        sa.UniqueConstraint(
            "doctor_id",
            "source",
            "external_patient_ref",
            name="uq_patient_external_identity_doctor_source_ref",
        ),
    )

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    patient_id: TypeIDType = Patient.foreign_key()
    patient: Patient = Relationship()

    source: SchedulingProviderSource
    "which SchedulingProvider adapter this reference came from"

    external_patient_ref: str
    "opaque, stable client/attendee identifier as given by the source"
