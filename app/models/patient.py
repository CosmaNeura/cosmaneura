import uuid
from datetime import datetime

from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from sqlmodel import Relationship

from .doctor import Doctor


class Patient(BaseModel, TimestampsMixin, TypeIDMixin("patient"), table=True):
    """
    Represents a patient in the system. This to simply help a doctor to find a transcript or intake.

    This is not perfect:

    - Doctors will end up having patients with the same name
    - Patient name could be entered in multiple different ways

    Fixing this is left as an exercise to the reader.
    """

    doctor_id: uuid.UUID = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    name: str
    date_of_birth: datetime
