import uuid
from datetime import datetime

from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from app.models.user import User
from sqlmodel import Field, Relationship

from .patient import Patient


class Appointment(BaseModel, TimestampsMixin, TypeIDMixin("appointment"), table=True):
    """
    Represents an appointment with a patient from accuity
    """

    user_id: uuid.UUID = Field(foreign_key="user.id", nullable=False)
    user: User = Relationship()

    patient_id: uuid.UUID = Field(foreign_key="patient.id", nullable=False)
    patient: Patient = Relationship()

    appointment_date: datetime
