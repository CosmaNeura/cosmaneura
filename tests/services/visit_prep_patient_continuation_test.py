"""Invented cryptographic remembered-confirmation envelopes; no live identities."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.services.visit_prep_patient_continuation import (
    ConfirmedVisitPrepAccess,
    PatientContinuationInvalid,
    open_confirmation,
    seal_confirmation,
    valid_confirmation_name,
)
from app.utils.encryption import decrypt_value, encrypt_value


def _access():
    now = datetime.now(UTC)
    return ConfirmedVisitPrepAccess("a" * 64, "patient", now, now + timedelta(days=1))


def test_cookie_is_purpose_bound_encrypted_and_randomized():
    access = _access()
    cookies = [seal_confirmation(access) for _ in range(2)]
    assert cookies[0] != cookies[1]
    for cookie in cookies:
        assert open_confirmation(cookie) == access
        assert access.token_hash not in cookie
        assert "patient" not in cookie
        assert access.token_hash not in repr(access)
        payload = json.loads(decrypt_value(cookie))
        assert set(payload) == {
            "purpose",
            "token_hash",
            "actor_role",
            "confirmed_at",
            "expires_at",
            "nonce",
        }


@pytest.mark.parametrize(
    "change",
    [
        {"purpose": "another-service"},
        {"nonce": "bad"},
        {"extra": "data"},
        {"token_hash": "bad"},
        {"actor_role": "provider"},
        {"confirmed_at": "2026-01-01T00:00:00"},
        {"expires_at": 1},
        {"expires_at": "2000-01-01T00:00:00+00:00"},
    ],
)
def test_signed_but_wrong_shape_or_purpose_does_not_authorize(change):
    payload = json.loads(decrypt_value(seal_confirmation(_access())))
    cookie = encrypt_value(json.dumps(payload | change))
    with pytest.raises(
        PatientContinuationInvalid, match="^patient_continuation_invalid$"
    ):
        open_confirmation(cookie)


@pytest.mark.parametrize("cookie", ["", "x" * 2049, "garbled"])
def test_invalid_cookie_errors_do_not_include_credentials(cookie):
    with pytest.raises(PatientContinuationInvalid) as error:
        open_confirmation(cookie)
    assert str(error.value) == "patient_continuation_invalid"
    assert error.value.__cause__ is None


def test_naive_or_reversed_dates_cannot_be_issued():
    access = _access()
    for changed in [
        replace(access, confirmed_at=datetime.now()),
        replace(access, expires_at=access.confirmed_at),
    ]:
        with pytest.raises(PatientContinuationInvalid):
            seal_confirmation(changed)


def test_empty_name_marker_is_not_a_raw_bearer_confirmation():
    assert valid_confirmation_name("", _access())
    assert not valid_confirmation_name("", "raw-bearer")
    assert valid_confirmation_name("José", "raw-bearer")
    assert not valid_confirmation_name("x" * 121, _access())
