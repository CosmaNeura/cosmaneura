"""Pydantic models defining the input/output contract (v2, communication roles).

These are the shapes the FastAPI service accepts and returns, and the shape the
React dashboard panel consumes.

Privacy note: no field for direct identifiers. The engine assumes text arrives
already de-identified with consent handled upstream by the Cosmas application.
``patient_ref`` is an opaque, non-PHI handle (e.g. an internal encounter id).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


class ConfidenceBand(str, Enum):
    low = "Low"
    moderate = "Moderate"
    high = "High"


# --- Request --------------------------------------------------------------------
class ScoreRequest(BaseModel):
    """Input to the engine. At least one of intake_text / visit_text is required.

    intake_text -> the patient's own pre-visit words (e.g. the free-text intake
    question). Scored as the patient's *preferred* communication role.
    visit_text  -> the visit transcript. Scored (recalibrated) as the *enacted*
    communication role. When both are present the engine also returns the
    preference-enactment gap.
    """

    patient_ref: Optional[str] = Field(
        default=None,
        description="Opaque, non-PHI reference (e.g. encounter id). Never a name/MRN.",
        max_length=128,
    )
    intake_text: Optional[str] = Field(
        default=None,
        description="De-identified intake free-text (the patient's own words). -> preferred role.",
    )
    visit_text: Optional[str] = Field(
        default=None,
        description="De-identified visit transcript. -> enacted role.",
    )

    @model_validator(mode="after")
    def _require_some_text(self) -> "ScoreRequest":
        has_intake = bool(self.intake_text and self.intake_text.strip())
        has_visit = bool(self.visit_text and self.visit_text.strip())
        if not (has_intake or has_visit):
            raise ValueError(
                "Provide at least one of 'intake_text' or 'visit_text' with content."
            )
        return self


# --- Response components --------------------------------------------------------
class IndicatorScore(BaseModel):
    indicator: str
    score: float = Field(
        ge=0, le=100, description="0-100, where 50 is typical/neutral."
    )
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(default="")


class CommunicationIndicators(BaseModel):
    decision_control: IndicatorScore
    information_engagement: IndicatorScore
    expressed_concern: IndicatorScore

    def as_axis_dict(self) -> Dict[str, float]:
        return {
            "decision_control": self.decision_control.score,
            "information_engagement": self.information_engagement.score,
        }


class RoleDistributionItem(BaseModel):
    role_key: str
    role_name: str
    probability: float = Field(ge=0, le=1)


class RolePrediction(BaseModel):
    """A communication-role read for one channel (preferred or enacted)."""

    channel: str = Field(
        description="'preferred' (from intake) or 'enacted' (from visit)."
    )
    role_key: str
    role_name: str
    cps_role: str = Field(
        description="Control Preferences Scale lineage: active|collaborative|passive."
    )
    short_label: str
    summary: str
    confidence: float = Field(ge=0, le=1)
    confidence_band: ConfidenceBand
    runner_up_key: str
    runner_up_name: str
    distribution: List[RoleDistributionItem]
    indicators: CommunicationIndicators
    provider_guidance: List[str]
    cautions: List[str]
    concern_flag: bool = Field(
        description="True if expressed worry/anxiety is high enough to flag for reassurance."
    )
    baseline_source: str = Field(
        default="neutral_default",
        description="Reference population used for recalibration (R3).",
    )


class PreferenceEnactmentGap(BaseModel):
    """The gap between the role a patient prefers (intake) and enacts (visit)."""

    preferred_role_key: str
    preferred_role_name: str
    enacted_role_key: str
    enacted_role_name: str
    decision_control_gap: float = Field(
        description="enacted minus preferred on the decision-control axis (-100..100)."
    )
    direction: str = Field(
        description="'suppressed' (wants more involvement than enacted), "
        "'over_enacted' (took more control than preferred), or 'concordant'."
    )
    magnitude_band: ConfidenceBand
    interpretation: str


class EvidenceSufficiency(BaseModel):
    band: ConfidenceBand
    score: float = Field(ge=0, le=1)
    word_count: int
    sources_used: List[str]
    note: str


class ScoreResult(BaseModel):
    """Full result returned by the engine / API and consumed by the dashboard."""

    patient_ref: Optional[str] = None
    # Headline role: enacted when a visit transcript is present, else preferred.
    role: RolePrediction
    preferred_role: Optional[RolePrediction] = None
    enacted_role: Optional[RolePrediction] = None
    gap: Optional[PreferenceEnactmentGap] = None

    evidence_sufficiency: EvidenceSufficiency
    indicator_confidence_mean: float = Field(ge=0, le=1)

    inputs_used: List[str] = Field(description="Subset of ['intake', 'visit'].")
    view: str = Field(
        description="preferred_only | enacted_only | preferred_and_enacted.",
    )

    model: str = Field(description="LLM model string used, or 'heuristic-offline'.")
    engine_version: str
    framework_version: str
    rubric_version: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    disclaimer: str = Field(
        default=(
            "Decision-support communication aid only. This is an estimate of how the "
            "patient prefers to communicate and decide, from limited text -- not a "
            "personality assessment, diagnosis, or clinical judgement. Use it to tailor "
            "how you communicate, not what care you provide."
        )
    )


class HealthResponse(BaseModel):
    status: str
    mode: str
    engine_version: str
    framework_version: str
    rubric_version: str
    model: Optional[str] = None


def confidence_band(value: float) -> ConfidenceBand:
    """Map a 0-1 confidence to a Low/Moderate/High band."""
    if value >= 0.66:
        return ConfidenceBand.high
    if value >= 0.40:
        return ConfidenceBand.moderate
    return ConfidenceBand.low
