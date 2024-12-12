import uuid

from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from sqlmodel import Field, Relationship
from .patient import Patient

class Appointment(BaseModel, TimestampsMixin, TypeIDMixin("appointment"), table=True):
    """
    Represents an appointment with a patient from accuity
    """
    patient_id: uuid.UUID = Field(foreign_key="patient.id", nullable=False)
    patient: Patient = Relationship()
    location: str
