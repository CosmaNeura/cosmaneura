from enum import Enum

from activemodel import BaseModel
from activemodel.mixins import TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship

from .appointment import Appointment
from .doctor import Doctor
from .patient import Patient


class SessionPrepStatus(str, Enum):
    pending = "pending"
    completed = "completed"
    failed = "failed"


class SessionPrep(
    BaseModel,
    TimestampsMixin,
    TypeIDMixin("session_prep"),
    table=True,
):
    """A short pre-session briefing for a provider, generated on demand.

    Built from the patient's session history (app/services/patient_context.py):
    prior clinical notes, the latest intake summary, and the communication-role
    read when present. Brevity is the core design constraint — the whole brief
    must be readable in well under a minute, so generation enforces 3-5 talking
    points and a hard word budget rather than a long narrative.

    Content is PHI: same handling as AIVisitNote, never logged.
    """

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    patient_id: TypeIDType = Patient.foreign_key()
    patient: Patient = Relationship()

    appointment_id: TypeIDType | None = Appointment.foreign_key(nullable=True)
    "the upcoming appointment this prep is for, when generated from the schedule view"

    status: SessionPrepStatus = Field(default=SessionPrepStatus.pending)

    talking_points: list[str] = Field(default_factory=list, sa_type=JSONB)
    "3-5 short talking points for the upcoming session"

    open_threads: list[str] = Field(default_factory=list, sa_type=JSONB)
    "unresolved items from the most recent session (plan items, assignments)"

    alerts: list[str] = Field(default_factory=list, sa_type=JSONB)
    "safety/risk mentions and medication changes surfaced from prior notes; rendered first"

    personality_note: str | None = Field(default=None)
    "one-line communication-style note from the engagement read, if available"

    days_since_last_session: int | None = Field(default=None)
    "computed from the most recent note's appointment, not by the LLM"

    visits_in_context: int = Field(default=0)
    "how many prior notes informed this brief"

    model: str | None = Field(default=None)
    "LLM used to generate the brief, for provenance"
