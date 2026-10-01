"""Public command input boundaries, without external service calls."""

import hashlib
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.visit_prep_command_contract import (
    RecipientInput,
    VisitPrepValidation,
    _canonical_uuid,
    _new_access_token,
    _normalize_recipient,
    _normalized_schedule,
    access_token_hash,
)

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def test_normalization_is_bounded_and_sensitive_input_is_not_represented():
    recipient = RecipientInput(
        first_name="  Jose\u0301  ",
        role="caregiver",
        email="SYNTHETIC@EXAMPLE.COM",
        phone=" +12025550123 ",
    )
    normalized = _normalize_recipient(recipient)
    assert normalized.first_name == "Jos\u00e9"
    assert normalized.role == "caregiver"
    assert normalized.email == "synthetic@example.com"
    assert normalized.phone == "+12025550123"
    for representation in (repr(recipient), repr(normalized)):
        assert "EXAMPLE" not in representation and "synthetic@" not in representation
        assert "+12025550123" not in representation
        assert "Jos" not in representation


@pytest.mark.parametrize("name", ["", " ", "x" * 121, "line\nname", "\ud800"])
def test_invalid_first_names_have_only_safe_errors(name):
    with pytest.raises(VisitPrepValidation, match="recipient first name is invalid"):
        _normalize_recipient(
            RecipientInput(first_name=name, email="synthetic@example.com")
        )


@pytest.mark.parametrize(
    "contact",
    [
        {},
        {"email": "invalid"},
        {"phone": "2025550123"},
        {"phone": "+0123456789"},
        {"email": 42},
        {"phone": 42},
        {"email": " ", "phone": " "},
    ],
)
def test_contact_requires_a_valid_email_or_e164_number(contact):
    with pytest.raises(VisitPrepValidation):
        _normalize_recipient(RecipientInput(first_name="Synthetic", **contact))


@pytest.mark.parametrize(
    "invalid",
    [
        "",
        "vp1_",
        "vp1_" + "A" * 42,
        "vp1_" + "A" * 44,
        "vp1_" + "!" * 43,
        "vp1_" + "\u2603" * 43,
        "vp1_" + "A" * 42 + "B",  # non-canonical unused base64 bits
    ],
)
def test_invalid_or_noncanonical_bearers_are_rejected(invalid):
    with pytest.raises(VisitPrepValidation, match="credential is invalid"):
        access_token_hash(invalid)


def test_bearers_have_256_random_bits_and_domain_separated_verifiers():
    tokens = {_new_access_token() for _ in range(20)}
    assert len(tokens) == 20
    for token in tokens:
        assert len(token) == 47 and token.startswith("vp1_")
        verifier = access_token_hash(token)
        assert verifier == access_token_hash(token)
        assert verifier != hashlib.sha256(token.encode()).hexdigest()
        assert len(verifier) == 64


@pytest.mark.parametrize(
    "key",
    [
        "bad",
        "123E4567-E89B-12D3-A456-426614174000",
        "123e4567e89b12d3a456426614174000",
        42,
    ],
)
def test_idempotency_key_requires_canonical_uuid(key):
    with pytest.raises(VisitPrepValidation):
        _canonical_uuid(key)


def test_dst_folds_require_the_explicit_correct_offset_and_gaps_are_rejected():
    zone = ZoneInfo("America/New_York")
    for fold, expected_hour in ((0, 5), (1, 6)):
        at = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=fold)
        utc, name = _normalized_schedule(at, zone.key, now=NOW)
        assert utc.hour == expected_hour and name == zone.key
    gap = datetime(2027, 3, 14, 2, 30, tzinfo=zone)
    with pytest.raises(VisitPrepValidation, match="does not match timezone"):
        _normalized_schedule(gap, zone.key, now=NOW)


@pytest.mark.parametrize(
    ("when", "zone"),
    [
        (datetime(2026, 12, 1), "UTC"),
        (datetime(2026, 12, 1, tzinfo=UTC), "America/New_York"),
        (datetime(2026, 12, 1, tzinfo=UTC), "Not/A_Real_Zone"),
        (datetime(2026, 12, 1, tzinfo=UTC), "Etc/../UTC"),
        (datetime.max.replace(tzinfo=UTC), "UTC"),
        (NOW, "UTC"),
    ],
)
def test_invalid_timezone_past_naive_and_overflow_schedules_fail_closed(when, zone):
    with pytest.raises(VisitPrepValidation):
        _normalized_schedule(when, zone, now=NOW)
