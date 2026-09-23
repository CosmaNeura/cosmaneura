from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import computed_field

import sqlalchemy as sa
from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from app.models.doctor import Doctor
from sqlmodel import Field, Relationship

if TYPE_CHECKING:
    from app.models.intake.intake import Intake

from enum import Enum

from .patient import Patient


class IntakeStatus(str, Enum):
    none = "none"
    pending = "pending"
    processing = "processing"
    complete = "complete"


class Appointment(BaseModel, TimestampsMixin, TypeIDMixin("appointment"), table=True):
    """
    Represents an appointment with a patient from accuity
    """

    __table_args__ = (
        # Closes the race in app/commands/intake/create.py's non-atomic
        # get()-then-create(): a second concurrent delivery for the same
        # external appointment now fails at the DB rather than creating a
        # duplicate. Partial so appointments with no external_id (created
        # by staff in the GUI) are unaffected.
        sa.Index(
            "uq_appointment_doctor_external_id",
            "doctor_id",
            "external_id",
            unique=True,
            postgresql_where=sa.text("external_id IS NOT NULL"),
        ),
    )

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    patient_id: TypeIDType = Patient.foreign_key()
    patient: Patient = Relationship()

    intake: "Intake" = Relationship(back_populates="appointment")

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

    @computed_field
    @property
    def intake_status(self) -> IntakeStatus:
        if not self.intake:
            return IntakeStatus.none

        if self.intake.unanswered_questions():
            return IntakeStatus.pending

        if not self.intake.summary:
            return IntakeStatus.processing

        return IntakeStatus.complete

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
