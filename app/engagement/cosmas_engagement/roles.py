"""Communication-style roles + the transparent indicator -> role mapping.

This module is the single source of truth for the patient *communication style*,
replacing the v1 Big-Five personality segmentation. It is grounded in the
**Control Preferences Scale (CPS)** of Degner & Sloan, the most widely used,
validated measure of the role a patient prefers in clinical decision-making. The
CPS's five roles collapse to three -- *active*, *collaborative*, *passive* -- and
those three are the output of this engine, renamed for the product as
**Self-Directed**, **Collaborative**, and **Directed**.

Why this rather than personality traits: the prior analysis showed the five
engagement styles separated mainly on Agreeableness/Conscientiousness/Neuroticism,
which are the *least observable* traits from outside (Vazire's SOKA model). A
communication-role model instead keys on **observable verbal behaviour** -- how
much decision control the patient seeks and how much information they engage with
-- which is what a transcript or an intake free-text answer can actually carry.

Axes used for mapping (each 0-100):
  decision_control      0 = defers to clinician  ...  100 = wants to lead the decision
  information_engagement 0 = wants minimal detail ...  100 = seeks detail/options/rationale
A third estimated indicator, ``expressed_concern`` (worry/anxiety), is carried as a
tone flag and is NOT used to assign the role.

References (see README for full list):
- Degner & Sloan, Control Preferences Scale; pooled norms ~26% active / 49%
  collaborative / 25% passive (Singh et al.).
- Vazire (2010), Self-Other Knowledge Asymmetry model (trait observability).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from .version import FRAMEWORK_VERSION

# Axes used to place a patient onto a role.
ROLE_AXES: Tuple[str, ...] = ("decision_control", "information_engagement")
# All indicators the model estimates (the third is a tone flag, not a mapping axis).
INDICATOR_ORDER: Tuple[str, ...] = (
    "decision_control",
    "information_engagement",
    "expressed_concern",
)

# Softmax temperature for turning prototype distances into a distribution. With only
# three roles spread along a largely 1-D axis, separation is cleaner than the v1
# five-way model -> higher, better-calibrated confidence.
_SOFTMAX_TAU = 18.0


@dataclass(frozen=True)
class CommunicationRole:
    """A single patient communication / decision-role style."""

    key: str
    display_name: str
    cps_role: str  # lineage: the Control Preferences Scale role this maps to
    short_label: str
    summary: str
    # Prototype on (decision_control, information_engagement), 0-100.
    prototype: Tuple[float, float]
    provider_guidance: List[str] = field(default_factory=list)
    cautions: List[str] = field(default_factory=list)


# display_name is the ONLY thing to change to re-label a role.
ROLES: Dict[str, CommunicationRole] = {
    "directed": CommunicationRole(
        key="directed",
        display_name="Directed",
        cps_role="passive",
        short_label="Wants the clinician to lead and decide",
        summary=(
            "Prefers the clinician to take the lead, give a clear recommendation, "
            "and decide. Seeks reassurance and direction rather than a menu of "
            "options. Lower demand for detail."
        ),
        prototype=(20.0, 30.0),
        provider_guidance=[
            "State the recommendation plainly and say exactly what to do next.",
            "Reassure and check understanding rather than asking them to choose.",
            "Keep instructions simple and sequential; avoid option overload.",
        ],
        cautions=[
            "Presenting many options without a recommendation can create anxiety.",
            "Deference is not the same as agreement -- confirm understanding.",
        ],
    ),
    "collaborative": CommunicationRole(
        key="collaborative",
        display_name="Collaborative",
        cps_role="collaborative",
        short_label="Wants to decide together",
        summary=(
            "Wants to share the decision with the clinician -- to hear the "
            "reasoning and options and weigh in, with responsibility shared. The "
            "modal preference in Control Preferences Scale data (~half of patients)."
        ),
        prototype=(50.0, 55.0),
        provider_guidance=[
            "Lay out the main options with plain trade-offs and invite their view.",
            "Make a recommendation, but frame it as a shared decision.",
            "Explain the 'why'; check what matters most to them.",
        ],
        cautions=[
            "Purely directive delivery can feel dismissive to this style.",
        ],
    ),
    "self_directed": CommunicationRole(
        key="self_directed",
        display_name="Self-Directed",
        cps_role="active",
        short_label="Wants full information and to lead the decision",
        summary=(
            "Wants the information, the evidence, and the options, and prefers to "
            "make the final call themselves. Values autonomy and rationale; may "
            "push back on a recommendation offered without explanation."
        ),
        prototype=(80.0, 75.0),
        provider_guidance=[
            "Give the evidence and the mechanism; invite questions.",
            "Offer options with trade-offs and support their choice.",
            "Provide reputable materials they can review independently.",
        ],
        cautions=[
            "A 'because I said so' tone tends to erode trust with this style.",
        ],
    ),
}


def list_roles() -> List[CommunicationRole]:
    """Return all roles in a stable order (most -> least patient-led)."""
    order = ["self_directed", "collaborative", "directed"]
    return [ROLES[k] for k in order]


def _euclidean(a: Tuple[float, ...], b: Tuple[float, ...]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _softmax(neg_logits: List[float]) -> List[float]:
    m = max(neg_logits)
    exps = [math.exp(v - m) for v in neg_logits]
    s = sum(exps)
    return [e / s for e in exps]


@dataclass
class RoleMatch:
    """Result of mapping communication indicators onto a role."""

    role_key: str
    role_name: str
    cps_role: str
    separation_probability: float
    distribution: Dict[str, float]
    runner_up_key: str
    runner_up_name: str
    # The (possibly baseline-standardized) axis values the mapping used.
    axes_used: Dict[str, float]


def map_indicators_to_role(indicators: Dict[str, float], baseline=None) -> RoleMatch:
    """Map estimated communication indicators onto the nearest role.

    Parameters
    ----------
    indicators:
        Mapping containing at least the keys in :data:`ROLE_AXES`, each 0-100.
    baseline:
        Optional object with ``standardize(axis, value) -> float`` used to express
        the patient's position *relative to a reference population* (R3
        recalibration). When ``None``, raw values are used as-is.

    Returns
    -------
    RoleMatch
        Best-matching role plus the full distribution, runner-up, and the axis
        values used (after any standardization).
    """
    missing = [a for a in ROLE_AXES if a not in indicators]
    if missing:
        raise ValueError(f"map_indicators_to_role missing axes: {missing}")

    if baseline is not None:
        axes = {
            a: float(baseline.standardize(a, float(indicators[a]))) for a in ROLE_AXES
        }
    else:
        axes = {a: float(indicators[a]) for a in ROLE_AXES}

    vec = tuple(axes[a] for a in ROLE_AXES)
    keys = list(ROLES.keys())
    distances = [_euclidean(vec, ROLES[k].prototype) for k in keys]
    probs = _softmax([-d / _SOFTMAX_TAU for d in distances])

    distribution = {k: round(p, 4) for k, p in zip(keys, probs)}
    ranked = sorted(zip(keys, probs), key=lambda kv: kv[1], reverse=True)
    top_key, top_p = ranked[0]
    runner_key, _ = ranked[1]

    return RoleMatch(
        role_key=top_key,
        role_name=ROLES[top_key].display_name,
        cps_role=ROLES[top_key].cps_role,
        separation_probability=round(top_p, 4),
        distribution=distribution,
        runner_up_key=runner_key,
        runner_up_name=ROLES[runner_key].display_name,
        axes_used={a: round(axes[a], 1) for a in ROLE_AXES},
    )


__all__ = [
    "ROLE_AXES",
    "INDICATOR_ORDER",
    "CommunicationRole",
    "ROLES",
    "FRAMEWORK_VERSION",
    "RoleMatch",
    "list_roles",
    "map_indicators_to_role",
]
