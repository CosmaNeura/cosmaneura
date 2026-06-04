from enum import Enum

from activemodel import BaseModel
from activemodel.mixins import SoftDeletionMixin, TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship

from .ai_visit_transcript import AIVisitTranscript
from .appointment import Appointment
from .doctor import Doctor


class ConfidenceBand(str, Enum):
    low = "Low"
    moderate = "Moderate"
    high = "High"


class EngagementRole(
    BaseModel,
    TimestampsMixin,
    SoftDeletionMixin,
    TypeIDMixin("engagement_role"),
    table=True,
):
    """A communication-role estimate for one visit transcript (engagement engine v2).

    Reads the patient's *enacted* communication style from the visit transcript and
    maps it to a Control Preferences Scale role (Self-Directed / Collaborative /
    Directed). This is a decision-support communication aid, NOT a diagnosis or a
    personality assessment. One row per scored transcript.
    """

    appointment_id: TypeIDType = Appointment.foreign_key()
    appointment: Appointment = Relationship()

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    ai_transcript_id: TypeIDType = AIVisitTranscript.foreign_key(unique=True)
    ai_transcript: AIVisitTranscript = Relationship()

    # Headline role
    role_key: str
    role_name: str
    cps_role: str
    "Control Preferences Scale lineage: active | collaborative | passive."

    confidence: float
    confidence_band: ConfidenceBand
    channel: str = Field(default="enacted")
    "Which read produced this: enacted (visit) or preferred (intake)."

    # Observable indicators (0-100) + the rater's confidence in each (0-1)
    decision_control: float
    information_engagement: float
    expressed_concern: float
    decision_control_confidence: float
    information_engagement_confidence: float
    concern_flag: bool = Field(default=False)

    distribution: dict[str, float] = Field(sa_type=JSONB)
    "role_key -> probability across all roles."

    # Preferred read (from the intake free-text), when intake text is available.
    preferred_role_key: str | None = Field(default=None)
    preferred_role_name: str | None = Field(default=None)
    preferred_confidence: float | None = Field(default=None)

    # Preference-enactment gap, when BOTH intake and visit text are present.
    gap_direction: str | None = Field(default=None)
    "suppressed | over_enacted | concordant."
    gap_decision_control: float | None = Field(default=None)
    "enacted minus preferred on the decision-control axis."
    gap_magnitude_band: str | None = Field(default=None)

    # Provenance / audit
    baseline_source: str = Field(default="neutral_default")
    model: str
    engine_version: str
    framework_version: str
    rubric_version: str
