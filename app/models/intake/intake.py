from pydantic import BaseModel as PydanticBaseModel
from pydantic import HttpUrl

from activemodel import BaseModel
from activemodel.mixins import (
    PydanticJSONMixin,
    SoftDeletionMixin,
    TimestampsMixin,
    TypeIDMixin,
)
from activemodel.types import TypeIDType
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship

from ..appointment import Appointment
from ..doctor import Doctor
from ..patient import Patient
from .doctor_intake_schema import DoctorIntakeSchema, IntakeQuestion


class IntakeAnswer(PydanticBaseModel):
    question_key: str
    "unique key for the question"

    answer: str
    "the user's answer to the question"

    timestamp: int
    "the timestamp when the question was answered"


class Intake(
    BaseModel,
    PydanticJSONMixin,
    TimestampsMixin,
    SoftDeletionMixin,
    TypeIDMixin("intake"),
    table=True,
):
    """
    This represents a chat experience with a patient to gather information about them.
    """

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    appointment_id: TypeIDType = Appointment.foreign_key()
    appointment: Appointment = Relationship()

    # TODO patient is tied to appointment, so we probably don't need this
    # patient_id: TypeIDType = Patient.foreign_key()
    # patient: Patient = Relationship()

    doctor_intake_schema_id: TypeIDType = DoctorIntakeSchema.foreign_key()
    doctor_intake_schema: DoctorIntakeSchema = Relationship()

    duration: int | None = None
    "total length of the transcript, in seconds"

    intake_questions: list[IntakeQuestion] = Field(sa_type=JSONB)
    "list of questions extracted from the intake schema when this intake URL was generated"

    intake_questions_answers: list[IntakeAnswer] = Field(sa_type=JSONB, default={})
    "list of answers to the intake questions"

    intake_followup_questions: list[IntakeQuestion] = Field(sa_type=JSONB, default={})
    "after the initial intake questions are answered, the LLM is asked to generate followup questions, this is the structured response"

    intake_followup_questions_answers: list[IntakeAnswer] = Field(
        sa_type=JSONB, default=[]
    )
    "answers to the followup questions"

    def public_url(self) -> HttpUrl:
        return HttpUrl("https://intake.heydoctor.com/intake/" + str(self.id))
