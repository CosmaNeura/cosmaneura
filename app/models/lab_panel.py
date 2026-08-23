from datetime import date
from enum import Enum

from activemodel import BaseModel
from activemodel.mixins import SoftDeletionMixin, TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Field, Relationship

from .appointment import Appointment
from .doctor import Doctor
from .patient import Patient


class LabPanelSource(str, Enum):
    manual = "manual"
    upload = "upload"


class LabPanelStatus(str, Enum):
    pending = "pending"  # uploaded, extraction queued
    completed = "completed"
    failed = "failed"


class LabPanel(
    BaseModel,
    TimestampsMixin,
    SoftDeletionMixin,
    TypeIDMixin("lab_panel"),
    table=True,
):
    """A set of lab results for a patient, entered manually or extracted from
    an uploaded lab PDF. Individual biomarker values live in LabResult.

    Content is PHI: logs carry IDs only. Uploaded PDFs go to the PHI-capable
    Azure container, never a de-identified one.
    """

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    patient_id: TypeIDType = Patient.foreign_key()
    patient: Patient = Relationship()

    appointment_id: TypeIDType | None = Appointment.foreign_key(nullable=True)

    source: LabPanelSource = Field(default=LabPanelSource.manual)
    status: LabPanelStatus = Field(default=LabPanelStatus.completed)

    azure_file_name: str | None = Field(default=None)
    "PDF file name in the PHI upload container, when source=upload"

    collected_on: date | None = Field(default=None)
    "date the specimen was collected, if known"
