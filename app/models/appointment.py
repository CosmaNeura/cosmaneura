import uuid
from datetime import datetime

from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from app.models.doctor import Doctor
from sqlmodel import Field, Relationship

from .patient import Patient


class Appointment(BaseModel, TimestampsMixin, TypeIDMixin("appointment"), table=True):
    """
    Represents an appointment with a patient from accuity
    """

    doctor_id: uuid.UUID = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    patient_id: uuid.UUID = Patient.foreign_key()
    patient: Patient = Relationship()

    appointment_date: datetime
