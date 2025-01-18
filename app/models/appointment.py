from datetime import datetime

from pydantic import computed_field

from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from app.models.doctor import Doctor
from sqlmodel import Field, Relationship

from .patient import Patient


class Appointment(BaseModel, TimestampsMixin, TypeIDMixin("appointment"), table=True):
    """
    Represents an appointment with a patient from accuity
    """

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    patient_id: TypeIDType = Patient.foreign_key()
    patient: Patient = Relationship()

    appointment_date: datetime
    "date of the appointment"

    appointment_name: str | None = Field(nullable=True)
    "a couple words about the appointment"

    appointment_notes_preview: str | None = Field(nullable=True)
    "longer summary about the appointment"

    @computed_field
    @property
    def patient_name(self) -> str:
        return self.patient.name
