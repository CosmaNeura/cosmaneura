from enum import Enum

from activemodel import BaseModel
from activemodel.mixins import SoftDeletionMixin, TimestampsMixin, TypeIDMixin
from activemodel.types import TypeIDType
from sqlmodel import Field, Relationship

from .appointment import Appointment
from .doctor import Doctor


class CpsRole(str, Enum):
    """Control Preferences Scale role (the collapsed 3-role taxonomy)."""

    active = "active"  # Self-Directed
    collaborative = "collaborative"  # Collaborative
    passive = "passive"  # Directed


class CpsSelfReport(
    BaseModel,
    TimestampsMixin,
    SoftDeletionMixin,
    TypeIDMixin("cps_self_report"),
    table=True,
):
    """A patient's self-reported control preference for one encounter — GROUND TRUTH.

    Collected via a short validated Control Preferences Scale item, this is what the
    engagement engine's *predicted* role is validated against (the validation gate).
    It is the ground truth the engine currently lacks; without it we can only say the
    engine is confident, not that it is correct.

    Stored per appointment (one self-report per encounter). Opt-in, internal, never
    patient-facing as a "score"; consent handled upstream.
    """

    appointment_id: TypeIDType = Appointment.foreign_key(unique=True)
    appointment: Appointment = Relationship()

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    preferred_cps_role: CpsRole
    "The role the patient says they prefer in clinical decision-making."

    instrument: str = Field(default="CPS-1item")
    "Which instrument/version produced this (for audit as the pilot evolves)."

    raw_response: str | None = Field(default=None)
    "Optional verbatim of the patient's selection/answer."
