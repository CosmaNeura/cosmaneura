"""Cosmas Engagement Engine (v2).

Estimates a patient's *communication style* -- the role they prefer in clinical
decision-making, grounded in the Control Preferences Scale -- from de-identified
intake and/or visit text, to help clinicians tailor how they communicate.
Decision-support communication aid only; not a diagnostic or personality instrument.
"""

from .calibration.baseline import CPS_POPULATION_NORMS, RoleBaseline, default_baseline
from .engine import EngagementEngine, score_text
from .llm_client import (
    AnthropicLLMClient,
    HeuristicLLMClient,
    LLMClient,
    build_default_client,
)
from .roles import ROLES, list_roles, map_indicators_to_role
from .schemas import (
    CommunicationIndicators,
    PreferenceEnactmentGap,
    RolePrediction,
    ScoreRequest,
    ScoreResult,
)
from .version import ENGINE_VERSION, FRAMEWORK_VERSION, RUBRIC_VERSION

__version__ = ENGINE_VERSION

__all__ = [
    "EngagementEngine",
    "score_text",
    "LLMClient",
    "AnthropicLLMClient",
    "HeuristicLLMClient",
    "build_default_client",
    "ScoreRequest",
    "ScoreResult",
    "RolePrediction",
    "CommunicationIndicators",
    "PreferenceEnactmentGap",
    "ROLES",
    "list_roles",
    "map_indicators_to_role",
    "RoleBaseline",
    "default_baseline",
    "CPS_POPULATION_NORMS",
    "ENGINE_VERSION",
    "FRAMEWORK_VERSION",
    "RUBRIC_VERSION",
    "__version__",
]
