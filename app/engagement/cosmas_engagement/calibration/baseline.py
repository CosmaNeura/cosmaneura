"""Recalibration baseline for the in-encounter role estimate (R3).

The problem (range restriction): a single practice's patients are a filtered
sample, so their communication behaviour is bunched into a narrow band. Scored on
an absolute scale, almost everyone lands in the same role with low separation.

The fix: express each patient's communication indicators *relative to the local
population* before mapping to a role. A ``RoleBaseline`` holds the per-axis mean
and standard deviation of the reference population; ``standardize`` converts a raw
0-100 indicator into a population-relative 0-100 value (z-scored, then rescaled to
a common reference SD). In production the mean/SD are the rolling per-practice
distribution; until enough data accrues, a neutral baseline (mean 50, SD 18) makes
relative == raw, so the engine degrades gracefully to absolute scoring.

The published Control Preferences Scale distribution is provided as a sensible
prior for the *role mix* a practice might expect, and for documentation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

from ..roles import ROLE_AXES

# Pooled CPS preference distribution (Singh et al., ~3,000 patients): a reference
# for the expected role mix. Not used to bias individual classification; provided
# for calibration dashboards and sanity checks.
CPS_POPULATION_NORMS: Dict[str, float] = {
    "self_directed": 0.26,  # CPS "active"
    "collaborative": 0.49,  # CPS "collaborative"
    "directed": 0.25,  # CPS "passive"
}

# Reference SD used when rescaling z-scores back onto the 0-100 axis.
_REFERENCE_SD = 18.0


@dataclass
class RoleBaseline:
    """Per-axis location/scale of a reference population for standardization."""

    axis_mean: Dict[str, float] = field(
        default_factory=lambda: {a: 50.0 for a in ROLE_AXES}
    )
    axis_sd: Dict[str, float] = field(
        default_factory=lambda: {a: _REFERENCE_SD for a in ROLE_AXES}
    )
    source: str = "neutral_default"

    def standardize(self, axis: str, value: float) -> float:
        """Map a raw 0-100 indicator to a population-relative 0-100 value."""
        mean = self.axis_mean.get(axis, 50.0)
        sd = self.axis_sd.get(axis, _REFERENCE_SD)
        if sd <= 1e-6:
            return value
        z = (value - mean) / sd
        rel = 50.0 + _REFERENCE_SD * z
        return max(0.0, min(100.0, rel))

    @classmethod
    def from_samples(cls, axis_values: Dict[str, list], source: str) -> "RoleBaseline":
        """Build a baseline from accumulated per-practice indicator samples."""
        import statistics

        mean, sd = {}, {}
        for a in ROLE_AXES:
            vals = [float(v) for v in axis_values.get(a, []) if v is not None]
            if len(vals) >= 2:
                mean[a] = statistics.fmean(vals)
                s = statistics.pstdev(vals)
                sd[a] = s if s > 1e-6 else _REFERENCE_SD
            else:
                mean[a] = 50.0
                sd[a] = _REFERENCE_SD
        return cls(axis_mean=mean, axis_sd=sd, source=source)


def default_baseline() -> RoleBaseline:
    """Neutral baseline (relative == raw). Replace with a per-practice baseline."""
    return RoleBaseline()


__all__ = ["CPS_POPULATION_NORMS", "RoleBaseline", "default_baseline"]
