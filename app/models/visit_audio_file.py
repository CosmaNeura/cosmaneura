from activemodel.types import TypeIDType
import uuid

from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from sqlmodel import Relationship

from .appointment import Appointment
from .doctor import Doctor
from .patient import Patient


class VisitAudioFile(
    BaseModel, TimestampsMixin, TypeIDMixin("visit_audio_file"), table=True
):
    """
    Link to the blob for a specific visit audio file

    1. User drops file into upload area
    2. User hits upload which hits the backend API which generates a signed upload URL
    3. User uploads file to signed upload URL, does not hit the backend API for upload
    """

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    patient_id: TypeIDType = Patient.foreign_key()
    patient: Patient = Relationship()

    appointment_id: TypeIDType = Appointment.foreign_key()
    appointment: Appointment = Relationship()

    azure_file_name: str
    "file name of the blob in azure"
