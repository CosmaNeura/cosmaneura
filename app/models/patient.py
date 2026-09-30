from datetime import datetime

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
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

    # Supports database-enforced doctor/patient scope on the synthetic EHR
    # designation. `id` remains the primary key; this redundant unique tuple is
    # intentional because PostgreSQL requires the referenced composite key to
    # be unique as a tuple.
    __table_args__ = (
        sa.UniqueConstraint(
            "id",
            "doctor_id",
            name="uq_patient_id_doctor_id_ehr_sandbox",
        ),
    )

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    name: str
    date_of_birth: datetime | None = None
