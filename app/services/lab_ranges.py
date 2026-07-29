"""
Dual-range lab interpretation: classify a biomarker value against a STANDARD
reference range and a tighter functional-medicine OPTIMAL range.

Two layers:
- Platform DEFAULTS (below): standard ranges are common medical-knowledge lab
  reference intervals; optimal ranges are conservative Cosmaneura defaults the
  provider is expected to tune to their own source (e.g. Lamkin / Fullscript).
  We do NOT copy any copyrighted range table — these are starting values, and
  the resource directory links out to the provider's chosen reference.
- Provider OVERRIDES live in the LabRangeSet table and win over the default
  for that doctor.

No PHI: biomarker keys, units, and numeric bounds only.

A bound of None means "unbounded on that side" (e.g. optimal_high=5.4 with
optimal_low=None means "lower is better, target under 5.4").
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RangeSpec:
    unit: str
    standard_low: float | None
    standard_high: float | None
    optimal_low: float | None
    optimal_high: float | None


# key -> default range. Standard = widely published reference intervals.
# Optimal = editable functional-medicine starting targets.
DEFAULT_RANGES: dict[str, RangeSpec] = {
    "vitamin_d_25oh": RangeSpec("ng/mL", 30, 100, 40, 60),
    "vitamin_b12": RangeSpec("pg/mL", 200, 900, 500, 900),
    "folate": RangeSpec("ng/mL", 3.0, None, 10, None),
    "ferritin": RangeSpec("ng/mL", 30, 400, 50, 150),
    "tsh": RangeSpec("mIU/L", 0.4, 4.0, 0.5, 2.5),
    "free_t3": RangeSpec("pg/mL", 2.3, 4.2, 3.0, 4.2),
    "free_t4": RangeSpec("ng/dL", 0.8, 1.8, 1.0, 1.8),
    "hba1c": RangeSpec("%", None, 5.7, None, 5.4),
    "magnesium": RangeSpec("mg/dL", 1.7, 2.2, 2.0, 2.4),
    "potassium": RangeSpec("mmol/L", 3.5, 5.1, 4.0, 4.5),
    "crp_hs": RangeSpec("mg/L", None, 3.0, None, 1.0),
    "homocysteine": RangeSpec("µmol/L", None, 15, None, 7),
    "ldl_c": RangeSpec("mg/dL", None, 130, None, 100),
    "hdl_c": RangeSpec("mg/dL", 40, None, 60, None),
    "triglycerides": RangeSpec("mg/dL", None, 150, None, 100),
}

# classification results
OPTIMAL = "optimal"  # within the optimal window
WITHIN_STANDARD = "within_standard"  # passes standard but outside optimal
OUTSIDE_STANDARD = "outside_standard"  # outside the standard reference range
NO_RANGE = "no_range"  # we have no range for this biomarker


def _below(value: float, low: float | None) -> bool:
    return low is not None and value < low


def _above(value: float, high: float | None) -> bool:
    return high is not None and value > high


def classify(value: float, spec: RangeSpec | None) -> str:
    if spec is None:
        return NO_RANGE

    if _below(value, spec.standard_low) or _above(value, spec.standard_high):
        return OUTSIDE_STANDARD

    if _below(value, spec.optimal_low) or _above(value, spec.optimal_high):
        return WITHIN_STANDARD

    return OPTIMAL


def default_range(biomarker_key: str) -> RangeSpec | None:
    return DEFAULT_RANGES.get(biomarker_key)
