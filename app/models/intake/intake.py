from typing import Literal

from furl import furl
from pydantic import BaseModel as PydanticBaseModel
from pydantic import HttpUrl, computed_field

from app.constants import INTAKE_CHAT_DOMAIN

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


class ExternalIntakeData(PydanticBaseModel):
    reason_for_visit: str
    "The patient's main reason for the visit. AKA chief complaint."

    appointment_type: str
    "Type of appointment"

    additional_info: str
    "Additional info relevant to the doctor's preparation"

    appointment_category: str | None = None
    "Category of appointment, if available"


class QuestionChoice(PydanticBaseModel):
    """One tap target on a `kind="choice"` question."""

    value: str
    "what gets stored as the answer when this choice is picked"

    label: str
    "what the patient reads and taps"

    hint: str | None = None
    "optional half-sentence under the label"


class QuestionCondition(PydanticBaseModel):
    """Ask a question only when an earlier answer is one of `any_of`.

    Anchored to another question's `key`, never to its order or its text: a
    physician can reorder and reword their own questions, and either would
    silently break a condition pinned to those.

    One operator on purpose. "Show when the answer is one of these" expresses
    every condition a choice question can need, and having a single operator
    means there is no precedence to get wrong. Conditions on free-text
    questions are deliberately not expressible -- gating on prose is a bug
    waiting to happen.
    """

    key: str
    "the `key` of the question this one depends on"

    any_of: list[str]
    "ask this question when the other answer is one of these values"

    def satisfied_by(self, answers: dict[str, str]) -> bool:
        value = answers.get(self.key)
        # A skipped gate leaves an empty answer. Treat that as "we do not know
        # there is a problem" and do not ask the follow-up.
        if not value:
            return False
        return value in self.any_of


class IntakeSchemaQuestion(PydanticBaseModel):
    order: int
    "order that the question should be asked in"

    question: str
    "question to ask the patient"

    # TODO not currently in use, but could be used in the future for follow-up questions
    required_details: str = ""
    "information that we expect to be included as a full answer to the question"

    answer: str | None = None
    "the user's answer to the question"

    answer_timestamp: int | None = None
    "the timestamp when the question was answered"

    # --- physician-authored question sets (Visit Prep, CosmaNeura#135) ------
    # All optional: model-generated intakes predate these and must stay valid.

    key: str | None = None
    """Stable identifier for a physician-authored question.

    Model-generated questions have none. Downstream logic (conditions, and the
    brief's adherence reasoning) keys off this rather than the question text,
    which the physician can edit at will.
    """

    helper: str | None = None
    "a line or two under the question, in the patient's voice"

    kind: Literal["story", "list", "choice"] = "story"
    "how the patient answers: prose, a list, or tap targets"

    choices: list[QuestionChoice] | None = None
    "the tap targets; only meaningful when kind == 'choice'"

    show_if: QuestionCondition | None = None
    "ask this question only when the condition holds"

    @property
    def is_answered(self) -> bool:
        """Whether the patient has dealt with this question, skips included.

        Keyed off the timestamp rather than the answer text so that skipping
        can store an empty string and still advance -- `if not answer` would
        loop on it forever. Every existing row sets both fields together, so
        this is behavior-preserving for model-generated intakes.
        """
        return self.answer_timestamp is not None


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

    # patient is tied to the appointment, so we don't have a patient_id on this model
    appointment_id: TypeIDType = Appointment.foreign_key()
    appointment: Appointment = Relationship(back_populates="intake")

    external_intake_data: dict[str, str] = Field(sa_type=JSONB)
    "arbitrary blob of data from an external system representing any other intake data that has been collected"

    cleaned_external_intake_data: ExternalIntakeData | None = Field(
        nullable=True, default=None, sa_type=JSONB
    )
    "data from an external intake system that has been cleaned and normalized"

    intake_questions: list[IntakeSchemaQuestion] = Field(sa_type=JSONB)
    "list of questions extracted from the intake schema when this intake URL was generated"

    summary: str | None = Field(nullable=True, default=None)
    "AI generated summary of all intake information"

    def answers_by_key(self) -> dict[str, str]:
        "Answers to keyed questions, for evaluating conditions."
        return {
            question.key: question.answer
            for question in self.intake_questions
            if question.key and question.answer is not None
        }

    def visible_questions(self) -> list[IntakeSchemaQuestion]:
        """The questions this patient should actually be asked, in order.

        A conditional question whose gate is unanswered, skipped, or does not
        match is not asked -- and must not count toward completion either, or a
        prep whose patient takes everything as prescribed would never finish.

        Model-generated intakes carry no conditions, so every question is
        visible and this matches the previous behavior exactly.
        """
        answers = self.answers_by_key()
        visible = [
            question
            for question in self.intake_questions
            if question.show_if is None or question.show_if.satisfied_by(answers)
        ]
        return sorted(visible, key=lambda question: question.order)

    def unanswered_questions(self) -> list[IntakeSchemaQuestion]:
        return [q for q in self.visible_questions() if not q.is_answered]

    def next_unanswered_question(self) -> IntakeSchemaQuestion | None:
        """
        Get the next unanswered question, if any.
        """
        return next(iter(self.unanswered_questions()), None)

    def is_processing(self) -> bool:
        """
        Is AI still determining what questions to ask?
        """
        return len(self.intake_questions) == 0

    def is_complete(self) -> bool:
        """
        Check if the intake process is complete. An intake is considered complete if all questions have been answered.
        """
        return not self.is_processing() and len(self.unanswered_questions()) == 0

    @computed_field
    @property
    def public_url(self) -> HttpUrl:
        chat_url = furl().set(
            scheme="https",
            host=INTAKE_CHAT_DOMAIN,
            # TODO is there a way to auto-gen this in some way?
            path="chat/intake/" + str(self.id),
            # args={"some": "args"},
            # fragment="great job",
        )
        return HttpUrl(str(chat_url))
