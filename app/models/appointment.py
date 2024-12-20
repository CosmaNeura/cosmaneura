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

    user_id: uuid.UUID = User.foreign_key()
    user: User = Relationship()

    patient_id: uuid.UUID = Patient.foreign_key()
    patient: Patient = Relationship()

    appointment_date: datetime
