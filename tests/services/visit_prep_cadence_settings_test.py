"""Owner-approved standard settings; no patient data or live environment writes."""

import inspect
from dataclasses import FrozenInstanceError, replace

import pytest

import app.configuration.visit_prep_cadence as configuration
from app.configuration.visit_prep import load_visit_prep_settings
from app.configuration.visit_prep_cadence import (
    DEFAULT_VISIT_PREP_CADENCE,
    VisitPrepCadenceSettings,
    current_standard_cadence,
    load_visit_prep_cadence_settings,
)
from app.services.visit_prep_email_worker import (
    claim_standard_email_invitation,
    dispatch_standard_email_claim,
    run_one_standard_email,
)
from app.services.visit_prep_timed_email import enqueue_locked_standard_email


def _values(**changes):
    return {
        "VISIT_PREP_STANDARD_CADENCE_VERSION": "standard-20261002",
        "VISIT_PREP_STANDARD_QUIET_START_HOUR": "21",
        "VISIT_PREP_STANDARD_QUIET_END_HOUR": "8",
        "VISIT_PREP_STANDARD_CHANNEL_SPACING_HOURS": "4",
    } | changes


def test_absent_configuration_uses_only_exact_owner_approved_standard():
    standard = load_visit_prep_cadence_settings({})
    assert standard == DEFAULT_VISIT_PREP_CADENCE
    assert standard is not None
    assert standard.quiet_hours == (21, 8)
    assert standard.minimum_channel_spacing.total_seconds() == 4 * 60 * 60
    assert load_visit_prep_settings({}).email_send_enabled is False
    assert load_visit_prep_settings({}).sms_send_enabled is False
    with pytest.raises(FrozenInstanceError):
        setattr(standard, "quiet_start_hour", 0)


@pytest.mark.parametrize("missing", list(_values()))
def test_partial_override_is_unavailable_not_a_default_fallback(missing):
    values = _values()
    del values[missing]
    assert load_visit_prep_cadence_settings(values) is None


@pytest.mark.parametrize(
    "key,value",
    [
        ("CADENCE_VERSION", ""),
        ("CADENCE_VERSION", "Private Patient"),
        ("CADENCE_VERSION", "A" * 65),
        ("QUIET_START_HOUR", "24"),
        ("QUIET_START_HOUR", "08"),
        ("QUIET_START_HOUR", " 21"),
        ("QUIET_START_HOUR", True),
        ("QUIET_END_HOUR", "21"),
        ("QUIET_END_HOUR", "-1"),
        ("CHANNEL_SPACING_HOURS", "0"),
        ("CHANNEL_SPACING_HOURS", "49"),
        ("CHANNEL_SPACING_HOURS", "4.0"),
        ("CHANNEL_SPACING_HOURS", 4),
    ],
)
def test_malformed_override_never_selects_a_permissive_standard(key, value):
    assert (
        load_visit_prep_cadence_settings(
            _values(**{f"VISIT_PREP_STANDARD_{key}": value})
        )
        is None
    )


def test_valid_central_override_and_fingerprint_bind_all_rules_and_version():
    standard = load_visit_prep_cadence_settings(_values())
    assert standard == DEFAULT_VISIT_PREP_CADENCE
    assert standard is not None
    fingerprints = {standard.fingerprint}
    for change in (
        {"version": "standard-next"},
        {"quiet_start_hour": 20},
        {"quiet_end_hour": 9},
        {"minimum_channel_spacing_hours": 5},
    ):
        fingerprints.add(replace(standard, **change).fingerprint)
    assert len(fingerprints) == 5
    assert all(len(value) == 64 for value in fingerprints)


def test_core_configuration_reader_uses_same_source_without_mutating_environment(
    monkeypatch,
):
    reads = []
    values = _values()

    def configured(name, *, default):
        reads.append(name)
        return values.get(name, default)

    monkeypatch.setattr(configuration, "config", configured)
    assert load_visit_prep_cadence_settings() == DEFAULT_VISIT_PREP_CADENCE
    assert set(reads) == set(values)


def test_current_standard_rejects_wrong_types_and_mutated_invalid_policy(monkeypatch):
    monkeypatch.setattr(configuration, "VISIT_PREP_CADENCE_SETTINGS", {})
    assert current_standard_cadence() is None
    invalid = VisitPrepCadenceSettings("synthetic", 21, 8, 4)
    object.__setattr__(invalid, "quiet_start_hour", True)
    monkeypatch.setattr(configuration, "VISIT_PREP_CADENCE_SETTINGS", invalid)
    assert current_standard_cadence() is None


def test_standard_entrypoints_cannot_accept_provider_timing_overrides():
    for operation in (
        claim_standard_email_invitation,
        dispatch_standard_email_claim,
        run_one_standard_email,
        enqueue_locked_standard_email,
    ):
        names = inspect.signature(operation).parameters
        assert "quiet_hours" not in names and "minimum_channel_spacing" not in names
