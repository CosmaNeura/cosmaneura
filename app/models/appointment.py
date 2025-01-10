from datetime import datetime

from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from app.models.doctor import Doctor
from sqlmodel import Relationship

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
