from typing import Literal

from pydantic import BaseModel as PydanticBaseModel

from activemodel import BaseModel
from activemodel.mixins import (
    PydanticJSONMixin,
    SoftDeletionMixin,
    TimestampsMixin,
    TypeIDMixin,
)
from activemodel.types import TypeIDType
from app.models.intake.intake import Intake
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship

from ..doctor import Doctor

FlagKind = Literal["clinical", "adherence", "story", "billing"]


class BriefFlag(PydanticBaseModel):
    """Something in the answers the physician should not walk past.

    `billing` is deliberately last and deliberately speculative. It exists
    because a documented screen is sometimes both clinically indicated and
    billable -- it is never the reason to ask, and #135 is explicit that it is
    never the pitch.
    """

    kind: FlagKind
    label: str
    "three to five words"

    detail: str
    "one sentence"


class VisitBrief(PydanticBaseModel):
    """The 45-second read a physician gets before the visit.

    Structured rather than prose so the dashboard can lay it out, flags can be
    counted across a practice, and the shape stays stable while the wording
    behind it changes.
    """

    headline: str
    "one sentence, specific, no throat-clearing"

    bullets: list[str]
    "the facts not to miss"

    decision_points: list[str]
    "what to decide in the room"

    flags: list[BriefFlag] = []

    narrative: str
    "one tight paragraph"


class IntakeSummary(
    BaseModel,
    PydanticJSONMixin,
    TimestampsMixin,
    SoftDeletionMixin,
    TypeIDMixin("intake_summary"),
    table=True,
):
    """
    After an intake is complete, we generate a summary of the answers for the doctor's review.
    """

    doctor_id: TypeIDType = Doctor.foreign_key()
    doctor: Doctor = Relationship()

    intake_id: TypeIDType = Intake.foreign_key()
    intake: Intake = Relationship()

    summary: VisitBrief = Field(sa_type=JSONB)
    """The structured brief.

    Widened from a flat `dict[str, str]`: the brief carries lists of bullets,
    decisions and typed flags. Safe to retype rather than migrate -- the column
    is JSONB either way, and nothing has ever written this table.
    """
