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

    # TODO should make this an enum
    created_by_system: str | None = Field(default="gui", nullable=True)
    "the system the appointment was created by. API, UI, etc"

    external_id: str | None
    "external ID of the appointment in acuity, or another external system"

    appointment_date: datetime
    "date of the appointment"

    def is_expired(self) -> bool:
        """
        Check if the appointment is expired. An appointment is considered expired if it is in the past.
        """
        return self.appointment_date < datetime.now()

    appointment_name: str | None = Field(nullable=True, default=None)
    "a couple words about the appointment"

    appointment_notes_preview: str | None = Field(nullable=True, default=None)
    "longer summary about the appointment"

    @computed_field
    @property
    def patient_name(self) -> str:
        return self.patient.name

    # TODO not absolutely required right now since ALL appointments have audio files, until we integrate with accuity
    # @computed_field
    # @property
    # def has_audio_upload(self) -> bool:
    #     import sqlmodel as sm
    #     from app.models.visit_audio_file import VisitAudioFile

    #     with SessionManager.get_instance().get_session() as session:
    #         # TODO this method is deprecated, but it wasn't clear what the replacement was?
    #         return session.query(
    #             sm.select(VisitAudioFile)
    #             .where(VisitAudioFile.appointment_id == self.id)
    #             .exists()
    #         ).scalar()
