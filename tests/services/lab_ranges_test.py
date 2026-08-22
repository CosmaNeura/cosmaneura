from app.services.lab_ranges import (
    DEFAULT_RANGES,
    NO_RANGE,
    OPTIMAL,
    OUTSIDE_STANDARD,
    OUTSIDE_STANDARD_WITHIN_OPTIMAL,
    WITHIN_STANDARD,
    RangeSpec,
    classify,
    default_range,
)


def test_classify_optimal():
    spec = RangeSpec("ng/mL", 30, 100, 40, 60)
    assert classify(50, spec) == OPTIMAL


def test_classify_within_standard_but_outside_optimal():
    spec = RangeSpec("ng/mL", 30, 100, 40, 60)
    assert classify(35, spec) == WITHIN_STANDARD  # passes standard, below optimal
    assert classify(80, spec) == WITHIN_STANDARD  # passes standard, above optimal


def test_classify_outside_standard():
    spec = RangeSpec("ng/mL", 30, 100, 40, 60)
    assert classify(20, spec) == OUTSIDE_STANDARD
    assert classify(120, spec) == OUTSIDE_STANDARD


def test_lower_is_better_unbounded_low():
    # hs-CRP: standard <3.0, optimal <1.0
    spec = RangeSpec("mg/L", None, 3.0, None, 1.0)
    assert classify(0.5, spec) == OPTIMAL
    assert classify(2.0, spec) == WITHIN_STANDARD
    assert classify(5.0, spec) == OUTSIDE_STANDARD


def test_higher_is_better_unbounded_high():
    # HDL: standard >40, optimal >60
    spec = RangeSpec("mg/dL", 40, None, 60, None)
    assert classify(70, spec) == OPTIMAL
    assert classify(50, spec) == WITHIN_STANDARD
    assert classify(30, spec) == OUTSIDE_STANDARD


def test_boundaries_are_inclusive_of_the_range():
    spec = RangeSpec("ng/mL", 30, 100, 40, 60)
    assert classify(40, spec) == OPTIMAL  # exactly optimal_low
    assert classify(60, spec) == OPTIMAL  # exactly optimal_high
    assert classify(30, spec) == WITHIN_STANDARD  # exactly standard_low


def test_no_spec_is_no_range():
    assert classify(5, None) == NO_RANGE


def test_default_range_lookup():
    assert default_range("tsh") is not None
    assert default_range("not_a_marker") is None


def test_every_default_range_is_coherent():
    for spec in DEFAULT_RANGES.values():
        if spec.standard_low is not None and spec.standard_high is not None:
            assert spec.standard_low < spec.standard_high
        if spec.optimal_low is not None and spec.optimal_high is not None:
            assert spec.optimal_low < spec.optimal_high


def test_classify_outside_standard_but_within_optimal():
    """Both facts are reported rather than one winning.

    A functional optimal window may legitimately extend past the standard
    ceiling — magnesium is the platform default that does. Collapsing this to
    "outside standard" reads as plainly abnormal and hides that the value is
    exactly where the provider's own target wants it; collapsing it to "optimal"
    hides that it clears the standard interval.
    """

    spec = RangeSpec("mg/dL", 1.7, 2.2, 2.0, 2.4)
    assert classify(2.3, spec) == OUTSIDE_STANDARD_WITHIN_OPTIMAL
    # inside both windows is still plain optimal
    assert classify(2.1, spec) == OPTIMAL
    # outside both is still plain outside-standard
    assert classify(2.5, spec) == OUTSIDE_STANDARD
    assert classify(1.5, spec) == OUTSIDE_STANDARD
    # below standard but above optimal_low cannot happen here; check the low side
    assert classify(1.8, spec) == WITHIN_STANDARD


def test_nested_ranges_are_unaffected_by_the_dual_label():
    "the 14 biomarkers whose optimal window nests inside standard behave as before"

    spec = RangeSpec("ng/mL", 30, 100, 40, 60)
    assert classify(25, spec) == OUTSIDE_STANDARD
    assert classify(35, spec) == WITHIN_STANDARD
    assert classify(50, spec) == OPTIMAL
    assert classify(80, spec) == WITHIN_STANDARD
    assert classify(120, spec) == OUTSIDE_STANDARD


def test_magnesium_default_keeps_the_extended_optimal_window():
    "guards the deliberate choice — see the comment on the magnesium default"

    spec = DEFAULT_RANGES["magnesium"]
    assert spec.optimal_high is not None and spec.standard_high is not None
    assert spec.optimal_high > spec.standard_high
    assert classify(2.3, spec) == OUTSIDE_STANDARD_WITHIN_OPTIMAL
